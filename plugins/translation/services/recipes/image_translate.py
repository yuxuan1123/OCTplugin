"""
translation/services/recipes/image_translate.py
──────────────────────────────────────────────
拼接配方 · 图翻译 = 图像识别 + 文字翻译（1:1 复刻 OCTools）。
"""

from core.engines.ocr_engine import ocr_pil_image, ocr_image
from core.engines.translation_engine import translate


def recognize(image) -> str:
    """仅识别一张图像，返回识别文本；失败返回空串"""
    try:
        return (ocr_pil_image(image) or "").strip()
    except Exception:
        return ""


def recognize_translate(image, direction: str = "auto", log=None, config=None):
    """识别一张图像并翻译，返回 (识别文本, 译文)"""
    text = recognize(image)
    if not text:
        return "", ""
    try:
        trans = translate(text, direction, log=log, config=config) or ""
    except Exception:
        trans = ""
    return text, trans


def recognize_translate_file(image_path: str, direction: str = "auto",
                             log=None, config=None):
    """识别一个本地图片文件并翻译，返回 (识别文本, 译文)"""
    try:
        text = (ocr_image(image_path) or "").strip()
    except Exception:
        return "", ""
    if not text:
        return "", ""
    try:
        trans = translate(text, direction, log=log, config=config) or ""
    except Exception:
        trans = ""
    return text, trans