# -*- coding: utf-8 -*-
"""merge —— 配置默认值模型（纯字典，直接透传给 conversion.convert 的 config 参数）。

merge 不自己实现格式引擎，因此不需要完整 dataclass 配置类；只需提供与
conversion 侧 `_pick_config().from_dict(...)` 兼容的默认字典，供 UI 面板编辑、
摘要展示与透传。预设采用「存名→存字典」的命名预设机制，落盘到 deps/<id>/.cache。
"""
import json
import os


def default_docx_layout():
    """MD → DOCX 排版默认（对齐 conversion FormatConfig 字段）"""
    return {
        "page": {
            "paper_size": "A4", "orientation": "portrait",
            "margin_top": 2.54, "margin_bottom": 2.54,
            "margin_left": 3.18, "margin_right": 3.18, "gutter": 0.0,
        },
        "typography": {
            "body_font_cn": "宋体", "body_font_en": "Times New Roman", "body_font_size": 12.0,
            "headings": {
                "h1": {"font_cn": "黑体", "font_en": "Arial", "font_size": 22.0, "bold": True},
                "h2": {"font_cn": "黑体", "font_en": "Arial", "font_size": 18.0, "bold": True},
                "h3": {"font_cn": "黑体", "font_en": "Arial", "font_size": 14.0, "bold": True},
            },
            "line_spacing_mode": "multiple", "line_spacing": 1.5,
            "first_line_indent_chars": 2.0, "alignment": "left",
        },
        "content": {"code_font": "Consolas"},
        "advanced": {
            "heading_numbering": False, "heading_numbering_format": "1.",
            "auto_toc": False, "toc_depth": 3,
            "header_text": "", "footer_text": "",
            "cover_enabled": False, "cover_title": "", "cover_author": "", "cover_date": "",
            "watermark_text": "", "watermark_font_size": 72.0, "watermark_opacity": 0.1,
        },
    }


def default_tts():
    """TTS 语音合成默认（对齐 conversion config）"""
    return {
        "engine": "kokoro",
        "edge_voice": "zh-CN-XiaoxiaoNeural", "edge_rate": "+0%",
        "edge_volume": "+0%", "edge_pitch": "+0Hz",
        "kokoro_lang": "z", "kokoro_voice": "zf_xiaoxiao", "kokoro_speed": 1.0,
        "moss_model_dir": "", "moss_voice": "", "moss_reference": "",
    }


def default_stt():
    """STT 语音识别默认（对齐 conversion config）"""
    return {"language": "auto", "device": "cpu", "use_itn": True}


def default_image_docx():
    """图片 → DOCX 排版默认（对齐 conversion config）"""
    return {"images_per_row": 2}


def default_ocr():
    return {"lang": "ch"}


DEFAULTS = {
    "docx": default_docx_layout,
    "tts": default_tts,
    "stt": default_stt,
    "img": default_image_docx,
    "ocr": default_ocr,
}


# ═══════════════════════════════════
#  命名预设存储（deps/<id>/.cache）
# ═══════════════════════════════════

def _cache_dir():
    # 使用 merge 插件目录同级 .cache（deploy 后以实际运行目录为准）
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(os.path.dirname(here)), ".cache", "presets")


def _file_for(kind):
    return os.path.join(_cache_dir(), f"{kind}.json")


def list_presets(kind):
    """返回命名预设名列表（不含内置默认）"""
    p = _file_for(kind)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return list(json.load(f).keys())
    except Exception:
        return []


def load_preset(kind, name):
    p = _file_for(kind)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get(name)
    except Exception:
        return None


def save_preset(kind, name, config_dict):
    d = os.path.dirname(_file_for(kind))
    os.makedirs(d, exist_ok=True)
    data = {}
    if os.path.exists(_file_for(kind)):
        try:
            with open(_file_for(kind), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[name] = config_dict
    with open(_file_for(kind), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_preset(kind, name):
    p = _file_for(kind)
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    data.pop(name, None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)