"""
conversion/core/engines/screenshot_engine.py
───────────────────────────────────────────────
截图引擎（降级桩）· 屏幕区域 → PIL Image —— 核心引擎层（原子能力）

说明：
  本模块在插件中为降级实现。原生 OCTools 版本依赖 PySide6 的
  QGuiApplication / QRect / QImage 抓屏（必须在 Qt GUI 线程运行），
  插件后端为纯 Python（无 PySide），故不提供真实截图能力，
  所有对外函数调用时抛出带清晰提示的 RuntimeError。
"""
# -*- coding: utf-8 -*-

# 截屏前隐藏悬浮窗后，等待桌面合成器刷新，避免把悬浮窗拍进截图（秒）
CAPTURE_EXCLUDE_DELAY = 0.06


def _unsupported():
    raise RuntimeError(
        "❌ 屏幕截图在本纯 Python 插件后端中不可用：该能力依赖 PySide6 "
        "（QGuiApplication），插件禁止引入 PySide6。如需截图请使用系统自带工具。"
    )


def _as_qrect(rect):
    """兼容 QRect / (x, y, w, h) 两种区域传入（降级：不支持）"""
    _unsupported()


def qimage_to_pil(qimg):
    """QImage → PIL Image（RGB）（降级：不支持）"""
    _unsupported()


def grab_region(rect):
    """截取屏幕坐标 rect 区域的图像，返回 PIL Image（降级：不支持）"""
    _unsupported()


def grab_fullscreen():
    """截取主屏幕完整图像，返回 PIL Image（降级：不支持）"""
    _unsupported()


def screen_rect():
    """主屏幕几何（降级：不支持）"""
    _unsupported()