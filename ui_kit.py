#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ui_kit.py —— 微信记录导出工具的界面组件库

提供：
  * 一套暗色主题调色板与字体
  * 圆角绘制、渐变绘制等 Canvas 辅助函数
  * PillButton（圆角渐变按钮，支持 hover/press/disabled）
  * Card（带描边的卡片容器）
  * SearchBox（圆角搜索框）
  * TabBar（胶囊式标签栏，API 兼容 ttk.Notebook 的 add()）
  * VirtualList（虚拟滚动列表：只渲染可视行，500+ 联系人也不掉帧）

设计原则：所有控件都只用 tkinter 原生能力，不引入额外依赖。
"""

import bisect
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- 调色板

COLORS = {
    # 浅色灰白主题（GitHub Light 风格）：白底灰层，文字深灰，清晰易读
    "bg": "#F6F8FA",          # 应用底色（浅灰白）
    "bg_soft": "#F0F3F6",     # 次级底色
    "surface": "#FFFFFF",     # 卡片/列表表面（纯白）
    "surface2": "#F6F8FA",    # 输入框、次级表面
    "surface3": "#EAEEF2",    # hover
    "border": "#D0D7DE",      # 描边（浅灰）
    "border_soft": "#E4E9EE",
    "text": "#1F2328",        # 主文字（深灰黑）
    "text_dim": "#424A53",
    "muted": "#6E7781",       # 次要文字
    "faint": "#8C959F",
    # 单一强调色：绿色，浅色主题下更醒目清晰
    "accent": "#2DA44E",      # 主强调（GitHub 绿）
    "accent2": "#2DA44E",
    "accent_soft": "#E6F4EC", # 选中底色（浅绿）
    "green": "#2DA44E",
    "amber": "#BF8700",       # 状态提示（深琥珀，浅底清晰）
    "red": "#CF222E",         # 错误提示
    "blue": "#0969DA",        # 链接/信息
}

FONTS = {
    "display": ("Microsoft YaHei UI", 17, "bold"),
    "h1": ("Microsoft YaHei UI", 12, "bold"),
    "h2": ("Microsoft YaHei UI", 10, "bold"),
    "body": ("Microsoft YaHei UI", 10),
    "small": ("Microsoft YaHei UI", 9),
    "tiny": ("Microsoft YaHei UI", 8),
    "mono": ("Consolas", 9),
}

# ---------------------------------------------------------------- 字体探测

# 中文字体候选（按美观度排序）：优先 Noto Sans SC（思源黑体），
# 缺字重时回退到微软雅黑。运行时探测，保证换机器也不崩。
_CJK_CANDIDATES = [
    "Noto Sans SC",       # 思源黑体，现代、字重全、观感最佳
    "Microsoft YaHei UI",
    "微软雅黑",
    "PingFang SC",        # macOS
    "苹方-简",
    "Segoe UI",
]

_MONO_CANDIDATES = [
    "Cascadia Code",      # 现代等宽，日志/代码好看
    "JetBrains Mono",
    "Consolas",
    "Courier New",
]

# 字重映射：Regular 用基名，Light/DemiLight/Medium/Black 用带后缀的名字
# tkinter 的 font 命名：'Noto Sans SC Light' 等。若后缀不存在则退回基名。
_CJK_WEIGHTS = {
    "display": "Medium",   # 大标题用 Medium（比 Bold 更细腻）
    "h1": "Medium",
    "h2": "Medium",
    "body": "",            # Regular
    "small": "",
    "tiny": "",
    "light": "Light",
}


def _pick_family(candidates, available):
    """返回 (family, has_weights) —— 优先选有完整字重的字体"""
    avail = set(available)
    for fam in candidates:
        if fam in avail:
            return fam
    # 全部没装时，退到 Tk 默认
    return "TkDefaultFont"


def _resolve_fonts(root):
    """运行时探测可用字体，生成 FONTS 表"""
    from tkinter import font as tkfont
    available = set(tkfont.families(root))

    cjk = _pick_family(_CJK_CANDIDATES, available)
    mono = _pick_family(_MONO_CANDIDATES, available)

    def family_with_weight(weight):
        """'Noto Sans SC' + 'Medium' -> 'Noto Sans SC Medium'（存在则用，否则基名）"""
        if not weight:
            return cjk
        named = f"{cjk} {weight}"
        return named if named in available else cjk

    # bold 用 tkinter 的 'bold' 属性对 Medium/Regular 加粗
    # 注意：Noto Sans SC 的 Medium 字重本身已足够醒目，叠加 bold 会发糊，
    # 因此标题用 Medium 不加 bold，需要强调的正文用 Medium。
    return {
        "display": (family_with_weight(_CJK_WEIGHTS["display"]), 17),
        "h1": (family_with_weight(_CJK_WEIGHTS["h1"]), 12),
        "h2": (family_with_weight(_CJK_WEIGHTS["h2"]), 10),
        "body": (family_with_weight(_CJK_WEIGHTS["body"]), 10),
        "small": (family_with_weight(_CJK_WEIGHTS["small"]), 9),
        "tiny": (family_with_weight(_CJK_WEIGHTS["tiny"]), 9),
        "mono": (mono, 9),
        # 供个别地方直接取的字面名
        "cjk": cjk,
        "mono_name": mono,
    }


# ---------------------------------------------------------------- 绘制辅助


def round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上画圆角矩形（用平滑多边形近似），返回 item id"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def lerp_color(c1, c2, t):
    """两个 #RRGGBB 之间线性插值"""
    t = max(0.0, min(1.0, t))
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)
    )


def draw_gradient(canvas, x1, y1, x2, y2, c1, c2, steps=64, horizontal=False):
    """用横向细条模拟线性渐变"""
    span = (x2 - x1) if horizontal else (y2 - y1)
    steps = max(2, min(steps, int(span) or 2))
    for i in range(steps):
        t = i / (steps - 1)
        color = lerp_color(c1, c2, t)
        if horizontal:
            xs = x1 + span * i / steps
            xe = x1 + span * (i + 1) / steps + 1
            canvas.create_rectangle(xs, y1, xe, y2, fill=color, outline=color)
        else:
            ys = y1 + span * i / steps
            ye = y1 + span * (i + 1) / steps + 1
            canvas.create_rectangle(x1, ys, x2, ye, fill=color, outline=color)


def accent_gradient(canvas, x1, y1, x2, y2, steps=64, horizontal=True):
    draw_gradient(canvas, x1, y1, x2, y2, COLORS["accent"], COLORS["accent2"],
                  steps=steps, horizontal=horizontal)


# ---------------------------------------------------------------- 主题与 ttk 样式


def setup_ttk_styles(root):
    """把 ttk 控件统一到暗色主题（clam 是唯一能完整改色的内置主题）"""
    # 运行时探测字体，原地更新全局 FONTS，所有引用点自动生效
    resolved = _resolve_fonts(root)
    FONTS.clear()
    FONTS.update(resolved)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    C, F = COLORS, FONTS

    style.configure(".", background=C["bg"], foreground=C["text"], font=F["body"],
                    borderwidth=0, focuscolor=C["accent"])

    # 容器
    style.configure("TFrame", background=C["bg"])
    style.configure("TLabel", background=C["bg"], foreground=C["text"], font=F["body"])
    style.configure("Muted.TLabel", background=C["bg"], foreground=C["muted"], font=F["small"])

    # 兼容旧样式名（其余标签页仍在用）
    for name in ("WeChat.TFrame", "WeChat.Contacts.TFrame"):
        style.configure(name, background=C["bg"])
    style.configure("WeChat.TLabel", background=C["bg"], foreground=C["text"], font=F["body"])
    style.configure("WeChat.TLabelframe", background=C["bg"], borderwidth=0)
    style.configure("WeChat.TLabelframe.Label", background=C["bg"],
                    foreground=C["accent"], font=F["h2"])

    # 按钮
    style.configure("TButton", background=C["surface2"], foreground=C["text"],
                    bordercolor=C["border"], lightcolor=C["surface2"],
                    darkcolor=C["surface2"], relief="flat", padding=(12, 7),
                    font=F["body"])
    style.map("TButton",
              background=[("pressed", C["surface3"]), ("active", C["surface3"]),
                          ("disabled", C["surface"])],
              foreground=[("disabled", C["faint"])])

    style.configure("WeChat.TButton", background=C["accent"], foreground="#FFFFFF",
                    font=F["h2"], padding=(14, 8), relief="flat", borderwidth=0)
    style.map("WeChat.TButton",
              background=[("pressed", lerp_color(C["accent"], "#000000", .25)),
                          ("active", lerp_color(C["accent"], "#ffffff", .12))])

    # 输入框
    style.configure("TEntry", fieldbackground=C["surface2"], foreground=C["text"],
                    bordercolor=C["border"], lightcolor=C["border"],
                    darkcolor=C["border"], insertcolor=C["accent"],
                    padding=(8, 6), relief="flat")
    style.map("TEntry", bordercolor=[("focus", C["accent"])],
              lightcolor=[("focus", C["accent"])], darkcolor=[("focus", C["accent"])])
    style.configure("WeChat.Search.TEntry", fieldbackground=C["surface2"],
                    foreground=C["text"], bordercolor=C["surface2"],
                    lightcolor=C["surface2"], darkcolor=C["surface2"],
                    insertcolor=C["accent"], padding=(6, 6))

    # 下拉框（含弹出列表配色）
    style.configure("TCombobox", fieldbackground=C["surface2"], background=C["surface2"],
                    foreground=C["text"], arrowcolor=C["muted"],
                    bordercolor=C["border"], lightcolor=C["surface2"],
                    darkcolor=C["surface2"], padding=(8, 5), relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", C["surface2"])],
              foreground=[("readonly", C["text"])],
              bordercolor=[("focus", C["accent"])])
    root.option_add("*TCombobox*Listbox.background", C["surface2"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # 数字框
    style.configure("TSpinbox", fieldbackground=C["surface2"], background=C["surface2"],
                    foreground=C["text"], arrowcolor=C["muted"], bordercolor=C["border"],
                    lightcolor=C["surface2"], darkcolor=C["surface2"], padding=(4, 4))
    style.map("TSpinbox", bordercolor=[("focus", C["accent"])])

    # 勾选 / 单选
    style.configure("TCheckbutton", background=C["bg"], foreground=C["text_dim"],
                    indicatorbackground=C["surface2"], indicatorforeground="#FFFFFF",
                    bordercolor=C["border"], focuscolor=C["bg"], font=F["small"])
    style.map("TCheckbutton",
              indicatorbackground=[("selected", C["accent"]), ("active", C["surface3"])],
              foreground=[("selected", C["text"])])
    style.configure("TRadiobutton", background=C["bg"], foreground=C["text_dim"],
                    indicatorbackground=C["surface2"], indicatorforeground="#FFFFFF",
                    bordercolor=C["border"], focuscolor=C["bg"], font=F["small"])
    style.map("TRadiobutton",
              indicatorbackground=[("selected", C["accent"]), ("active", C["surface3"])],
              foreground=[("selected", C["text"])])

    # 进度条
    style.configure("TProgressbar", background=C["accent"], troughcolor=C["surface2"],
                    bordercolor=C["surface2"], lightcolor=C["accent"],
                    darkcolor=C["accent"], thickness=6)

    # 滚动条（细、无箭头、低调滑块）
    for name in ("Vertical.TScrollbar", "Slim.Vertical.TScrollbar"):
        style.configure(name, background="#C0C8D0", troughcolor=C["surface"],
                        bordercolor=C["surface"], arrowcolor=C["surface"],
                        lightcolor="#C0C8D0", darkcolor="#C0C8D0",
                        width=8, relief="flat", borderwidth=0)
        style.map(name, background=[("active", "#9AA4AF"), ("pressed", "#9AA4AF")])
    # 去掉箭头：clam 主题下用空 layout 覆盖默认 Scrollbar 布局
    try:
        style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough",
                      {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb",
                       {"expand": "1", "sticky": "nswe"})]})])
        style.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough",
                      {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb",
                       {"expand": "1", "sticky": "nswe"})]})])
    except tk.TclError:
        pass

    # 分隔条
    style.configure("TSeparator", background=C["border"])

    # 分组框（周报等子模块在用）
    style.configure("TLabelframe", background=C["bg"], bordercolor=C["border"],
                    lightcolor=C["border"], darkcolor=C["border"], relief="solid",
                    borderwidth=1)
    style.configure("TLabelframe.Label", background=C["bg"], foreground=C["accent"],
                    font=F["h2"])
    style.configure("TPanedwindow", background=C["bg"])
    style.configure("Sash", background=C["border"], gripcount=0)

    return style


def theme_tree(widget, depth=0):
    """递归把子模块里写死颜色的原生控件刷成当前主题色（ttk 控件由 style 控制）"""
    if depth > 12:
        return
    C = COLORS
    for child in widget.winfo_children():
        cls = child.winfo_class()
        try:
            if cls in ("Frame", "Toplevel"):
                child.configure(bg=C["bg"])
            elif cls == "Label":
                child.configure(bg=C["bg"], fg=C["text_dim"])
            elif cls == "Text":
                child.configure(bg=C["surface2"], fg=C["text_dim"],
                                insertbackground=C["accent"], borderwidth=0,
                                highlightthickness=0)
            elif cls == "Listbox":
                child.configure(bg=C["surface2"], fg=C["text_dim"],
                                selectbackground=C["surface3"],
                                selectforeground=C["text"], borderwidth=0,
                                highlightthickness=0)
            elif cls == "Canvas":
                child.configure(bg=C["bg"], highlightthickness=0)
            elif cls == "Entry":
                child.configure(bg=C["surface2"], fg=C["text"],
                                insertbackground=C["accent"], borderwidth=0)
        except tk.TclError:
            pass
        theme_tree(child, depth + 1)


def apply_dark_titlebar(root):
    """把 Windows 原生标题栏改成浅色，与浅色主题协调"""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        zero = ctypes.c_int(0)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win11），19 = 旧版本；设 0 = 浅色
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(zero), ctypes.sizeof(zero))
        # 35 = DWMWA_CAPTION_COLOR（仅 Win11 生效），0x00BBGGRR → 白
        color = ctypes.c_int(0x00FFFFFF)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


# ---------------------------------------------------------------- 圆角按钮


class PillButton(tk.Canvas):
    """圆角按钮，兼容 ttk 的 state/text 配置方式

    用法：
        btn = PillButton(parent, text="开始导出", command=fn, kind="primary")
        btn.config(state=tk.DISABLED, text="导出中...")
    """

    VARIANTS = {
        "primary": ("#2DA44E", "#2DA44E", "#FFFFFF"),
        "ghost": (None, None, None),
        "danger": ("#CF222E", "#CF222E", "#FFFFFF"),
        "tab": ("#2DA44E", "#2DA44E", "#FFFFFF"),  # 标签选中态
    }

    def __init__(self, master, text="", command=None, kind="ghost", width=None,
                 height=34, radius=None, font=None, bg=None, icon=""):
        self._bg = bg or COLORS["bg"]
        super().__init__(master, height=height, bg=self._bg,
                         highlightthickness=0, bd=0)
        self._text = text
        self._icon = icon
        self._command = command
        self._kind = kind
        self._radius = radius if radius is not None else min(height // 2, 12)
        self._font = font or FONTS["body"]
        self._state = tk.NORMAL
        self._hover = False
        self._pressed = False
        # Canvas 默认宽 378px，会撑爆布局：未指定宽度时按文字长度估算
        self._fixed_width = width
        if width:
            self.configure(width=width)
        else:
            self.configure(width=max(72, 14 * len(self._text) + 26))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._redraw())

    # -- 兼容 ttk 写法
    def config(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "bg" in kwargs:
            bg = kwargs.pop("bg")
            self._bg = bg
            super().config(bg=bg)
        if kwargs:
            super().config(**kwargs)
        self._redraw()

    configure = config

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    # -- 内部
    def _on_enter(self, _e):
        if self._state == tk.DISABLED:
            return
        self._hover = True
        self.configure(cursor="hand2")
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _e):
        if self._state == tk.DISABLED:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _e):
        if self._state == tk.DISABLED or not self._pressed:
            self._pressed = False
            return
        self._pressed = False
        self._redraw()
        if callable(self._command):
            self._command()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        C = COLORS
        disabled = self._state == tk.DISABLED
        c1, c2, fg = self.VARIANTS.get(self._kind, (None, None, None))

        if disabled:
            fill = C["surface"]
            fg = C["faint"]
            round_rect(self, 1, 1, w - 1, h - 1, self._radius,
                       fill=fill, outline=C["border_soft"])
        elif self._kind in ("primary", "tab"):
            # 纯色强调，不用渐变；hover 微亮、按下微暗
            base = c1
            if self._hover:
                base = lerp_color(c1, "#ffffff", .08)
            if self._pressed:
                base = lerp_color(c1, "#000000", .14)
            round_rect(self, 1, 1, w - 1, h - 1, self._radius,
                       fill=base, outline="")
        else:
            fill = C["surface3"] if (self._hover or self._pressed) else C["surface2"]
            outline = C["surface3"] if self._hover else C["border"]
            round_rect(self, 1, 1, w - 1, h - 1, self._radius,
                       fill=fill, outline=outline)

        label = (self._icon + " " + self._text).strip() if self._icon else self._text
        self.create_text(w / 2, h / 2 + 1, text=label, fill=fg, font=self._font)


# ---------------------------------------------------------------- 卡片


class Card(tk.Frame):
    """带 1px 描边与内边距的暗色卡片，可带标题行"""

    def __init__(self, master, title=None, subtitle=None, pad=12, bg=None,
                 accent_left=False):
        self._outer = bg or COLORS["bg"]
        super().__init__(master, bg=COLORS["surface"], highlightthickness=1,
                         highlightbackground=COLORS["border"],
                         highlightcolor=COLORS["border"], bd=0)
        self.body = tk.Frame(self, bg=COLORS["surface"])
        self.body.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        if title:
            head = tk.Frame(self.body, bg=COLORS["surface"])
            head.pack(fill=tk.X, pady=(0, 8))
            tk.Label(head, text=title, bg=COLORS["surface"], fg=COLORS["text"],
                     font=FONTS["h1"]).pack(side=tk.LEFT)
            if subtitle:
                tk.Label(head, text=subtitle, bg=COLORS["surface"],
                         fg=COLORS["muted"], font=FONTS["small"]).pack(
                    side=tk.LEFT, padx=8)

        # 内容区：需要 grid 布局的调用方用 .content，避免与标题的 pack 冲突
        self.content = tk.Frame(self.body, bg=COLORS["surface"])
        self.content.pack(fill=tk.BOTH, expand=True)


# ---------------------------------------------------------------- 搜索框


class SearchBox(tk.Frame):
    """圆角搜索框：图标 + 输入 + 清除按钮"""

    def __init__(self, master, textvariable, placeholder="搜索昵称 / 备注 / wxid",
                 height=36):
        super().__init__(master, bg=COLORS["surface"], height=height)
        self.pack_propagate(False)
        self._var = textvariable
        self._placeholder = placeholder
        self._canvas = tk.Canvas(self, bg=COLORS["surface"], highlightthickness=0, bd=0,
                                 height=height)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self.entry = tk.Entry(self._canvas, textvariable=textvariable, bd=0,
                              relief=tk.FLAT, bg=COLORS["surface2"],
                              fg=COLORS["text"], insertbackground=COLORS["accent"],
                              font=FONTS["body"], highlightthickness=0)
        self._placeholder_label = tk.Label(self._canvas, text=placeholder,
                                           bg=COLORS["surface2"], fg=COLORS["faint"],
                                           font=FONTS["small"])
        self._clear = tk.Label(self._canvas, text="✕", bg=COLORS["surface2"],
                               fg=COLORS["muted"], font=FONTS["small"], cursor="hand2")
        self._clear.bind("<Button-1>", self._on_clear)
        self._clear.bind("<Enter>", lambda e: self._clear.config(fg=COLORS["text"]))
        self._clear.bind("<Leave>", lambda e: self._clear.config(fg=COLORS["muted"]))

        self._canvas.bind("<Configure>", self._layout)
        self.entry.bind("<FocusIn>", lambda e: self._layout())
        self.entry.bind("<FocusOut>", lambda e: self._layout())
        textvariable.trace_add("write", lambda *a: self._layout())
        self.entry.bind("<Escape>", lambda e: self._var.set(""))

    def _on_clear(self, _e):
        self._var.set("")
        self.entry.focus_set()

    def _layout(self, _e=None):
        c = self._canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 1:
            return
        focused = c.focus_get() is self.entry
        round_rect(c, 1, 1, w - 1, h - 1, h // 2 - 1,
                   fill=COLORS["surface2"],
                   outline=COLORS["accent"] if focused else COLORS["border"])
        # 图标
        c.create_text(16, h / 2, text="⌕", fill=COLORS["muted"],
                      font=("Segoe UI", 13))
        self.entry.place(x=32, y=(h - self.entry.winfo_reqheight()) / 2,
                        width=max(20, w - 70))
        empty = not self._var.get()
        if empty and c.focus_get() is not self.entry:
            self._placeholder_label.place(x=34, y=(h - 16) / 2)
        else:
            self._placeholder_label.place_forget()
        if not empty:
            self._clear.place(x=w - 24, y=(h - 16) / 2)
        else:
            self._clear.place_forget()


# ---------------------------------------------------------------- 日期选择


class DateField(ttk.Frame):
    """轻量日期输入：文本框 + 日历按钮

    tkcalendar 的 DateEntry 每次实例化都要重建整套 ttk 样式（单次约 0.4s），
    这里改成点开时才创建 Calendar，启动零开销。API 与 DateEntry 兼容。
    """

    def __init__(self, master, year=2020, month=1, day=1, width=12, bg=None):
        super().__init__(master, style="TFrame")
        self._bg = bg or COLORS["surface"]
        self._var = tk.StringVar(value=f"{year:04d}-{month:02d}-{day:02d}")
        self.entry = ttk.Entry(self, textvariable=self._var, width=width)
        self.entry.pack(side=tk.LEFT)
        self._btn = PillButton(self, text="日历", height=29, width=52,
                               bg=self._bg, font=FONTS["small"],
                               command=self._open)
        self._btn.pack(side=tk.LEFT, padx=(5, 0))
        self._popup = None
        self._calendar = None

    def get(self):
        return self._var.get()

    def set(self, value):
        self._var.set(value)

    def get_date(self):
        import datetime
        text = self._var.get().strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return datetime.date.today()

    def _open(self):
        import datetime
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.lift()
            return
        try:
            from tkcalendar import Calendar
        except ImportError:
            return

        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=COLORS["border"])
        top = self._popup

        current = self.get_date()
        self._calendar = Calendar(
            top, selectmode="day", year=current.year, month=current.month,
            day=current.day, date_pattern="yyyy-mm-dd",
            background=COLORS["surface2"], foreground=COLORS["text"],
            headersbackground=COLORS["surface3"], headersforeground=COLORS["text"],
            normalbackground=COLORS["surface2"], normalforeground=COLORS["text"],
            weekendbackground=COLORS["surface2"], weekendforeground=COLORS["accent"],
            othermonthbackground=COLORS["surface"], othermonthforeground=COLORS["faint"],
            selectbackground=COLORS["accent"], selectforeground="#FFFFFF",
            bordercolor=COLORS["border"], font=FONTS["small"])
        self._calendar.pack(padx=1, pady=1)

        def apply(_e=None):
            self._var.set(self._calendar.get_date())
            close()

        def close(_e=None):
            try:
                top.destroy()
            except tk.TclError:
                pass
            self._popup = None

        self._calendar.bind("<<CalendarSelected>>", apply)
        top.bind("<FocusOut>", close)
        top.bind("<Escape>", close)

        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        top.geometry(f"+{x}+{y}")
        top.lift()
        self._calendar.focus_set()


# ---------------------------------------------------------------- 标签栏


class TabBar(tk.Frame):
    """胶囊式顶部标签栏，API 与 ttk.Notebook 的 add() 保持兼容"""

    def __init__(self, master, on_change=None):
        super().__init__(master, bg=COLORS["bg"])
        self._on_change = on_change
        self._tabs = []          # [(frame, title, btn)]
        self._current = None

        self._strip = tk.Frame(self, bg=COLORS["bg"])
        self._strip.pack(fill=tk.X, padx=22, pady=(0, 8))
        self._container = tk.Frame(self, bg=COLORS["bg"])
        self._container.pack(fill=tk.BOTH, expand=True)

        # 底部细分隔线
        line = tk.Frame(self, bg=COLORS["border_soft"], height=1)
        line.pack(fill=tk.X, side=tk.BOTTOM)

    def add(self, frame, text=""):
        try:
            frame.configure(bg=COLORS["bg"])
        except tk.TclError:
            pass  # ttk.Frame 不接受 -bg
        btn = PillButton(self._strip, text=text, kind="ghost", height=32,
                         font=FONTS["body"],
                         command=lambda f=frame: self.select(f))
        btn.pack(side=tk.LEFT, padx=(0, 6))
        self._tabs.append((frame, text, btn))
        if self._current is None:
            self.select(frame)

    def select(self, frame):
        if self._current is frame:
            return
        for f, _t, _b in self._tabs:
            f.pack_forget()
        self._current = frame
        frame.pack(in_=self._container, fill=tk.BOTH, expand=True)
        for f, _t, btn in self._tabs:
            if f is frame:
                btn._kind = "tab"
                btn._font = FONTS["h2"]
            else:
                btn._kind = "ghost"
                btn._font = FONTS["body"]
            btn.configure(height=32)
            btn._redraw()
        if callable(self._on_change):
            self._on_change(frame)


# ---------------------------------------------------------------- 虚拟滚动列表


class VirtualList(tk.Frame):
    """只渲染可视区域的滚动列表

    行模型：{"kind": "header"|"item", ...}
      header: {"kind":"header", "title":str, "count":int}
      item  : {"kind":"item", "contact":obj, "tag":str, "badge":str}

    通过 avatar_hook(wxid, row_height) 请求头像，拿到 PhotoImage 后调用
    set_avatar(wxid, photo) 即可自动刷新可见行。
    """

    ROW_H = 56
    HEADER_H = 32

    def __init__(self, master, on_select=None, avatar_hook=None,
                 empty_text="没有匹配的联系人"):
        super().__init__(master, bg=COLORS["surface"])
        self._on_select = on_select
        self._avatar_hook = avatar_hook
        self._empty_text = empty_text

        self.canvas = tk.Canvas(self, bg=COLORS["surface"], highlightthickness=0,
                                bd=0, yscrollincrement=1)
        self.vsb = ttk.Scrollbar(self, orient=tk.VERTICAL,
                                 style="Slim.Vertical.TScrollbar",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._rows = []
        self._offsets = [0]
        self._items = {}
        self._avatar_cache = {}
        self._avatar_pending = set()
        self._selected = None
        self._hover = None
        self._render_job = None
        self._wheel_bound = False

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._scroll_px(-60))
        self.canvas.bind("<Button-5>", lambda e: self._scroll_px(60))
        self.canvas.bind("<Home>", lambda e: self.canvas.yview_moveto(0))
        self.canvas.bind("<End>", lambda e: self.canvas.yview_moveto(1))
        self.canvas.bind("<Prior>", lambda e: self._scroll_px(-self.canvas.winfo_height()))
        self.canvas.bind("<Next>", lambda e: self._scroll_px(self.canvas.winfo_height()))
        self.canvas.bind("<Up>", lambda e: self._scroll_px(-self.ROW_H))
        self.canvas.bind("<Down>", lambda e: self._scroll_px(self.ROW_H))
        self.canvas.bind("<KeyPress>", self._on_key)

    # ------------------------------------------------ 数据
    def set_rows(self, rows, keep_position=False):
        yview = self.canvas.yview() if keep_position else (0, 0)
        self.canvas.delete("all")
        self._items.clear()
        self._rows = rows
        offsets = [0]
        total = 0
        for r in rows:
            total += self.HEADER_H if r.get("kind") == "header" else self.ROW_H
            offsets.append(total)
        self._offsets = offsets
        self._render_all()
        try:
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width() or 1, total))
        except tk.TclError:
            pass
        if keep_position:
            self.canvas.yview_moveto(yview[0])
        self._schedule_render()

    def clear_selection(self):
        self._selected = None
        self._render_all()

    def set_avatar(self, wxid, photo):
        self._avatar_cache[wxid] = photo
        self._avatar_pending.discard(wxid)
        self._refresh_visible_avatars()

    # ------------------------------------------------ 滚动
    def _bind_wheel(self, _e):
        if not self._wheel_bound:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
            self._wheel_bound = True

    def _on_wheel(self, event):
        # 高精度滚轮：按像素滚动，触控板自然平滑
        step = int(-event.delta / 120 * 56) or (-56 if event.delta > 0 else 56)
        self._scroll_px(step)

    def _scroll_px(self, dy):
        before = self.canvas.canvasy(0)
        total = self._offsets[-1] if self._offsets else 0
        view_h = self.canvas.winfo_height()
        if total <= view_h:
            return
        target = max(0, min(total - view_h, before + dy))
        if target == before:
            return
        self.canvas.yview_moveto(target / total)

    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)
        self._schedule_render()

    def _on_configure(self, _e):
        self.canvas.configure(scrollregion=(
            0, 0, self.canvas.winfo_width(), self._offsets[-1] if self._offsets else 1))
        self._schedule_render()

    def _schedule_render(self):
        if self._render_job is None:
            self._render_job = self.after(0, self._render_all)

    # ------------------------------------------------ 渲染
    def _visible_range(self):
        total = self._offsets[-1] if self._offsets else 0
        if total <= 0:
            return 0, -1
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height() or 1
        first = max(0, bisect.bisect_right(self._offsets, top) - 1)
        last = min(len(self._rows) - 1,
                   bisect.bisect_right(self._offsets, top + height) - 1)
        return first, last

    def _render_all(self):
        self._render_job = None
        if not self.winfo_exists():
            return
        c = self.canvas
        w = c.winfo_width()
        if w <= 1:
            return
        C, F = COLORS, FONTS

        if not self._rows:
            c.delete("all")
            c.create_text(w / 2, 60, text=self._empty_text, fill=C["muted"],
                          font=F["body"])
            return

        first, last = self._visible_range()
        # 回收超出窗口的行
        for idx in [i for i in self._items if i < first - 1 or i > last + 1]:
            for item in self._items.pop(idx):
                c.delete(item)
        if first < 0 or last < 0:
            return

        for idx in range(max(0, first - 1), min(len(self._rows), last + 2)):
            if idx in self._items:
                continue
            self._draw_row(idx, w)

    def _draw_row(self, idx, width):
        c = self.canvas
        row = self._rows[idx]
        y0 = self._offsets[idx]
        items = []
        C, F = COLORS, FONTS

        if row.get("kind") == "header":
            h = self.HEADER_H
            items.append(c.create_text(14, y0 + h / 2, text=row["title"],
                                       fill=C["accent"], font=F["h2"], anchor="w"))
            if row.get("count") is not None:
                items.append(c.create_text(22 + 9 * len(str(row["title"])),
                                           y0 + h / 2, text=str(row["count"]),
                                           fill=C["muted"], font=F["tiny"], anchor="w"))
            items.append(c.create_line(12, y0 + h - 1, width - 8, y0 + h - 1,
                                       fill=C["border_soft"]))
        else:
            contact = row["contact"]
            h = self.ROW_H
            selected = self._selected == idx
            hovered = self._hover == idx
            bg = C["accent_soft"] if selected else (C["surface3"] if hovered else C["surface"])
            items.append(c.create_rectangle(0, y0, width, y0 + h - 2,
                                            fill=bg, outline=""))
            if selected:
                items.append(c.create_rectangle(0, y0 + 4, 3, y0 + h - 6,
                                                fill=C["accent"], outline=""))

            # 头像
            ax, ay = 18, y0 + (h - 36) / 2
            wxid = getattr(contact, "wxid", "")
            photo = self._avatar_cache.get(wxid)
            if photo:
                items.append(c.create_image(ax + 18, ay + 18, image=photo))
                c.image = photo
            else:
                color = self._placeholder_color(wxid)
                items.append(c.create_oval(ax, ay, ax + 36, ay + 36,
                                           fill=color, outline=""))
                nick = getattr(contact, "remark", "") or getattr(contact, "nickname", "") or "?"
                items.append(c.create_text(ax + 18, ay + 19, text=nick[:1],
                                           fill="#57606A", font=F["h2"]))
                if self._avatar_hook and wxid and wxid not in self._avatar_pending:
                    self._avatar_pending.add(wxid)
                    self._avatar_hook(wxid)

            # 文本
            tx = 66
            nickname = getattr(contact, "nickname", "") or ""
            remark = getattr(contact, "remark", "") or ""
            if remark and nickname:
                display = f"{remark}（{nickname}）"
            else:
                display = remark or nickname or "未知联系人"
            items.append(c.create_text(tx, y0 + h / 2 - 9, text=self._ellipsis(display, 30),
                                       fill=C["text"], font=F["body"], anchor="w"))

            tag = row.get("tag", "好友")
            sub = getattr(contact, "wxid", "") or ""
            if len(sub) > 26:
                sub = sub[:26] + "…"
            items.append(c.create_text(tx, y0 + h / 2 + 10, text=f"{tag} · {sub}",
                                       fill=C["muted"], font=F["tiny"], anchor="w"))

            badge = row.get("badge")
            if badge:
                bw = 10 + 7 * len(badge)
                items.append(round_rect(c, width - 20 - bw, y0 + h / 2 - 10,
                                        width - 20, y0 + h / 2 + 10, 9,
                                        fill=C["surface2"], outline=C["border"]))
                items.append(c.create_text(width - 20 - bw / 2, y0 + h / 2,
                                           text=badge, fill=C["muted"], font=F["tiny"]))

        # 整行点击/悬浮热区放在最底层（透明，不描边）
        hit = c.create_rectangle(0, y0, width, y0 + (h - 2), fill="", outline="")
        items.insert(0, hit)
        self._items[idx] = items

    def _refresh_visible_avatars(self):
        """头像到达后只重画可见行，避免整表重绘"""
        first, last = self._visible_range()
        for idx in list(self._items.keys()):
            if idx < first - 1 or idx > last + 1:
                continue
            for item in self._items.pop(idx, []):
                self.canvas.delete(item)
        self._render_all()

    @staticmethod
    def _ellipsis(text, limit):
        return text if len(text) <= limit else text[:limit] + "…"

    @staticmethod
    def _placeholder_color(seed):
        # 统一浅灰，浅色主题下柔和但可见
        return "#DDE4EA"

    # ------------------------------------------------ 交互
    def _row_at(self, y):
        total = self._offsets[-1] if self._offsets else 0
        if not self._rows or y < 0 or y > total:
            return None
        idx = bisect.bisect_right(self._offsets, y) - 1
        if 0 <= idx < len(self._rows) and self._rows[idx].get("kind") != "header":
            return idx
        return None

    def _on_click(self, event):
        y = self.canvas.canvasy(event.y)
        self.canvas.focus_set()
        self._select_idx(self._row_at(y), redraw=True)

    def _select_idx(self, idx, redraw=True):
        if idx is None or not (0 <= idx < len(self._rows)):
            return
        old = self._selected
        self._selected = idx
        if redraw:
            for i in (old, idx):
                self._redraw_row(i)
        if callable(self._on_select):
            self._on_select(self._rows[idx]["contact"])

    def _on_motion(self, event):
        y = self.canvas.canvasy(event.y)
        idx = self._row_at(y)
        if idx == self._hover:
            return
        old = self._hover
        self._hover = idx
        for i in (old, idx):
            self._redraw_row(i)

    def _on_leave_canvas(self, _e):
        old = self._hover
        self._hover = None
        self._redraw_row(old)

    def _on_key(self, event):
        if event.keysym in ("Return", "space"):
            self._select_idx(self._selected)
            return
        if event.keysym not in ("Up", "Down"):
            return
        step = -1 if event.keysym == "Up" else 1
        base = self._selected if self._selected is not None else -1
        idx = base
        for _ in range(len(self._rows)):
            idx += step
            if idx < 0 or idx >= len(self._rows):
                return
            if self._rows[idx].get("kind") != "header":
                break
        else:
            return
        self._select_idx(idx)
        # 保证选中行可见
        top, bottom = self._offsets[idx], self._offsets[idx + 1]
        view_top = self.canvas.canvasy(0)
        view_h = self.canvas.winfo_height()
        total = self._offsets[-1] or 1
        if top < view_top:
            self.canvas.yview_moveto(top / total)
        elif bottom > view_top + view_h:
            self.canvas.yview_moveto((bottom - view_h) / total)

    def _redraw_row(self, idx):
        if idx is None or idx not in self._items:
            return
        for item in self._items.pop(idx, []):
            self.canvas.delete(item)
        self._draw_row(idx, self.canvas.winfo_width())
