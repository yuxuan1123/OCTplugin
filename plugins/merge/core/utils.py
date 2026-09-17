# -*- coding: utf-8 -*-
"""merge —— 文件文本读写小工具（自包含，不依赖第三方）。

处理 Windows 中文路径/编码，供 same_format_merger 的同格式文本拼接使用。
"""
import os


def read_text(path, encoding="utf-8"):
    with open(path, "r", encoding=encoding, errors="replace") as f:
        return f.read()


def write_text(path, content, encoding="utf-8"):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding=encoding, errors="replace") as f:
        f.write(content)


def safe_name(path):
    base = os.path.basename(path)
    name = os.path.splitext(base)[0]
    return "".join(c for c in name if not (c in '<>:"/\\|?*' or ord(c) < 32)) or "untitled"