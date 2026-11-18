#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
WeChat Export GUI Application
This application provides a graphical user interface for exporting WeChat records.
It integrates the three main steps from the examples folder:
1. Decrypt database
2. Connect to database and get contact information
3. Export records based on contacts
"""

import os
import sys
import json
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import scrolledtext
import traceback
import importlib.util
import subprocess
from PIL import Image, ImageTk
import io

import ui_kit

# Add the parent directory to the path to import the required modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def _log_gui_error(tag, extra=''):
    """把异常堆栈落盘，便于排查（GUI 日志面板里的信息不完整）"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gui_error.log')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} [{tag}] {extra}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


# Check for required packages
required_packages = [
    ('Crypto', 'pycryptodome', 'Crypto.Protocol.KDF'),
    ('google', 'google', 'google'),
    ('protobuf', 'protobuf', 'google.protobuf'),
    ('PIL', 'pillow', 'PIL'),
    ('tkcalendar', 'tkcalendar', 'tkcalendar'),
]

missing_packages = []
for package_name, pip_name, import_path in required_packages:
    try:
        # Try to import the module
        module_name = import_path.split('.')[0]
        if importlib.util.find_spec(module_name) is None:
            missing_packages.append((package_name, pip_name))
    except ImportError:
        missing_packages.append((package_name, pip_name))

# If there are missing packages, show a message and exit
if missing_packages:
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    message = "缺少以下必要的依赖包:\n\n"
    for package_name, pip_name in missing_packages:
        message += f"- {package_name} (安装命令: pip install {pip_name})\n"
    message += "\n请安装这些包后再运行程序。"

    messagebox.showerror("缺少依赖", message)

    # Ask if the user wants to install the packages automatically
    if messagebox.askyesno("自动安装", "是否要自动安装这些依赖包?"):
        try:
            for _, pip_name in missing_packages:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
            messagebox.showinfo("安装完成", "依赖包已安装完成，请重新启动程序。")
        except Exception as e:
            messagebox.showerror("安装失败", f"安装依赖包时出错: {str(e)}")

    sys.exit(1)

# Import required modules
from multiprocessing import freeze_support
from wxManager import Me, DatabaseConnection, MessageType
from wxManager.decrypt import get_info_v4, get_info_v3
from wxManager.decrypt.decrypt_dat import get_decode_code_v4
from wxManager.decrypt import decrypt_v4, decrypt_v3
from exporter.config import FileType
from exporter import (
    HtmlExporter, TxtExporter, AiTxtExporter,
    DocxExporter, MarkdownExporter, ExcelExporter
)

# Import weekly report module
try:
    from weekly_report_gui import WeeklyReportFrame
except ImportError:
    WeeklyReportFrame = None


class WeChatExportGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("微信记录导出工具")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win_w = min(1240, max(1000, sw - 80))
        win_h = min(820, max(660, sh - 140))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(min(1000, win_w), min(640, win_h))
        self.root.configure(bg=ui_kit.COLORS["bg"])

        # 主题与 ttk 样式
        self.C = ui_kit.COLORS
        self.F = ui_kit.FONTS
        ui_kit.setup_ttk_styles(root)

        # 配置
        try:
            import config
            self.config = config.load_config()
        except ImportError:
            self.config = {
                "db_dir": "",
                "db_version": 3,
                "output_dir": "./data/",
                "last_export_format": "HTML",
                "recent_contacts": [],
                "recent_databases": []
            }
            self.log_message_console("未找到配置模块，使用默认配置")

        # 运行状态
        self.database = None
        self.contacts = []
        self.filtered_contacts = []
        self.load_button = None
        self.test_button = None
        self._contact = None
        self._selected_row = None
        self._search_job = None

        # 头像流水线：唯一后台 worker + 主线程分帧应用
        self.contact_avatar_cache = {}
        self._avatar_gen = 0
        self._avatar_todo = queue.Queue()
        self._avatar_done = queue.Queue()
        self._avatar_worker_started = False
        self._avatar_pump_job = None
        self._avatar_pending = set()
        self._avatar_missing = set()
        self._placeholder_avatars = {}

        # 日志流水线：跨线程安全 + 合并刷新，避免每行都重绘
        self._log_queue = queue.Queue()
        self._log_job = None

        # 变量
        self.db_dir = tk.StringVar()
        self.db_version = tk.IntVar(value=self.config.get("db_version", 3))
        self.output_dir = tk.StringVar(value=self.config.get("output_dir", "./data/"))
        self.selected_wxid = tk.StringVar()
        self.search_text = tk.StringVar()
        self.search_text.trace("w", self.filter_contacts)
        if self.config.get("db_dir"):
            self.db_dir.set(self.config.get("db_dir"))

        # 页面骨架
        self._build_header()
        self.notebook = ui_kit.TabBar(root, on_change=self._on_tab_change)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.create_contacts_tab()
        self.create_settings_tab()
        if WeeklyReportFrame is not None:
            self.create_weekly_report_tab()

        self._build_status_bar()
        self._start_log_pump()

        self.root.after(80, lambda: ui_kit.apply_dark_titlebar(self.root))

        if self.config.get("db_dir") and os.path.exists(self.config.get("db_dir")):
            self.root.after(800, lambda: self.load_contacts(auto=True))

    def _build_header(self):
        """顶部品牌栏：渐变底 + 徽标 + 状态药丸"""
        C, F = self.C, self.F
        self._header = tk.Canvas(self.root, height=64, bg=C["bg"],
                                 highlightthickness=0, bd=0)
        self._header.pack(fill=tk.X)

        def draw(_event=None):
            c = self._header
            w = c.winfo_width() or 1240
            h = 64
            c.delete("all")
            # 底部一条细分隔线，取代顶部渐变条
            c.create_line(0, h - 1, w, h - 1, fill=C["border_soft"])

            x0 = 24
            # 简洁徽标：圆角方块 + 单字，不再用花哨渐变
            ui_kit.round_rect(c, x0, 18, x0 + 28, 46, 8, fill=C["accent"], outline="")
            c.create_text(x0 + 14, 32, text="微", fill="#FFFFFF", font=F["h2"])
            c.create_text(x0 + 40, 32, text="微信记录导出", fill=C["text"],
                          font=F["display"], anchor="w")

            connected = self.database is not None
            label = "● 已连接" if connected else "● 未连接"
            color = C["green"] if connected else C["amber"]
            bw = 88
            ui_kit.round_rect(c, w - 24 - bw, 20, w - 24, 44, 12,
                              fill=C["surface"], outline=C["border"])
            c.create_text(w - 24 - bw / 2, 32, text=label, fill=color, font=F["small"])

        self._header.bind("<Configure>", draw)
        self._header_refresh = draw

    def _build_status_bar(self):
        C, F = self.C, self.F
        bar = tk.Frame(self.root, bg=C["bg_soft"], height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self.status_var, bg=C["bg_soft"],
                 fg=C["muted"], font=F["tiny"]).pack(side=tk.LEFT, padx=18)
        self.status_right = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.status_right, bg=C["bg_soft"],
                 fg=C["faint"], font=F["tiny"]).pack(side=tk.RIGHT, padx=18)

    def _on_tab_change(self, frame=None):
        if getattr(self, "_weekly_tab", None) is frame:
            self._ensure_weekly_built()
        if getattr(self, "vlist", None) is not None:
            self.root.after_idle(self.vlist._schedule_render)

    def create_custom_styles(self):
        """沿用旧方法名，实际交给 ui_kit 统一配置"""
        ui_kit.setup_ttk_styles(self.root)

    def create_decrypt_tab(self):
        """Create the database decryption tab"""
        decrypt_tab = ttk.Frame(self.notebook)
        self.notebook.add(decrypt_tab, text="1. 解密数据库")

        # WeChat version selection
        version_frame = ttk.LabelFrame(decrypt_tab, text="微信版本")
        version_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Radiobutton(version_frame, text="微信 3.x", variable=self.db_version, value=3).pack(side=tk.LEFT, padx=20, pady=10)
        ttk.Radiobutton(version_frame, text="微信 4.0 / 4.1", variable=self.db_version, value=4).pack(side=tk.LEFT, padx=20, pady=10)

        # Decrypt button
        decrypt_button = ttk.Button(decrypt_tab, text="开始解密", command=self.start_decrypt)
        decrypt_button.pack(pady=20)

        # Log area
        log_frame = ttk.LabelFrame(decrypt_tab, text="日志")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.decrypt_log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        self.decrypt_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.decrypt_log.config(state=tk.DISABLED)

    def create_contacts_tab(self):
        """联系人管理页：左侧数据源 + 虚拟列表，右侧详情 + 导出"""
        C, F = self.C, self.F
        tab = tk.Frame(self.notebook, bg=C["bg"])
        self.notebook.add(tab, text="联系人管理")

        wrap = tk.Frame(tab, bg=C["bg"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=18, pady=12)

        # ------------------------------------------------ 左列
        left = tk.Frame(wrap, bg=C["bg"], width=430)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        card = ui_kit.Card(left, title="数据源", subtitle="已解密的数据库目录", accent_left=True)
        card.pack(fill=tk.X)
        body = card.body

        tk.Label(body, text="数据库目录", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).pack(anchor="w")
        row = tk.Frame(body, bg=C["surface"])
        row.pack(fill=tk.X, pady=(4, 10))
        ttk.Entry(row, textvariable=self.db_dir).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ui_kit.PillButton(row, text="浏览", height=31, width=62, bg=C["surface"],
                          command=self.browse_db_dir).pack(side=tk.LEFT, padx=(8, 0))

        ver = tk.Frame(body, bg=C["surface"])
        ver.pack(fill=tk.X, pady=(0, 8))
        tk.Label(ver, text="版本", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).pack(side=tk.LEFT, padx=(0, 6))
        for txt, val in (("微信 3.x", 3), ("微信 4.x", 4)):
            ttk.Radiobutton(ver, text=txt, value=val,
                            variable=self.db_version).pack(side=tk.LEFT, padx=(6, 0))

        self.load_status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.load_status_var, bg=C["surface"],
                 fg=C["accent"], font=F["tiny"]).pack(anchor="w", pady=(0, 6))

        btns = tk.Frame(body, bg=C["surface"])
        btns.pack(fill=tk.X)
        self.load_button = ui_kit.PillButton(btns, text="加载联系人", kind="ghost",
                                             height=36, bg=C["surface"],
                                             command=self.load_contacts)
        self.load_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.test_button = ui_kit.PillButton(btns, text="测试", kind="ghost",
                                             height=36, width=72, bg=C["surface"],
                                             command=self.test_database_connection)
        self.test_button.pack(side=tk.LEFT, padx=(8, 0))

        # 统计徽章（常驻，只更新数字，避免每次筛选重建控件）
        self.stats_row = tk.Frame(left, bg=C["bg"])
        self.stats_row.pack(fill=tk.X, pady=(12, 8))
        self._stat_vars = {}
        # 数字用主文字色、标签用灰，不抢主操作（绿色）的注意力
        for name in ("好友", "群聊", "公众号", "星标"):
            chip = tk.Frame(self.stats_row, bg=C["surface"], highlightthickness=1,
                            highlightbackground=C["border"])
            chip.pack(side=tk.LEFT, padx=(0, 6))
            var = tk.StringVar(value="0")
            self._stat_vars[name] = var
            tk.Label(chip, textvariable=var, bg=C["surface"], fg=C["text"],
                     font=F["h2"]).pack(side=tk.LEFT, padx=(8, 4), pady=2)
            tk.Label(chip, text=name, bg=C["surface"], fg=C["muted"],
                     font=F["tiny"]).pack(side=tk.LEFT, padx=(0, 8))

        # 搜索
        ui_kit.SearchBox(left, self.search_text).pack(fill=tk.X, pady=(0, 8))

        # 虚拟列表
        list_card = ui_kit.Card(left, pad=10)
        list_card.pack(fill=tk.BOTH, expand=True)
        self.vlist = ui_kit.VirtualList(list_card.body,
                                        on_select=self._on_contact_select,
                                        avatar_hook=self._request_avatar)
        self.vlist.pack(fill=tk.BOTH, expand=True)

        # ------------------------------------------------ 右列
        right = tk.Frame(wrap, bg=C["bg"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)   # 日志区吃掉剩余高度

        # 详情卡
        dcard = ui_kit.Card(right, pad=12)
        dcard.grid(row=0, column=0, sticky="ew")
        head = tk.Frame(dcard.body, bg=C["surface"])
        head.pack(fill=tk.X)

        # 详情头像：与列表一致，圆形首字占位（空态隐藏）
        self.detail_avatar = tk.Label(head, text="", bg=C["surface2"], fg=C["text_dim"],
                                      font=F["h2"], width=3, height=1)
        self.detail_avatar.pack(side=tk.LEFT, padx=(0, 14))

        head_text = tk.Frame(head, bg=C["surface"])
        head_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.detail_name = tk.Label(head_text, text="未选择联系人", bg=C["surface"],
                                    fg=C["text"], font=F["h1"], anchor="w")
        self.detail_name.pack(fill=tk.X)
        self.detail_wxid = tk.Label(head_text, text="从左侧列表选择一位联系人或群聊",
                                    bg=C["surface"], fg=C["muted"],
                                    font=F["tiny"], anchor="w")
        self.detail_wxid.pack(fill=tk.X, pady=(2, 0))

        # 类型徽章：空态不显示（避免孤立方块）
        self.detail_badge = tk.Label(head, text="", bg=C["surface2"], fg=C["muted"],
                                     font=F["tiny"], padx=10, pady=3)

        detail_box = tk.Frame(dcard.body, bg=C["surface2"])
        detail_box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.contact_details = tk.Text(detail_box, height=4, wrap=tk.WORD,
                                       bg=C["surface2"], fg=C["muted"], bd=0,
                                       highlightthickness=0, padx=12, pady=8,
                                       font=F["small"], insertbackground=C["accent"])
        detail_vsb = ttk.Scrollbar(detail_box, orient=tk.VERTICAL,
                                   style="Slim.Vertical.TScrollbar",
                                   command=self.contact_details.yview)
        self.contact_details.configure(yscrollcommand=detail_vsb.set)
        detail_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.contact_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.contact_details.config(state=tk.DISABLED)

        # 导出卡
        ecard = ui_kit.Card(right, title="导出设置", accent_left=True)
        ecard.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        eb = ecard.content
        eb.columnconfigure(1, weight=1)

        tk.Label(eb, text="输出目录", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        outrow = tk.Frame(eb, bg=C["surface"])
        outrow.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Entry(outrow, textvariable=self.output_dir).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ui_kit.PillButton(outrow, text="浏览", height=31, width=62, bg=C["surface"],
                          command=self.browse_output_dir).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(eb, text="导出格式", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.format_combobox = ttk.Combobox(eb, values=["HTML", "TXT", "AI_TXT", "DOCX",
                                                        "MARKDOWN", "XLSX"], state="readonly")
        self.format_combobox.grid(row=1, column=1, sticky="w", pady=4)
        last_fmt = self.config.get("last_export_format", "HTML")
        if last_fmt in self.format_combobox["values"]:
            self.format_combobox.set(last_fmt)
        else:
            self.format_combobox.current(0)

        split = tk.Frame(eb, bg=C["surface"])
        split.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 4))
        split.columnconfigure(0, weight=0)
        split.columnconfigure(1, weight=1)

        time_col = tk.Frame(split, bg=C["surface"])
        time_col.grid(row=0, column=0, sticky="nw")
        tk.Label(time_col, text="时间范围", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).pack(anchor="w")
        self._build_time_range(time_col)

        types_col = tk.Frame(split, bg=C["surface"])
        types_col.grid(row=0, column=1, sticky="nw", padx=(24, 0))
        tk.Label(types_col, text="消息类型", bg=C["surface"], fg=C["muted"],
                 font=F["tiny"]).pack(anchor="w")
        types_grid = tk.Frame(types_col, bg=C["surface"])
        types_grid.pack(anchor="w", pady=(2, 0))
        self._build_message_types(types_grid)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(eb, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 6))

        self.export_button = ui_kit.PillButton(eb, text="开始导出", kind="primary",
                                               height=38, bg=C["surface"],
                                               font=F["h1"], command=self.start_export)
        self.export_button.grid(row=4, column=0, columnspan=3, sticky="ew")

        # 日志卡
        lcard = ui_kit.Card(right, pad=12)
        lcard.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        lhead = tk.Frame(lcard.body, bg=C["surface"])
        lhead.pack(fill=tk.X, pady=(0, 6))
        tk.Label(lhead, text="运行日志", bg=C["surface"], fg=C["text"],
                 font=F["h2"]).pack(side=tk.LEFT)
        ui_kit.PillButton(lhead, text="清空", kind="ghost", height=24, width=56,
                          bg=C["surface"], font=F["tiny"],
                          command=self._clear_log).pack(side=tk.RIGHT)

        self.contacts_log = tk.Text(lcard.body, height=2, wrap=tk.WORD,
                                    bg=C["surface2"], fg=C["text_dim"], bd=0,
                                    highlightthickness=0, padx=12, pady=8,
                                    font=F["mono"], insertbackground=C["accent"])
        log_vsb = ttk.Scrollbar(lcard.body, orient=tk.VERTICAL,
                                style="Slim.Vertical.TScrollbar",
                                command=self.contacts_log.yview)
        self.contacts_log.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.contacts_log.pack(fill=tk.BOTH, expand=True)
        self.contacts_log.config(state=tk.DISABLED)
        self.export_log = self.contacts_log
        self.vlist._empty_text = "点击上方「加载联系人」开始"

    def _build_time_range(self, parent):
        """日期 + 时分秒选择（日历按需弹出，启动不加载 tkcalendar 样式）"""
        C, F = self.C, self.F
        for i, (label, year, month, day, hh, mm, ss) in enumerate((
                ("开始", 2020, 1, 1, "00", "00", "00"),
                ("结束", 2035, 12, 31, "23", "59", "59"))):
            row = tk.Frame(parent, bg=C["surface"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=C["surface"], fg=C["faint"],
                     font=F["tiny"], width=3).pack(side=tk.LEFT)
            entry = ui_kit.DateField(row, year=year, month=month, day=day,
                                     width=11, bg=C["surface"])
            entry.pack(side=tk.LEFT)
            setattr(self, "start_date_entry" if i == 0 else "end_date_entry", entry)

            for which, val in (("hour", hh), ("minute", mm), ("second", ss)):
                if which != "hour":
                    tk.Label(row, text=":", bg=C["surface"], fg=C["faint"],
                             font=F["tiny"]).pack(side=tk.LEFT)
                sp = ttk.Spinbox(row, from_=0, to=59 if which != "hour" else 23,
                                 width=3, format="%02.0f")
                sp.set(val)
                sp.pack(side=tk.LEFT)
                setattr(self, f"{'start' if i == 0 else 'end'}_{which}", sp)

    def _build_message_types(self, parent):
        """消息类型复选框（4 列紧凑排布）"""
        self.msg_types = {}
        values = [
            ("文本", MessageType.Text),
            ("图片", MessageType.Image),
            ("语音", MessageType.Audio),
            ("视频", MessageType.Video),
            ("链接", MessageType.LinkMessage),
            ("表情", MessageType.Emoji),
            ("文件", MessageType.File),
            ("系统", MessageType.System),
            ("引用", MessageType.Quote),
            ("合并转发", MessageType.MergedMessages),
            ("全部消息", None),
        ]
        for i, (text, value) in enumerate(values):
            var = tk.BooleanVar(value=(value is None))
            self.msg_types[value] = var
            ttk.Checkbutton(parent, text=text, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 10), pady=2)

    def _clear_log(self):
        self.contacts_log.config(state=tk.NORMAL)
        self.contacts_log.delete("1.0", tk.END)
        self.contacts_log.config(state=tk.DISABLED)

    def create_export_tab(self):
        """Create the export records tab"""
        export_tab = ttk.Frame(self.notebook)
        self.notebook.add(export_tab, text="3. 导出记录")

        # Export settings
        settings_frame = ttk.LabelFrame(export_tab, text="导出设置")
        settings_frame.pack(fill=tk.X, padx=10, pady=10)

        # Output directory
        ttk.Label(settings_frame, text="输出目录:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(settings_frame, textvariable=self.output_dir, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(settings_frame, text="浏览...", command=self.browse_output_dir).grid(row=0, column=2, padx=5, pady=5)

        # Selected contact
        ttk.Label(settings_frame, text="选中的联系人:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(settings_frame, textvariable=self.selected_wxid, width=50, state="readonly").grid(row=1, column=1, padx=5, pady=5)

        # Export format
        ttk.Label(settings_frame, text="导出格式:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.format_combobox = ttk.Combobox(settings_frame, values=["HTML", "TXT", "AI_TXT", "DOCX", "MARKDOWN", "XLSX"])
        self.format_combobox.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        self.format_combobox.current(0)  # Default to HTML

        # Time range
        time_frame = ttk.LabelFrame(settings_frame, text="时间范围")
        time_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky=tk.W+tk.E)

        # 替换原始的输入框为日历选择器
        try:
            from tkcalendar import DateEntry

            ttk.Label(time_frame, text="开始时间:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

            # 使用DateEntry替代普通Entry
            self.start_date_entry = DateEntry(time_frame, width=18,
                                      background='darkblue', foreground='white',
                                      borderwidth=2,
                                      date_pattern='yyyy-mm-dd',
                                      year=2020, month=1, day=1)
            self.start_date_entry.grid(row=0, column=1, padx=5, pady=5)

            # 添加时间选择
            time_frame1 = ttk.Frame(time_frame)
            time_frame1.grid(row=0, column=2, padx=(0, 5), pady=5)
            self.start_hour = ttk.Spinbox(time_frame1, from_=0, to=23, width=3, format="%02.0f")
            self.start_hour.set("00")
            self.start_hour.pack(side=tk.LEFT)
            ttk.Label(time_frame1, text=":").pack(side=tk.LEFT)
            self.start_minute = ttk.Spinbox(time_frame1, from_=0, to=59, width=3, format="%02.0f")
            self.start_minute.set("00")
            self.start_minute.pack(side=tk.LEFT)
            ttk.Label(time_frame1, text=":").pack(side=tk.LEFT)
            self.start_second = ttk.Spinbox(time_frame1, from_=0, to=59, width=3, format="%02.0f")
            self.start_second.set("00")
            self.start_second.pack(side=tk.LEFT)

            ttk.Label(time_frame, text="结束时间:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)

            # 使用DateEntry替代普通Entry
            self.end_date_entry = DateEntry(time_frame, width=18,
                                    background='darkblue', foreground='white',
                                    borderwidth=2,
                                    date_pattern='yyyy-mm-dd',
                                    year=2035, month=12, day=31)
            self.end_date_entry.grid(row=1, column=1, padx=5, pady=5)

            # 添加时间选择
            time_frame2 = ttk.Frame(time_frame)
            time_frame2.grid(row=1, column=2, padx=(0, 5), pady=5)
            self.end_hour = ttk.Spinbox(time_frame2, from_=0, to=23, width=3, format="%02.0f")
            self.end_hour.set("23")
            self.end_hour.pack(side=tk.LEFT)
            ttk.Label(time_frame2, text=":").pack(side=tk.LEFT)
            self.end_minute = ttk.Spinbox(time_frame2, from_=0, to=59, width=3, format="%02.0f")
            self.end_minute.set("59")
            self.end_minute.pack(side=tk.LEFT)
            ttk.Label(time_frame2, text=":").pack(side=tk.LEFT)
            self.end_second = ttk.Spinbox(time_frame2, from_=0, to=59, width=3, format="%02.0f")
            self.end_second.set("59")
            self.end_second.pack(side=tk.LEFT)

        except ImportError:
            # 如果tkcalendar不可用，回退到普通的输入框
            ttk.Label(time_frame, text="开始时间:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
            self.start_time_entry = ttk.Entry(time_frame, width=20)
            self.start_time_entry.grid(row=0, column=1, padx=5, pady=5)
            self.start_time_entry.insert(0, "2020-01-01 00:00:00")

            ttk.Label(time_frame, text="结束时间:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
            self.end_time_entry = ttk.Entry(time_frame, width=20)
            self.end_time_entry.grid(row=0, column=3, padx=5, pady=5)
            self.end_time_entry.insert(0, "2035-12-31 23:59:59")

        # Message types
        types_frame = ttk.LabelFrame(settings_frame, text="消息类型")
        types_frame.grid(row=4, column=0, columnspan=3, padx=5, pady=5, sticky=tk.W+tk.E)

        self.msg_types = {}
        msg_type_values = [
            ("文本消息", MessageType.Text),
            ("图片消息", MessageType.Image),
            ("语音消息", MessageType.Audio),
            ("视频消息", MessageType.Video),
            ("链接消息", MessageType.LinkMessage),
            ("表情消息", MessageType.Emoji),
            ("文件消息", MessageType.File),
            ("系统消息", MessageType.System),
            ("引用消息", MessageType.Quote),
            ("合并转发消息", MessageType.MergedMessages),
            ("全部消息", None)
        ]

        row, col = 0, 0
        for text, value in msg_type_values:
            var = tk.BooleanVar(value=True if value is None else False)
            self.msg_types[value] = var
            cb = ttk.Checkbutton(types_frame, text=text, variable=var)
            cb.grid(row=row, column=col, padx=5, pady=2, sticky=tk.W)
            col += 1
            if col > 2:
                col = 0
                row += 1

        # Export button
        export_button = ttk.Button(export_tab, text="开始导出", command=self.start_export)
        export_button.pack(pady=10)

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(export_tab, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)

        # Export log
        log_frame = ttk.LabelFrame(export_tab, text="导出日志")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.export_log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        self.export_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.export_log.config(state=tk.DISABLED)

    def create_weekly_report_tab(self):
        """周报生成：先占位，首次切到时才真正构建（避免启动时发网络请求）"""
        tab = tk.Frame(self.notebook, bg=self.C["bg"])
        self.notebook.add(tab, text="周报生成")
        self._weekly_tab = tab
        self.weekly_report_frame = None
        self._weekly_hint = tk.Label(tab, text="首次打开时加载周报模块…",
                                     bg=self.C["bg"], fg=self.C["faint"],
                                     font=self.F["small"])
        self._weekly_hint.pack(pady=40)

    def _ensure_weekly_built(self):
        if self.weekly_report_frame is not None or WeeklyReportFrame is None:
            return
        try:
            self._weekly_hint.destroy()
        except tk.TclError:
            pass
        self.weekly_report_frame = WeeklyReportFrame(
            self._weekly_tab,
            database=self.database,
            contact=self._contact,
            config={
                "report_api_url": self.config.get("report_api_url", "http://localhost:8000"),
                "output_dir": self.config.get("output_dir", "./data/")
            }
        )
        self.weekly_report_frame.pack(fill=tk.BOTH, expand=True)
        ui_kit.theme_tree(self.weekly_report_frame)

    def create_settings_tab(self):
        """创建设置标签页，整合解密和导出功能"""
        settings_tab = ttk.Frame(self.notebook, style="WeChat.TFrame")
        self.notebook.add(settings_tab, text="设置")

        # 创建左右分栏布局
        paned_window = ttk.PanedWindow(settings_tab, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # 左侧面板 - 基本设置
        left_panel = ttk.Frame(paned_window, style="WeChat.TFrame")
        paned_window.add(left_panel, weight=1)

        # 右侧面板 - 数据库解密
        right_panel = ttk.Frame(paned_window, style="WeChat.TFrame")
        paned_window.add(right_panel, weight=1)

        # ========== 左侧面板 - 基本设置 ==========
        base_settings_frame = ttk.LabelFrame(left_panel, text="基本设置", style="WeChat.TLabelframe")
        base_settings_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 数据库目录设置
        db_frame = ttk.Frame(base_settings_frame, style="WeChat.TFrame")
        db_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(db_frame, text="数据库目录:", style="WeChat.TLabel").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(db_frame, textvariable=self.db_dir, width=30).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        ttk.Button(db_frame, text="浏览...", command=self.browse_db_dir).grid(row=0, column=2, padx=5, pady=5)

        # 数据库版本设置
        version_frame = ttk.Frame(base_settings_frame, style="WeChat.TFrame")
        version_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(version_frame, text="数据库版本:", style="WeChat.TLabel").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(version_frame, text="微信 3.x", variable=self.db_version, value=3).pack(side=tk.LEFT, padx=15)
        ttk.Radiobutton(version_frame, text="微信 4.0 / 4.1", variable=self.db_version, value=4).pack(side=tk.LEFT, padx=15)

        # 输出目录设置
        output_frame = ttk.Frame(base_settings_frame, style="WeChat.TFrame")
        output_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(output_frame, text="输出目录:", style="WeChat.TLabel").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(output_frame, textvariable=self.output_dir, width=30).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        ttk.Button(output_frame, text="浏览...", command=self.browse_output_dir).grid(row=0, column=2, padx=5, pady=5)

        # 周报API设置
        if WeeklyReportFrame is not None:
            # 创建周报API URL变量
            self.report_api_url = tk.StringVar(value=self.config.get("report_api_url", "http://localhost:8000"))

            ttk.Label(output_frame, text="周报API地址:", style="WeChat.TLabel").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
            ttk.Entry(output_frame, textvariable=self.report_api_url, width=30).grid(row=1, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
            ttk.Button(output_frame, text="测试连接", command=self.test_report_api).grid(row=1, column=2, padx=5, pady=5)

        # 测试和保存按钮
        btn_frame = ttk.Frame(base_settings_frame, style="WeChat.TFrame")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(
            btn_frame,
            text="测试数据库连接",
            command=self.test_database_connection
        ).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        ttk.Button(
            btn_frame,
            text="保存设置",
            command=self.save_current_config,
            style="WeChat.TButton"
        ).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        # 最近使用的数据库
        recent_db_frame = ttk.LabelFrame(base_settings_frame, text="最近使用的数据库", style="WeChat.TLabelframe")
        recent_db_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建最近数据库列表
        self.recent_db_listbox = tk.Listbox(
            recent_db_frame,
            height=5,
            font=("Microsoft YaHei UI", 9),
            bg="#F6F8FA", fg="#424A53",
            highlightthickness=1, highlightbackground="#D0D7DE",
            borderwidth=0, activestyle="none",
            selectbackground="#EAEEF2",
            selectforeground="#E6EDF7"
        )
        self.recent_db_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.recent_db_listbox.bind('<<ListboxSelect>>', self.on_recent_db_select)

        # 填充最近数据库列表
        for db_item in self.config.get("recent_databases", []):
            if isinstance(db_item, dict) and "path" in db_item:
                display_text = f"{db_item['path']} (微信 {db_item['version']})"
                self.recent_db_listbox.insert(tk.END, display_text)

        # ========== 右侧面板 - 数据库解密 ==========
        decrypt_frame = ttk.LabelFrame(right_panel, text="数据库解密", style="WeChat.TLabelframe")
        decrypt_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # WeChat版本选择
        version_frame = ttk.Frame(decrypt_frame, style="WeChat.TFrame")
        version_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(version_frame, text="微信版本:", style="WeChat.TLabel").pack(side=tk.LEFT, padx=5)
        self.decrypt_version = tk.IntVar(value=3)
        ttk.Radiobutton(version_frame, text="微信 3.x", variable=self.decrypt_version, value=3).pack(side=tk.LEFT, padx=15)
        ttk.Radiobutton(version_frame, text="微信 4.0 / 4.1", variable=self.decrypt_version, value=4).pack(side=tk.LEFT, padx=15)

        # 解密按钮
        decrypt_button = ttk.Button(decrypt_frame, text="开始解密", command=self.start_decrypt, style="WeChat.TButton")
        decrypt_button.pack(pady=10)

        # 日志区域
        log_frame = ttk.LabelFrame(decrypt_frame, text="解密日志", style="WeChat.TLabelframe")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        log_box = tk.Frame(log_frame, bg="#F6F8FA")
        log_box.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.decrypt_log = tk.Text(log_box, wrap=tk.WORD, height=8, width=30,
                                   font=("Consolas", 9),
                                   bg="#F6F8FA", fg="#424A53",
                                   insertbackground="#2DA44E",
                                   borderwidth=0, highlightthickness=0,
                                   padx=10, pady=8)
        log_vsb = ttk.Scrollbar(log_box, orient=tk.VERTICAL,
                                style="Slim.Vertical.TScrollbar",
                                command=self.decrypt_log.yview)
        self.decrypt_log.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.decrypt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.decrypt_log.config(state=tk.DISABLED)

        # 历史解密记录区域
        history_frame = ttk.LabelFrame(decrypt_frame, text="历史解密记录", style="WeChat.TLabelframe")
        history_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建历史解密记录列表
        self.decrypt_history_listbox = tk.Listbox(
            history_frame,
            height=5,
            font=("Microsoft YaHei UI", 9),
            bg="#F6F8FA", fg="#424A53",
            highlightthickness=1, highlightbackground="#D0D7DE",
            borderwidth=0, activestyle="none",
            selectbackground="#EAEEF2",
            selectforeground="#E6EDF7"
        )
        self.decrypt_history_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.decrypt_history_listbox.bind('<<ListboxSelect>>', self.on_decrypt_history_select)

        # 填充历史解密记录列表
        for history_item in self.config.get("decrypt_history", []):
            if isinstance(history_item, dict) and "wxid" in history_item:
                display_text = f"{history_item['name']} ({history_item['wxid']}) - 微信{history_item['version']}"
                self.decrypt_history_listbox.insert(tk.END, display_text)

    def on_recent_db_select(self, event):
        """处理选择最近使用的数据库"""
        selection = self.recent_db_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.config.get("recent_databases", [])):
            return

        # 获取选定的数据库
        db_item = self.config["recent_databases"][index]
        if isinstance(db_item, dict) and "path" in db_item:
            # 设置数据库路径和版本
            self.db_dir.set(db_item["path"])
            self.db_version.set(db_item["version"])

            # 尝试连接数据库
            self.root.after(100, self.test_database_connection)

    def on_decrypt_history_select(self, event):
        """处理选择历史解密记录"""
        selection = self.decrypt_history_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.config.get("decrypt_history", [])):
            return

        # 获取选定的历史记录
        history_item = self.config["decrypt_history"][index]
        if isinstance(history_item, dict) and "db_path" in history_item:
            # 设置数据库路径和版本
            self.db_dir.set(history_item["db_path"])
            self.db_version.set(history_item["version"])

            # 尝试连接数据库
            self.root.after(100, self.test_database_connection)

    def load_contacts(self, auto=False):
        """Load contacts from the database"""
        # Disable the button to prevent multiple clicks
        if self.load_button:
            self.load_button.config(state=tk.DISABLED, text="正在加载...")

        # 更新状态
        self.load_status_var.set("正在加载联系人...")

        db_dir = self.db_dir.get()
        db_version = self.db_version.get()

        self.log_message(self.contacts_log, f"尝试加载联系人，数据库目录: {db_dir}, 版本: {db_version}")

        if not db_dir:
            messagebox.showerror("错误", "请先选择数据库目录")
            self.log_message(self.contacts_log, "错误: 未选择数据库目录")
            self.load_status_var.set("未选择数据库目录")
            if self.load_button:
                self.load_button.config(state=tk.NORMAL, text="加载联系人")
            return

        if not os.path.exists(db_dir):
            messagebox.showerror("错误", f"数据库目录不存在: {db_dir}")
            self.log_message(self.contacts_log, f"错误: 数据库目录不存在: {db_dir}")
            self.load_status_var.set("数据库目录不存在")
            if self.load_button:
                self.load_button.config(state=tk.NORMAL, text="加载联系人")
            return

        # 检查目录中是否有数据库文件（微信4.x 的库位于子目录，需递归查找）
        db_files = [f for f in os.listdir(db_dir) if f.endswith('.db')]
        if not db_files:
            for root, _dirs, files in os.walk(db_dir):
                for f in files:
                    if f.endswith('.db'):
                        db_files.append(os.path.relpath(os.path.join(root, f), db_dir))
                if len(db_files) >= 10:
                    break
        if not db_files:
            error_msg = "目录中没有找到数据库文件"
            messagebox.showerror("错误", error_msg)
            self.log_message(self.contacts_log, f"错误: {error_msg}")
            self.load_status_var.set("未找到数据库文件")
            if self.load_button:
                self.load_button.config(state=tk.NORMAL, text="加载联系人")
            return

        # 列出找到的数据库文件
        self.log_message(self.contacts_log, f"找到的数据库文件: {db_files}")

        # 如果不是自动加载，确认用户是否要继续
        if not auto and not messagebox.askyesno("确认", f"确定要从目录 {db_dir} 加载联系人吗？"):
            self.log_message(self.contacts_log, "用户取消了加载联系人")
            self.load_status_var.set("已取消")
            if self.load_button:
                self.load_button.config(state=tk.NORMAL, text="加载联系人")
            return

        self.status_var.set("正在加载联系人...")
        self.log_message(self.contacts_log, "开始加载联系人...")

        # 保存当前数据库设置到配置
        try:
            import config
            self.config = config.add_recent_database(self.config, db_dir, db_version)
            config.save_config(self.config)
        except ImportError:
            pass

        # Run in a separate thread to avoid freezing the UI
        threading.Thread(target=self._load_contacts_thread, args=(db_dir, db_version), daemon=True).start()

    def _load_contacts_thread(self, db_dir, db_version):
        """Thread function for loading contacts"""
        try:
            # 记录更详细的调试信息
            self.log_message(self.contacts_log, f"创建数据库连接: {db_dir}, 版本: {db_version}")
            self.log_message(self.contacts_log, f"当前工作目录: {os.getcwd()}")
            self.log_message(self.contacts_log, f"数据库目录是否存在: {os.path.exists(db_dir)}")
            self.log_message(self.contacts_log, f"数据库目录是否是目录: {os.path.isdir(db_dir)}")

            # 更新状态
            self.root.after(0, lambda: self.load_status_var.set("正在检查数据库文件..."))

            # 尝试列出数据库目录中的文件
            try:
                files = os.listdir(db_dir)
                self.log_message(self.contacts_log, f"数据库目录中的文件: {files}")

                # 检查数据库文件是否可读
                for file in files:
                    if file.endswith('.db'):
                        file_path = os.path.join(db_dir, file)
                        self.log_message(self.contacts_log, f"检查文件 {file_path}")
                        self.log_message(self.contacts_log, f"  - 文件存在: {os.path.exists(file_path)}")
                        self.log_message(self.contacts_log, f"  - 文件大小: {os.path.getsize(file_path)} 字节")
                        self.log_message(self.contacts_log, f"  - 文件可读: {os.access(file_path, os.R_OK)}")
            except Exception as e:
                self.log_message(self.contacts_log, f"列出数据库目录中的文件时出错: {str(e)}")

            # 更新状态
            self.root.after(0, lambda: self.load_status_var.set("正在创建数据库连接..."))

            # 创建数据库连接
            self.log_message(self.contacts_log, "正在创建 DatabaseConnection 对象...")
            conn = DatabaseConnection(db_dir, db_version)
            self.log_message(self.contacts_log, "DatabaseConnection 对象创建成功，正在获取接口...")
            self.database = conn.get_interface()
            self.log_message(self.contacts_log, f"接口获取结果: {self.database is not None}")

            if not self.database:
                error_msg = "数据库连接失败，请检查数据库路径和版本是否正确"
                self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                self.log_message(self.contacts_log, f"错误: {error_msg}")
                self.status_var.set("联系人加载失败")
                self.root.after(0, lambda: self.load_status_var.set("数据库连接失败"))
                # Re-enable the button
                if self.load_button:
                    self.root.after(0, lambda: self.load_button.config(state=tk.NORMAL, text="加载联系人"))
                return

            # 更新状态
            self.root.after(0, lambda: self.load_status_var.set("正在获取联系人列表..."))

            self.log_message(self.contacts_log, "数据库连接成功，开始获取联系人列表...")
            try:
                self.contacts = self.database.get_contacts()
                self.log_message(self.contacts_log, f"get_contacts() 返回结果类型: {type(self.contacts)}")
                self.log_message(self.contacts_log, f"联系人列表长度: {len(self.contacts) if self.contacts else 0}")
            except Exception as e:
                self.log_message(self.contacts_log, f"获取联系人列表时出错: {str(e)}")
                self.log_message(self.contacts_log, traceback.format_exc())
                self.root.after(0, lambda: self.load_status_var.set("获取联系人列表失败"))
                raise  # 重新抛出异常，让外层的 try-except 捕获

            if not self.contacts:
                self.log_message(self.contacts_log, "警告: 未找到任何联系人，请检查数据库是否正确")
                self.root.after(0, lambda: messagebox.showwarning("警告", "未找到任何联系人，请检查数据库是否正确"))
                self.status_var.set("未找到联系人")
                self.root.after(0, lambda: self.load_status_var.set("未找到联系人"))
                # Re-enable the button
                if self.load_button:
                    self.root.after(0, lambda: self.load_button.config(state=tk.NORMAL, text="加载联系人"))
                return

            # 更新状态
            self.root.after(0, lambda: self.load_status_var.set("正在处理联系人数据..."))

            self.log_message(self.contacts_log, "复制联系人列表...")
            self.filtered_contacts = self.contacts.copy()
            self.log_message(self.contacts_log, f"成功获取 {len(self.contacts)} 个联系人")

            # 记录一些联系人信息用于调试
            if self.contacts:
                self.log_message(self.contacts_log, "联系人示例:")
                for i, contact in enumerate(self.contacts[:3]):  # 只显示前3个联系人
                    self.log_message(self.contacts_log, f"联系人 {i+1}: wxid={contact.wxid}, nickname={contact.nickname}")
                    if hasattr(contact, 'remark'):
                        self.log_message(self.contacts_log, f"  备注: {contact.remark}")

            # 更新状态
            self.root.after(0, lambda: self.load_status_var.set("正在更新界面..."))

            # Update the UI in the main thread
            self.log_message(self.contacts_log, "更新UI...")
            self.root.after(0, self._update_contacts_list)
            self.status_var.set(f"已加载 {len(self.contacts)} 个联系人")

            # 更新最终状态
            success_msg = f"已成功加载 {len(self.contacts)} 个联系人"
            self.root.after(0, lambda: self.load_status_var.set(success_msg))
            self.root.after(0, lambda: messagebox.showinfo("成功", success_msg))

            # Re-enable the button
            if self.load_button:
                self.root.after(0, lambda: self.load_button.config(state=tk.NORMAL, text="重新加载联系人"))

            # 如果周报生成标签页存在，更新数据库
            if hasattr(self, 'weekly_report_frame') and self.weekly_report_frame is not None:
                self.root.after(0, lambda: self._update_weekly_report_tab())

        except Exception as e:
            error_msg = f"加载联系人时出错: {str(e)}"
            self.log_message(self.contacts_log, f"错误: {error_msg}")
            self.log_message(self.contacts_log, traceback.format_exc())
            _log_gui_error('load_contacts', f'db_dir={db_dir} ver={db_version}')
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.status_var.set("联系人加载失败")
            self.root.after(0, lambda: self.load_status_var.set("加载失败"))

            # Re-enable the button
            if self.load_button:
                self.root.after(0, lambda: self.load_button.config(state=tk.NORMAL, text="加载联系人"))

    @staticmethod
    def _contact_kind(contact):
        """返回 (分组名, 头像类型)"""
        if getattr(contact, 'type', None) == 'star':
            return "星标联系人", "star"
        if getattr(contact, 'wxid', '').startswith('gh_'):
            return "公众号", "oa"
        if getattr(contact, 'is_chatroom', False):
            return "群聊", "group"
        return "好友", "friend"

    def _update_contacts_list(self):
        """重建行模型并交给虚拟列表渲染（只画可视行，不影响滚动帧率）"""
        if not getattr(self, "vlist", None):
            return

        # 让在途头像任务失效，避免搜索后回填到错误的行
        self._avatar_gen += 1
        with self._avatar_todo.mutex:
            self._avatar_todo.queue.clear()
        self._avatar_pending.clear()

        groups = {"星标联系人": [], "好友": [], "群聊": [], "公众号": []}
        for contact in self.filtered_contacts:
            groups[self._contact_kind(contact)[0]].append(contact)

        tag_map = {"star": "星标", "oa": "公众号", "group": "群聊", "friend": "好友"}
        rows = []
        for name, items in groups.items():
            if not items:
                continue
            rows.append({"kind": "header", "title": name, "count": len(items)})
            for contact in items:
                kind = self._contact_kind(contact)[1]
                rows.append({
                    "kind": "item",
                    "contact": contact,
                    "tag": tag_map.get(kind, "好友"),
                    "badge": "★" if kind == "star" else None,
                })

        self.vlist._selected = None
        self.vlist.set_rows(rows)
        self._update_stats()
        if hasattr(self, "status_right"):
            self.status_right.set(
                f"共 {len(self.contacts)} 位联系人 · 当前显示 {len(self.filtered_contacts)} 位")

    def _update_stats(self):
        """只刷新统计数字，不重建控件"""
        if not getattr(self, "_stat_vars", None):
            return
        counts = {"好友": 0, "群聊": 0, "公众号": 0, "星标": 0}
        for c in self.contacts:
            kind = self._contact_kind(c)[1]
            counts[{"friend": "好友", "group": "群聊",
                    "oa": "公众号", "star": "星标"}[kind]] += 1
        for name, var in self._stat_vars.items():
            var.set(str(counts.get(name, 0)))

    def _on_contact_select(self, contact):
        """列表选中回调"""
        self._contact = contact
        self.selected_wxid.set(getattr(contact, "wxid", ""))

        nickname = getattr(contact, "nickname", "") or ""
        remark = getattr(contact, "remark", "") or ""
        display = f"{remark}（{nickname}）" if remark and nickname else (remark or nickname or "未知联系人")
        self.detail_name.config(text=display)
        self.detail_wxid.config(text=getattr(contact, "wxid", ""))
        self.detail_badge.config(text=self._contact_kind(contact)[0])
        self.detail_badge.pack(side=tk.RIGHT)

        self._update_contact_details(contact)
        self._load_avatar(contact)

        if hasattr(self, "weekly_report_frame") and self.weekly_report_frame is not None:
            self.weekly_report_frame.contact = contact
            self.weekly_report_frame.database = self.database

    def _ensure_avatar_pipeline(self):
        """启动唯一的头像 worker 与主线程消费器"""
        if not self._avatar_worker_started:
            self._avatar_worker_started = True
            threading.Thread(target=self._avatar_worker, daemon=True).start()
        if self._avatar_pump_job is None:
            self._avatar_pump_job = self.root.after(30, self._pump_avatar_results)

    def _request_avatar(self, wxid):
        """供虚拟列表调用：只为可见行请求头像"""
        if not wxid or not self.database:
            return
        if (wxid in self.contact_avatar_cache or wxid in self._avatar_pending
                or wxid in self._avatar_missing):
            return
        self._avatar_pending.add(wxid)
        self._avatar_todo.put((self._avatar_gen, wxid))
        self._ensure_avatar_pipeline()

    def _avatar_worker(self):
        while True:
            gen, wxid = self._avatar_todo.get()
            try:
                if gen != self._avatar_gen:
                    continue
                buf = None
                try:
                    if hasattr(self.database, 'get_avatar_buffer'):
                        buf = self.database.get_avatar_buffer(wxid)
                    elif hasattr(self.database, 'get_avatar_urls'):
                        urls = self.database.get_avatar_urls(wxid)
                        if urls and os.path.exists(urls[0]):
                            with open(urls[0], 'rb') as f:
                                buf = f.read()
                except Exception:
                    buf = None
                if buf:
                    img = Image.open(io.BytesIO(buf)).convert("RGB")
                    img = img.resize((36, 36), Image.LANCZOS)
                    self._avatar_done.put((gen, wxid, img))
                else:
                    self._avatar_done.put((gen, wxid, None))
            except Exception:
                pass
            finally:
                self._avatar_todo.task_done()

    def _pump_avatar_results(self):
        """每帧最多应用 6 张头像，队列空了就停止轮询"""
        self._avatar_pump_job = None
        applied = 0
        while applied < 6:
            try:
                gen, wxid, img = self._avatar_done.get_nowait()
            except queue.Empty:
                break
            if gen != self._avatar_gen:
                self._avatar_pending.discard(wxid)
                continue
            self._avatar_pending.discard(wxid)
            if img is None:
                self._avatar_missing.add(wxid)
                continue
            try:
                photo = ImageTk.PhotoImage(img)
                self.contact_avatar_cache[wxid] = photo
                self.vlist.set_avatar(wxid, photo)
                if self._contact is not None and getattr(self._contact, "wxid", "") == wxid:
                    self.detail_avatar.config(image=photo, text="")
                    self.detail_avatar.image = photo
            except Exception:
                pass
            applied += 1

        if self._avatar_pending or not self._avatar_done.empty():
            self._avatar_pump_job = self.root.after(30, self._pump_avatar_results)

    def _update_contact_details(self, contact):
        """联系人详情（暗色富文本）"""
        t = self.contact_details
        F = self.F
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.tag_configure("k", foreground=self.C["muted"], font=F["small"])
        t.tag_configure("v", foreground=self.C["text"], font=F["small"])
        t.tag_configure("s", foreground=self.C["accent"], font=F["h2"],
                        spacing1=6, spacing3=2)

        def line(key, value):
            t.insert(tk.END, f"{key}  ", "k")
            t.insert(tk.END, f"{value}\n", "v")

        t.insert(tk.END, "基本信息\n", "s")
        line("微信 ID", getattr(contact, "wxid", "") or "-")
        line("昵称", getattr(contact, "nickname", "") or "-")
        if getattr(contact, "remark", ""):
            line("备注", contact.remark)
        if getattr(contact, "alias", ""):
            line("别名", contact.alias)

        t.insert(tk.END, "\n类型信息\n", "s")
        kind = self._contact_kind(contact)
        if getattr(contact, "is_chatroom", False):
            line("类型", "群聊")
            if self.database:
                try:
                    members = self.database.get_chatroom_members(contact.wxid)
                    count = len(members) if members else 0
                    line("成员数", count)
                    if 0 < count <= 12:
                        names = []
                        for member in members[:12]:
                            names.append(getattr(member, "display_name", None)
                                         or getattr(member, "nickname", "") or "-")
                        line("成员", "、".join(names))
                except Exception as e:
                    line("提示", f"群成员读取失败：{e}")
        elif getattr(contact, "wxid", "").startswith("gh_"):
            line("类型", "公众号")
        else:
            line("类型", "个人")

        t.config(state=tk.DISABLED)

    def filter_contacts(self, *args):
        """搜索防抖：连续输入只重建一次列表"""
        if not hasattr(self, "vlist"):
            return
        if self._search_job:
            try:
                self.root.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.root.after(160, self._apply_filter)

    def _apply_filter(self):
        self._search_job = None
        q = self.search_text.get().strip().lower()
        if not q:
            self.filtered_contacts = list(self.contacts)
        else:
            self.filtered_contacts = [
                c for c in self.contacts
                if q in (getattr(c, 'nickname', '') or "").lower()
                or q in (getattr(c, 'remark', '') or "").lower()
                or q in (getattr(c, 'wxid', '') or "").lower()
            ]
        self._update_contacts_list()

    def start_export(self):
        """Start the export process"""
        wxid = self.selected_wxid.get()
        if not wxid:
            messagebox.showerror("错误", "请先选择一个联系人")
            return

        if not self.database:
            messagebox.showerror("错误", "请先加载联系人")
            return

        output_dir = self.output_dir.get()
        if not output_dir:
            messagebox.showerror("错误", "请选择输出目录")
            return

        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        # Get export format
        format_str = self.format_combobox.get()
        format_map = {
            "HTML": FileType.HTML,
            "TXT": FileType.TXT,
            "AI_TXT": FileType.AI_TXT,
            "DOCX": FileType.DOCX,
            "MARKDOWN": FileType.MARKDOWN,
            "XLSX": FileType.XLSX
        }
        file_type = format_map.get(format_str, FileType.HTML)

        # Get time range
        try:
            # 检查是否使用日历选择器
            if hasattr(self, 'start_date_entry'):
                # 使用日历选择器和时间选择器获取完整的日期时间字符串
                start_date = self.start_date_entry.get_date()
                start_time = f"{self.start_hour.get()}:{self.start_minute.get()}:{self.start_second.get()}"
                start_time = f"{start_date.strftime('%Y-%m-%d')} {start_time}"

                end_date = self.end_date_entry.get_date()
                end_time = f"{self.end_hour.get()}:{self.end_minute.get()}:{self.end_second.get()}"
                end_time = f"{end_date.strftime('%Y-%m-%d')} {end_time}"
            else:
                # 使用常规输入框
                start_time = self.start_time_entry.get()
                end_time = self.end_time_entry.get()
        except Exception as e:
            self.log_message(self.export_log, f"获取时间范围时出错: {str(e)}")
            self.log_message(self.export_log, traceback.format_exc())
            messagebox.showerror("错误", f"时间格式错误: {str(e)}")
            return

        # Get message types
        selected_types = set()
        for msg_type, var in self.msg_types.items():
            if var.get() and msg_type is not None:
                selected_types.add(msg_type)

        # If "All messages" is selected, set message_types to None
        message_types = None if self.msg_types[None].get() else selected_types

        self.status_var.set("正在导出记录...")
        self.progress_var.set(0)
        self.log_message(self.export_log, f"开始导出 {wxid} 的聊天记录...")
        self.log_message(self.export_log, f"时间范围: {start_time} 到 {end_time}")

        # Run export in a separate thread
        threading.Thread(
            target=self._export_thread,
            args=(wxid, output_dir, file_type, message_types, [start_time, end_time]),
            daemon=True
        ).start()

    def _export_thread(self, wxid, output_dir, file_type, message_types, time_range):
        """Thread function for exporting records"""
        try:
            contact = self.database.get_contact_by_username(wxid)
            if not contact:
                self.root.after(0, lambda: messagebox.showerror("错误", f"找不到联系人: {wxid}"))
                self.status_var.set("导出失败")
                return

            exporter_map = {
                FileType.HTML: HtmlExporter,
                FileType.TXT: TxtExporter,
                FileType.AI_TXT: AiTxtExporter,
                FileType.DOCX: DocxExporter,
                FileType.MARKDOWN: MarkdownExporter,
                FileType.XLSX: ExcelExporter
            }

            exporter_class = exporter_map.get(file_type)
            if not exporter_class:
                self.root.after(0, lambda: messagebox.showerror("错误", f"不支持的导出格式: {file_type}"))
                self.status_var.set("导出失败")
                return

            self.log_message(self.export_log, f"使用 {exporter_class.__name__} 导出到 {output_dir}")

            exporter = exporter_class(
                self.database,
                contact,
                output_dir=output_dir,
                type_=file_type,
                message_types=message_types,
                time_range=time_range,
                group_members=None
            )

            # Start export
            self.log_message(self.export_log, "导出中，请稍候...")
            start_time = time.time()
            exporter.start()
            end_time = time.time()

            self.log_message(self.export_log, f"导出完成，耗时: {end_time - start_time:.2f}秒")
            self.progress_var.set(100)
            self.status_var.set("导出完成")

            # Show success message
            self.root.after(0, lambda: messagebox.showinfo("成功", f"导出完成，文件保存在 {output_dir}"))
        except Exception as e:
            self.log_message(self.export_log, f"导出过程中出错: {str(e)}")
            self.log_message(self.export_log, traceback.format_exc())
            self.status_var.set("导出失败")
            self.root.after(0, lambda: messagebox.showerror("错误", f"导出过程中出错: {str(e)}"))

    def _load_avatar(self, contact):
        """详情卡头像：命中缓存直接用，否则先显首字占位、再排队加载"""
        wxid = getattr(contact, "wxid", "")
        if not wxid:
            return
        photo = self.contact_avatar_cache.get(wxid)
        if photo:
            self.detail_avatar.config(image=photo, text="")
            self.detail_avatar.image = photo
        else:
            nick = getattr(contact, "remark", "") or getattr(contact, "nickname", "") or "?"
            self.detail_avatar.config(image="", text=nick[:1])
            self._request_avatar(wxid)

    def browse_db_dir(self):
        """Browse for database directory"""
        directory = filedialog.askdirectory(title="选择数据库目录")
        if directory:
            self.db_dir.set(directory)

    def browse_output_dir(self):
        """Browse for output directory"""
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_dir.set(directory)

    def log_message(self, log_widget, message):
        """写日志：入队后由主线程合并刷新，可在任意线程调用"""
        if log_widget is None:
            return
        self._log_queue.put((log_widget, str(message)))

    def _start_log_pump(self):
        if self._log_job is None:
            self._log_job = self.root.after(120, self._flush_log)

    def _flush_log(self):
        """批量刷新日志，并把每个控件限制在 2000 行以内"""
        self._log_job = None
        pending = {}
        drained = 0
        while drained < 120:
            try:
                widget, msg = self._log_queue.get_nowait()
            except queue.Empty:
                break
            pending.setdefault(id(widget), (widget, []))[1].append(msg)
            drained += 1

        for widget, msgs in pending.values():
            try:
                if not widget.winfo_exists():
                    continue
                at_bottom = widget.yview()[1] > 0.97
                widget.config(state=tk.NORMAL)
                widget.insert(tk.END, "\n".join(msgs) + "\n")
                lines = int(widget.index("end-1c").split(".")[0])
                if lines > 2500:
                    widget.delete("1.0", f"{lines - 2000}.0")
                if at_bottom:
                    widget.see(tk.END)
                widget.config(state=tk.DISABLED)
            except tk.TclError:
                pass

        self._log_job = self.root.after(150, self._flush_log)

    def test_database_connection(self):
        """测试数据库连接"""
        # 更新状态
        self.load_status_var.set("正在测试数据库连接...")
        if hasattr(self, 'test_button'):
            self.test_button.config(state=tk.DISABLED, text="测试中...")

        db_dir = self.db_dir.get()
        db_version = self.db_version.get()

        self.log_message(self.contacts_log, f"测试数据库连接: {db_dir}, 版本: {db_version}")

        if not db_dir:
            messagebox.showerror("错误", "请先选择数据库目录")
            self.log_message(self.contacts_log, "错误: 未选择数据库目录")
            self.load_status_var.set("未选择数据库目录")
            if hasattr(self, 'test_button'):
                self.test_button.config(state=tk.NORMAL, text="测试连接")
            return

        if not os.path.exists(db_dir):
            messagebox.showerror("错误", f"数据库目录不存在: {db_dir}")
            self.log_message(self.contacts_log, f"错误: 数据库目录不存在: {db_dir}")
            self.load_status_var.set("数据库目录不存在")
            if hasattr(self, 'test_button'):
                self.test_button.config(state=tk.NORMAL, text="测试连接")
            return

        # 在单独的线程中运行测试
        threading.Thread(target=self._test_connection_thread, args=(db_dir, db_version), daemon=True).start()

    def _test_connection_thread(self, db_dir, db_version):
        """测试数据库连接的线程函数"""
        try:
            # 列出目录内容
            self.log_message(self.contacts_log, f"目录内容: {os.listdir(db_dir)}")

            # 检查数据库文件（微信4.x 的库位于子目录，需递归查找）
            db_files = [f for f in os.listdir(db_dir) if f.endswith('.db')]
            if not db_files:
                for root_dir, _dirs, files in os.walk(db_dir):
                    for f in files:
                        if f.endswith('.db'):
                            db_files.append(os.path.relpath(os.path.join(root_dir, f), db_dir))
                    if len(db_files) >= 10:
                        break
            self.log_message(self.contacts_log, f"找到的数据库文件: {db_files}")

            if not db_files:
                self.root.after(0, lambda: messagebox.showerror("错误", "目录中没有找到数据库文件"))
                self.log_message(self.contacts_log, "错误: 目录中没有找到数据库文件")
                self.root.after(0, lambda: self.load_status_var.set("未找到数据库文件"))
                if hasattr(self, 'test_button'):
                    self.root.after(0, lambda: self.test_button.config(state=tk.NORMAL, text="测试连接"))
                return

            # 尝试创建数据库连接
            self.log_message(self.contacts_log, "尝试创建数据库连接...")
            conn = DatabaseConnection(db_dir, db_version)
            db_interface = conn.get_interface()

            if not db_interface:
                self.root.after(0, lambda: messagebox.showerror("错误", "数据库连接失败"))
                self.log_message(self.contacts_log, "错误: 数据库连接失败")
                self.root.after(0, lambda: self.load_status_var.set("数据库连接失败"))
                if hasattr(self, 'test_button'):
                    self.root.after(0, lambda: self.test_button.config(state=tk.NORMAL, text="测试连接"))
                return

            # 尝试获取联系人数量
            self.log_message(self.contacts_log, "尝试获取联系人数量...")
            try:
                contacts = db_interface.get_contacts()
                contact_count = len(contacts) if contacts else 0
                self.log_message(self.contacts_log, f"找到 {contact_count} 个联系人")

                # 保存数据库信息到配置
                try:
                    import config
                    self.config = config.add_recent_database(self.config, db_dir, db_version)
                    config.save_config(self.config)
                except ImportError:
                    pass

                # 显示成功消息
                success_msg = f"连接成功! 找到 {contact_count} 个联系人"
                self.root.after(0, lambda: messagebox.showinfo("测试成功", success_msg))
                self.root.after(0, lambda: self.load_status_var.set(success_msg))
            except Exception as e:
                self.log_message(self.contacts_log, f"获取联系人时出错: {str(e)}")
                self.log_message(self.contacts_log, traceback.format_exc())
                _log_gui_error('test_connection/get_contacts', f'db_dir={db_dir} ver={db_version}')
                self.root.after(0, lambda: messagebox.showwarning("警告", f"数据库连接成功，但获取联系人时出错: {str(e)}"))
                self.root.after(0, lambda: self.load_status_var.set("连接成功，但获取联系人失败"))

            if hasattr(self, 'test_button'):
                self.root.after(0, lambda: self.test_button.config(state=tk.NORMAL, text="测试连接"))
        except Exception as e:
            self.log_message(self.contacts_log, f"测试连接时出错: {str(e)}")
            self.log_message(self.contacts_log, traceback.format_exc())
            self.root.after(0, lambda: messagebox.showerror("错误", f"测试连接时出错: {str(e)}"))
            self.root.after(0, lambda: self.load_status_var.set("测试连接失败"))
            if hasattr(self, 'test_button'):
                self.root.after(0, lambda: self.test_button.config(state=tk.NORMAL, text="测试连接"))

    def start_decrypt(self):
        """开始数据库解密过程"""
        self.log_message(self.decrypt_log, "开始解密数据库...")
        self.status_var.set("正在解密数据库...")

        # 在单独的线程中运行解密以避免UI卡顿
        threading.Thread(target=self._decrypt_thread, daemon=True).start()

    def _decrypt_thread(self):
        """数据库解密的线程函数"""
        try:
            # 使用设置页面的解密版本值
            decrypt_version = getattr(self, 'decrypt_version', self.db_version).get()

            if decrypt_version == 3:
                self.log_message(self.decrypt_log, "解析微信3.x版本的数据库...")
                version_list_path = './wxManager/decrypt/version_list.json'
                try:
                    with open(version_list_path, "r", encoding="utf-8") as f:
                        version_list = json.loads(f.read())
                except Exception as e:
                    self.log_message(self.decrypt_log, f"读取版本列表失败: {str(e)}")
                    self.status_var.set("解密失败")
                    return

                r_3 = get_info_v3(version_list)
                if not r_3:
                    self.log_message(self.decrypt_log, "未找到微信3.x版本信息，请确保微信已启动")
                    self.status_var.set("解密失败")
                    return

                for wx_info in r_3:
                    self.log_message(self.decrypt_log, f"找到微信账号: {wx_info.wxid} ({wx_info.nick_name})")
                    me = Me()
                    me.wx_dir = wx_info.wx_dir
                    me.wxid = wx_info.wxid
                    me.name = wx_info.nick_name
                    info_data = me.to_json()
                    output_dir = wx_info.wxid
                    key = wx_info.key
                    if not key:
                        self.log_message(self.decrypt_log, "错误! 未找到key，请重启微信后再试")
                        continue

                    wx_dir = wx_info.wx_dir
                    self.log_message(self.decrypt_log, f"开始解密数据库文件，源目录: {wx_dir}")
                    decrypt_v3.decrypt_db_files(key, src_dir=wx_dir, dest_dir=output_dir)

                    # 导出的数据库在 output_dir/Msg 文件夹下，后面会用到
                    db_path = os.path.join(output_dir, "Msg")
                    with open(os.path.join(db_path, 'info.json'), 'w', encoding='utf-8') as f:
                        json.dump(info_data, f, ensure_ascii=False, indent=4)

                    self.log_message(self.decrypt_log, f"数据库解析成功，在{db_path}路径下")
                    self.db_dir.set(db_path)

                    # 保存解密历史记录
                    try:
                        import config
                        self.config = config.add_decrypt_history(
                            self.config, wx_info.wxid, wx_info.nick_name, db_path, 3
                        )
                        # 更新最近数据库列表
                        self.config = config.add_recent_database(self.config, db_path, 3)
                        config.save_config(self.config)

                        # 刷新历史记录列表
                        if hasattr(self, 'decrypt_history_listbox'):
                            self.decrypt_history_listbox.delete(0, tk.END)
                            for history_item in self.config.get("decrypt_history", []):
                                if isinstance(history_item, dict) and "wxid" in history_item:
                                    display_text = f"{history_item['name']} ({history_item['wxid']}) - 微信{history_item['version']}"
                                    self.decrypt_history_listbox.insert(tk.END, display_text)
                    except ImportError:
                        pass
            else:
                self.log_message(self.decrypt_log, "解析微信 4.0 / 4.1 版本的数据库...")
                r_4 = get_info_v4()
                if not r_4:
                    self.log_message(self.decrypt_log, "未找到微信 4.0 / 4.1 版本信息，请确保微信已启动")
                    self.status_var.set("解密失败")
                    return

                for wx_info in r_4:
                    self.log_message(self.decrypt_log, f"找到微信账号: {wx_info.wxid} ({wx_info.nick_name})")
                    me = Me()
                    me.wx_dir = wx_info.wx_dir
                    me.wxid = wx_info.wxid
                    me.name = wx_info.nick_name
                    me.xor_key = get_decode_code_v4(wx_info.wx_dir)
                    info_data = me.to_json()
                    output_dir = wx_info.wxid  # 数据库输出文件夹
                    key = wx_info.key
                    if not key:
                        self.log_message(self.decrypt_log, "错误! 未找到key，请重启微信后再试")
                        continue

                    wx_dir = wx_info.wx_dir
                    self.log_message(self.decrypt_log, f"开始解密数据库文件，源目录: {wx_dir}")
                    decrypt_v4.decrypt_db_files(key, src_dir=wx_dir, dest_dir=output_dir)

                    # 导出的数据库在 output_dir/db_storage 文件夹下，后面会用到
                    db_path = os.path.join(output_dir, "db_storage")
                    with open(os.path.join(db_path, 'info.json'), 'w', encoding='utf-8') as f:
                        json.dump(info_data, f, ensure_ascii=False, indent=4)

                    self.log_message(self.decrypt_log, f"数据库解析成功，在{db_path}路径下")
                    self.db_dir.set(db_path)

                    # 保存解密历史记录
                    try:
                        import config
                        self.config = config.add_decrypt_history(
                            self.config, wx_info.wxid, wx_info.nick_name, db_path, 4
                        )
                        # 更新最近数据库列表
                        self.config = config.add_recent_database(self.config, db_path, 4)
                        config.save_config(self.config)

                        # 刷新历史记录列表
                        if hasattr(self, 'decrypt_history_listbox'):
                            self.decrypt_history_listbox.delete(0, tk.END)
                            for history_item in self.config.get("decrypt_history", []):
                                if isinstance(history_item, dict) and "wxid" in history_item:
                                    display_text = f"{history_item['name']} ({history_item['wxid']}) - 微信{history_item['version']}"
                                    self.decrypt_history_listbox.insert(tk.END, display_text)
                    except ImportError:
                        pass

            self.status_var.set("数据库解密完成")
            # 自动切换到联系人管理标签页
            self.notebook.select(0)
            # 自动尝试连接数据库
            self.root.after(1000, self.test_database_connection)
        except Exception as e:
            self.log_message(self.decrypt_log, f"解密过程中出错: {str(e)}")
            self.log_message(self.decrypt_log, traceback.format_exc())
            _log_gui_error('decrypt')
            self.status_var.set("解密失败")

    def test_report_api(self):
        """测试周报生成API连接"""
        if not hasattr(self, 'report_api_url'):
            messagebox.showerror("错误", "周报API地址未设置")
            return

        api_url = self.report_api_url.get()
        if not api_url:
            messagebox.showerror("错误", "请输入周报API地址")
            return

        self.log_message_console(f"测试周报API连接: {api_url}")
        self.status_var.set("正在测试周报API连接...")

        # 在单独的线程中运行测试
        threading.Thread(target=self._test_report_api_thread, args=(api_url,), daemon=True).start()

    def _test_report_api_thread(self, api_url):
        """测试周报API连接的线程函数"""
        try:
            # 导入API客户端
            from api_client import WeeklyReportClient

            # 创建客户端
            client = WeeklyReportClient(base_url=api_url)

            # 测试连接
            if client.health_check():
                # 获取模板列表
                templates = client.get_templates()
                template_count = len(templates) if templates else 0

                # 更新配置
                self.config["report_api_url"] = api_url
                try:
                    import config
                    config.save_config(self.config)
                except ImportError:
                    pass

                # 显示成功消息
                success_msg = f"连接成功! 找到 {template_count} 个模板"
                self.root.after(0, lambda: messagebox.showinfo("测试成功", success_msg))
                self.root.after(0, lambda: self.status_var.set("周报API连接成功"))

                # 如果周报生成标签页存在，更新API地址
                if hasattr(self, 'weekly_report_frame') and self.weekly_report_frame is not None:
                    self.weekly_report_frame.api_client.base_url = api_url
                    self.root.after(0, lambda: self.weekly_report_frame.check_service_status())
            else:
                # 显示失败消息
                self.root.after(0, lambda: messagebox.showerror("测试失败", "无法连接到周报API服务"))
                self.root.after(0, lambda: self.status_var.set("周报API连接失败"))
        except Exception as e:
            error_msg = f"测试周报API连接时出错: {str(e)}"
            self.log_message_console(error_msg)
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.root.after(0, lambda: self.status_var.set("周报API连接失败"))

    def save_current_config(self):
        """保存当前配置"""
        try:
            import config

            # 更新配置
            self.config["db_dir"] = self.db_dir.get()
            self.config["db_version"] = self.db_version.get()
            self.config["output_dir"] = self.output_dir.get()
            if hasattr(self, 'format_combobox') and self.format_combobox.get():
                self.config["last_export_format"] = self.format_combobox.get()

            # 如果周报API地址已设置，也保存它
            if hasattr(self, 'report_api_url'):
                self.config["report_api_url"] = self.report_api_url.get()

            # 保存配置
            config.save_config(self.config)
            self.log_message_console("配置已保存")
            messagebox.showinfo("提示", "设置已保存")
        except ImportError:
            self.log_message_console("未找到配置模块，无法保存配置")
            messagebox.showerror("错误", "无法保存配置：未找到配置模块")
        except Exception as e:
            self.log_message_console(f"保存配置出错: {str(e)}")
            messagebox.showerror("错误", f"保存配置失败: {str(e)}")
            traceback.print_exc()

    def _update_weekly_report_tab(self):
        """更新周报生成标签页的数据库连接"""
        if hasattr(self, 'weekly_report_frame') and self.weekly_report_frame is not None:
            self.weekly_report_frame.database = self.database
            self.log_message_console("已更新周报生成标签页的数据库连接")

            # 更新配置
            self.weekly_report_frame.config = {
                "report_api_url": self.config.get("report_api_url", "http://localhost:8000"),
                "output_dir": self.config.get("output_dir", "./data/")
            }

            # 检查服务状态
            if hasattr(self.weekly_report_frame, 'check_service_status'):
                self.weekly_report_frame.check_service_status()

    def log_message_console(self, message):
        """直接在控制台输出日志，不使用GUI组件"""
        print(f"[WeChat Export] {message}")


if __name__ == "__main__":
    freeze_support()  # Required for multiprocessing
    root = tk.Tk()
    app = WeChatExportGUI(root)
    root.mainloop()
