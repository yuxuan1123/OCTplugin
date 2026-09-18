"""
translation/services/recipes/one_shot_screen_translate.py
────────────────────────────────────────────────────────
拼接配方 · 一次性屏幕翻译 = 截图 + 图翻译（1:1 复刻 OCTools）。
"""

from services.recipes.common import capture_excluded
from services.recipes.image_translate import recognize_translate


def screen_translate_once(rect, direction: str = "auto", exclude_widgets=None,
                          log=None, config=None):
    """截取区域 → OCR → 翻译，返回 (识别文本, 译文)"""
    try:
        img = capture_excluded(exclude_widgets, rect)
    except Exception:
        return "", ""
    return recognize_translate(img, direction, log=log, config=config)