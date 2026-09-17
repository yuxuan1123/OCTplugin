# -*- coding: utf-8 -*-
"""merge —— 拼接格式模型（自包含，1:1 对齐 OCTools 拼接页可用的格式族）。

merge 不加载 conversion 的格式 DAG（避免依赖隔离 + 冷启动 15s 门限），
只维护拼接所需的格式族与可达性判定。所有「转换」动作仍委托 conversion 完成。

格式族（family）：
  - doc    文档类（可作拼接目标）
  - image  图像类（可作源，也能 图片→文档 / 图片→GIF）
  - video  视频（星型枢纽 mp4）
  - audio  音频（星型枢纽 wav）
"""

# 文档类（同格式可自我拼接，也可作为拼接目标）
DOC_FORMATS = [
    "pdf", "docx", "md", "txt", "txt-ocr",
    "html", "xlsx", "csv", "json", "pptx",
]

# 同格式可自我拼接（含 txt-ocr）
MERGEABLE_FORMATS = [
    "pdf", "docx", "md", "txt", "txt-ocr",
    "html", "xlsx", "csv", "json", "pptx",
    "jpg", "jpeg", "png", "bmp", "webp", "tiff",   # 联系表单图
    "gif",                                            # GIF 动画
    "mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "3gp", "ogv",
    "mp3", "aac", "wav", "flac", "ogg", "opus", "wma", "m4a", "amr", "ac3", "aiff",
]

# 图像格式（含 svg/heic 仅作源）
IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "heic", "svg"]

# 视频 / 音频
VIDEO_FORMATS = ["mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "3gp", "ogv"]
AUDIO_FORMATS = ["mp3", "aac", "wav", "flac", "ogg", "opus", "wma", "m4a", "amr", "ac3", "aiff"]

# 全格式（拼接文件选择器 + 分类用）
ALL_FORMATS = (
    ["pdf", "docx", "md", "txt", "txt-ocr", "html", "xlsx", "csv", "json", "pptx",
     "jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "heic", "svg"]
    + VIDEO_FORMATS + AUDIO_FORMATS
)

# 格式显示名（卡片/下拉用）
DISPLAY = {
    "pdf": "PDF 文档", "docx": "Word 文档", "md": "Markdown", "txt": "纯文本",
    "txt-ocr": "OCR 文本", "html": "HTML 网页", "xlsx": "Excel 表格",
    "csv": "CSV 表格", "json": "JSON 数据", "pptx": "PPT 演示",
    "jpg": "JPEG 图片", "jpeg": "JPEG 图片", "png": "PNG 图片",
    "gif": "GIF 动图", "bmp": "BMP 位图", "webp": "WebP 图片",
    "tiff": "TIFF 图片", "heic": "HEIC 图片", "svg": "SVG 矢量图",
    "mp4": "MP4 视频", "avi": "AVI 视频", "mkv": "MKV 视频",
    "mov": "MOV 视频", "webm": "WebM 视频", "flv": "FLV 视频",
    "wmv": "WMV 视频", "3gp": "3GP 视频", "ogv": "OGV 视频",
    "mp3": "MP3 音频", "aac": "AAC 音频", "wav": "WAV 音频",
    "flac": "FLAC 音频", "ogg": "OGG 音频", "opus": "Opus 音频",
    "wma": "WMA 音频", "m4a": "M4A 音频", "amr": "AMR 音频",
    "ac3": "AC3 音频", "aiff": "AIFF 音频",
}

_IDS = set(ALL_FORMATS)


def display(fmt):
    return DISPLAY.get(str(fmt).lstrip(".").lower(), str(fmt))


def family_of(fmt):
    fmt = str(fmt).lstrip(".").lower()
    if fmt in IMAGE_FORMATS:
        return "image"
    if fmt in VIDEO_FORMATS:
        return "video"
    if fmt in AUDIO_FORMATS:
        return "audio"
    return "doc"


def __is_reachable(src, dst):
    """本地方可达性：判断 src 能否经由 conversion 转换为 dst（用于 拼接 src!=dst）。"""
    sf, df = family_of(src), family_of(dst)
    if sf == df:
        return True          # 同族互转（doc 经 conversion 管道）
    if sf == "image" and df in ("doc", "video"):
        return True          # 图片 → 文档 / 视频
    if sf == "video" and df in ("audio",):
        return True          # 视频 → 抽音轨
    return False


def can_concat(src, dst):
    """1:1 对齐 OCTools planner.can_concat。"""
    src = str(src or "").strip().lower().lstrip(".")
    dst = str(dst or "").strip().lower().lstrip(".")
    if dst == "pptx-img":
        return False
    if src in IMAGE_FORMATS and family_of(dst) == "doc":
        return True                               # 图片 → 文档
    if src == dst:
        return dst in MERGEABLE_FORMATS            # 自我拼接
    if dst not in MERGEABLE_FORMATS:
        return False
    return __is_reachable(src, dst)


def reachable_targets(src):
    """按 拼接(concat) 语义返回 src 的全部可拼接目标格式。

    注意：自我拼接（src==dst）是拼接最常用操作，当 src 支持同格式合并时，
    把 src 自身也纳入可达目标（置顶显示）。
    """
    src = src.strip().lower().lstrip(".")
    targets = [f for f in ALL_FORMATS
               if f != src and src in ALL_FORMATS and can_concat(src, f)]
    if src in ALL_FORMATS and can_concat(src, src):
        targets.insert(0, src)
    return targets