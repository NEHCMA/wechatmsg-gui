#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微信 4.x 图片(.dat)解密密钥运行时推导

背景
----
微信 4.0 起，聊天图片缓存为 `<md5>.dat`，采用两段式加密：

    [15B 头] [aes_size u32 LE] [xor_size u32 LE] [1B flag]
    [AES-128-ECB 段 (aes_size + 16)] [XOR 段 (xor_size)]

其中 AES 密钥与 XOR 密钥都来自同一个「账号级 code」：

    xor_key = code & 0xFF
    aes_key = MD5(f"{code}{wxid}")[:16]   # 16 字节 ASCII

`code` 由微信写入 `%APPDATA%\\Tencent\\xwechat\\<模块>\\kvcomm\\` 下的统计文件名中，
形如 `key_<code>_<uid>_..._input.statistic`。

设计原则
--------
**不硬编码任何账号密钥**。本模块在运行时：

1. 从 .dat 文件路径中解析出 wxid（形如 `...\\wxid_xxx_a2e3\\msg\\attach\\...`）
2. 扫描本机 kvcomm 目录，收集候选 code
3. 用每个候选 code 试解 .dat 的 AES 段，校验解密结果是否为合法图片魔数
4. 命中即返回，并缓存

这样换账号、换机器、微信升级后 code 变化都能自动适配，
仓库里也不会留下任何人的密钥。

仅用于导出本机当前登录账号自己的聊天数据。
"""
import hashlib
import os
import re
import struct
from pathlib import Path

# 图片魔数，用于校验解密结果
_IMAGE_MAGICS = (
    b'\xff\xd8\xff',        # JPEG
    b'\x89PNG\r\n\x1a\n',   # PNG
    b'GIF87a', b'GIF89a',   # GIF
    b'BM',                  # BMP
    b'RIFF',                # WEBP
)

# 微信 4.x dat 头标识
V1_SIG = b'\x07\x08V1\x08\x07'
V2_SIG = b'\x07\x08V2\x08\x07'

# 微信 4.0 第一代图片密钥（公开，非账号级）
V1_AES_KEY = b'cfcd208495d565ef'

# 统计文件名里提取 code：key_<code>_... / monitordata_<code>_... / reportnow_<code>_...
_CODE_RE = re.compile(r'^(?:key_|monitordata_|reportnow_)(\d+)_')
# 路径中提取 wxid
_WXID_RE = re.compile(r'^(wxid_[A-Za-z0-9]+)')

# 运行时缓存：{wxid: (code, aes_key, xor_key)}
_KEY_CACHE = {}
# 找不到 wxid 时的兜底（由调用方注入）
_FALLBACK_WXID = None


def set_fallback_wxid(wxid):
    """当 .dat 路径里解析不出 wxid 时使用的兜底账号"""
    global _FALLBACK_WXID
    _FALLBACK_WXID = wxid


def _kvcomm_dirs():
    appdata = os.environ.get('APPDATA') or os.path.expanduser('~/.config')
    base = Path(appdata) / 'Tencent' / 'xwechat'
    if not base.is_dir():
        return []
    return [p for p in base.rglob('kvcomm') if p.is_dir()]


def candidate_codes():
    """收集本机 kvcomm 目录下所有可能的账号级 code（按出现顺序去重）"""
    codes = []
    for d in _kvcomm_dirs():
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            m = _CODE_RE.match(f.name)
            if not m:
                continue
            try:
                code = int(m.group(1))
            except ValueError:
                continue
            if code and code not in codes:
                codes.append(code)
    return codes


def wxid_from_path(path):
    """从 dat 文件路径中解析 wxid"""
    for part in Path(path).parts:
        m = _WXID_RE.match(part)
        if m:
            return m.group(1)
    return None


def derive_aes_key(code, wxid):
    """aes_key = MD5(f'{code}{wxid}')[:16]"""
    return hashlib.md5(f'{code}{wxid}'.encode()).hexdigest()[:16].encode()


def derive_xor_key(code):
    """xor_key = code & 0xFF"""
    return code & 0xFF


def _read_aes_segment(dat_path):
    """读取 dat 的 AES 段与结构信息；返回 (header, aes_segment, xor_size, xor_offset)"""
    with open(dat_path, 'rb') as f:
        head = f.read(15)
        if len(head) < 15:
            return None
        aes_size, xor_size = struct.unpack_from('<II', head, 6)
        segment = f.read(aes_size + 16)
    if len(segment) < 16:
        return None
    if len(segment) % 16:
        segment += b'\x00' * (16 - len(segment) % 16)
    return head, segment, xor_size, 15 + aes_size + 16


def _try_decrypt(aes_key, segment):
    """用 AES-ECB 试解，返回明文或 None"""
    try:
        from Crypto.Cipher import AES
    except ImportError:  # pragma: no cover
        return None
    try:
        plain = AES.new(aes_key, AES.MODE_ECB).decrypt(segment)
    except Exception:
        return None
    pad = plain[-1] if plain else 0
    if 0 < pad <= 16:
        plain = plain[:-pad]
    return plain


def _looks_like_image(data):
    return any(data.startswith(m) for m in _IMAGE_MAGICS)


def resolve_keys(dat_path, wxid=None):
    """
    为一枚 v2 格式 .dat 推导 (aes_key, xor_key, code)。

    失败返回 (None, None, None)。
    """
    wxid = wxid or wxid_from_path(dat_path) or _FALLBACK_WXID
    if not wxid:
        return None, None, None

    cached = _KEY_CACHE.get(wxid)
    parsed = _read_aes_segment(dat_path)
    if parsed is None:
        return None, None, None
    _head, segment, _xor_size, _xor_off = parsed

    # 缓存命中：先验证，验证失败则重新推导（微信升级换 code 时会走到这里）
    if cached:
        code, aes_key, xor_key = cached
        plain = _try_decrypt(aes_key, segment)
        if plain is not None and _looks_like_image(plain):
            return aes_key, xor_key, code

    for code in candidate_codes():
        aes_key = derive_aes_key(code, wxid)
        plain = _try_decrypt(aes_key, segment)
        if plain is not None and _looks_like_image(plain):
            xor_key = derive_xor_key(code)
            _KEY_CACHE[wxid] = (code, aes_key, xor_key)
            return aes_key, xor_key, code

    return None, None, None
