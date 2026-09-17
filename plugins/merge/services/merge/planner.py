# -*- coding: utf-8 -*-
"""merge —— 拼接可达性规划（自包含）。

基于 core.formats 的格式族判定，1:1 对齐 OCTools 拼接页能选的「可拼接目标」。
metaxdata（分类/源格式/可达目标）由本模块本地计算，不依赖 conversion 冷启动，
保证 merge.formats 即使在 conversion 尚未热启动时也能即时返回。
"""
from core import formats as FMT


def categories():
    """两级选择器所需分类：[(族名, 图标, [格式id...])] -> [{key,name,formats}]"""
    groups = [
        ("文档", "doc", ["pdf", "docx", "md", "txt", "txt-ocr", "html", "xlsx",
                         "csv", "json", "pptx"]),
        ("图片", "img", ["jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff",
                         "heic", "svg"]),
        ("视频", "vid", FMT.VIDEO_FORMATS),
        ("音频", "aud", FMT.AUDIO_FORMATS),
    ]
    out = []
    for name, key, fmts in groups:
        out.append({
            "key": key,
            "name": name,
            "formats": {
                "values": list(fmts),
                "labels": [FMT.display(f) for f in fmts],
                "icons": [_icon(f) for f in fmts],
            },
        })
    return out


def _icon(f):
    f = f.lstrip(".").lower()
    if f in FMT.IMAGE_FORMATS:
        return "img"
    if f in FMT.VIDEO_FORMATS:
        return "vid"
    if f in FMT.AUDIO_FORMATS:
        return "aud"
    return "doc"


def source_formats():
    return [f for f in FMT.ALL_FORMATS if f != "svg"]  # svg 仅作输入源


def all_formats():
    return list(FMT.ALL_FORMATS)


def reachable_for(src):
    return FMT.reachable_targets(src)


def formats_meta(src=""):
    """merge.formats 的完整返回体（src 为空时只返回元数据）。"""
    src = (src or "").strip().lower()
    return {
        "ok": True,
        "categories": categories(),
        "source_formats": source_formats(),
        "all_formats": all_formats(),
        "reachable": reachable_for(src) if src else [],
    }