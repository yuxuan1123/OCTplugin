"""
translation/services/recipes/common.py
──────────────────────────────────────
拼接配方层共享工具（非 UI）：

  capture_excluded(widgets, rect)
    截取指定区域。原生 OCTools 会先隐藏悬浮窗再截图避免把自己拍进去；
    本插件为纯 Python + mss，悬浮窗是独立原生子进程窗口，直接截取区域即可。
"""

from core.engines.screenshot_engine import CAPTURE_EXCLUDE_DELAY, grab_region


def capture_excluded(widgets, rect):
    """截取指定区域，返回 PIL Image。

    widgets 参数保留以兼容配方层接口（原生版本用于临时隐藏悬浮窗）。
    """
    return grab_region(rect)