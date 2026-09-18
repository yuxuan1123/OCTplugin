"""
translation/core/engines/screenshot_engine.py
───────────────────────────────────────────────
截图引擎 · 屏幕区域 → PIL Image —— 核心引擎层（原子能力）。

原生 OCTools 用 PySide6（QGuiApplication）抓屏（须 Qt GUI 线程）；
本插件为纯 Python（无 PySide6），改用 mss + Pillow 跨平台截屏。
支持多显示器坐标偏移与全屏抓取。截图失败抛带清晰提示的 RuntimeError。
"""

# -*- coding: utf-8 -*-

# 重依赖惰性守卫：依赖未装时模块也能被 import（插件照常启动），
# 真用到截图时再报清晰中文错误，而不是在启动阶段崩溃。
try:
    import mss
    from PIL import Image
    _SCREENSHOT_READY = True
except Exception:
    mss = None
    Image = None
    _SCREENSHOT_READY = False

# 截屏前隐藏悬浮窗后，等待桌面合成器刷新（秒）
CAPTURE_EXCLUDE_DELAY = 0.06


def _require_ready():
    if not _SCREENSHOT_READY:
        raise RuntimeError("截图功能缺少依赖（mss / Pillow），请在设置中确认依赖已安装")
    return True


def _monitors():
    """返回所有显示器几何列表（mss 坐标：左/上/宽/高）。"""
    _require_ready()
    with mss.mss() as sct:
        return list(sct.monitors)


def _screen_for(center_x: int, center_y: int):
    """按给定坐标找所在显示器；找不到回退主显示器，返回 (left, top, w, h)。"""
    monitors = _monitors() or []
    for m in monitors:
        if m["left"] <= center_x < m["left"] + m["width"] and \
                m["top"] <= center_y < m["top"] + m["height"]:
            return m
    main = monitors[0] if monitors else {"left": 0, "top": 0, "width": 1920, "height": 1080}
    return main


def _as_rect(rect):
    """兼容 (x, y, w, h) 或 dict{left,top,width,height}，返回 (x, y, w, h)。"""
    if isinstance(rect, dict):
        return (int(rect.get("left", 0)), int(rect.get("top", 0)),
                int(rect.get("width", 0)), int(rect.get("height", 0)))
    x, y, w, h = rect
    return int(x), int(y), int(w), int(h)


def _grab(left, top, width, height) -> Image.Image:
    if width <= 0 or height <= 0:
        return Image.new("RGB", (1, 1), (0, 0, 0))
    with mss.mss() as sct:
        shot = sct.grab({"left": int(left), "top": int(top),
                         "width": int(width), "height": int(height)})
        return Image.frombytes("RGB", shot.size, shot.bgra,
                               "raw", "BGRX").convert("RGB")


def grab_region(rect) -> Image.Image:
    """截取屏幕坐标 rect 区域的图像（(x, y, w, h)），返回 PIL Image。"""
    x, y, w, h = _as_rect(rect)
    if w <= 0 or h <= 0:
        return Image.new("RGB", (1, 1), (0, 0, 0))
    # 自动下移到最近显示器（区域可能跨屏）
    m = _screen_for(x + w // 2, y + h // 2)
    return _grab(x, y, w, h)


def grab_fullscreen() -> Image.Image:
    """截取主屏幕完整图像，返回 PIL Image。"""
    m = _monitors()
    main = m[0] if m else {"left": 0, "top": 0, "width": 1920, "height": 1080}
    return _grab(main["left"], main["top"], main["width"], main["height"])


def screen_rect():
    """主屏幕几何 (x, y, w, h)，供区域框选 / 全屏遮罩定位使用。"""
    m = _monitors()
    main = m[0] if m else {"left": 0, "top": 0, "width": 1920, "height": 1080}
    return (main["left"], main["top"], main["width"], main["height"])


def all_screens_rect():
    """所有显示器外接矩形，供全屏框选遮罩使用 (left, top, width, height)。"""
    monitors = _monitors()
    if not monitors:
        return (0, 0, 1920, 1080)
    left = min(x["left"] for x in monitors)
    top = min(x["top"] for x in monitors)
    right = max(x["left"] + x["width"] for x in monitors)
    bottom = max(x["top"] + x["height"] for x in monitors)
    return (left, top, right - left, bottom - top)