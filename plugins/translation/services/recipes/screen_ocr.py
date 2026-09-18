"""
translation/services/recipes/screen_ocr.py
─────────────────────────────────────────
拼接配方 · 屏幕 OCR = 截图 + 图像识别（1:1 复刻 OCTools）。
"""

from core.engines.ocr_engine import ocr_pil_image
from services.recipes.common import capture_excluded


def screen_ocr(rect, exclude_widgets=None) -> str:
    """截图指定区域 → OCR 识别 → 返回文本"""
    try:
        img = capture_excluded(exclude_widgets, rect)
    except Exception:
        return ""
    try:
        return (ocr_pil_image(img) or "").strip()
    except Exception:
        return ""