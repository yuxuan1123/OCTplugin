"""
translation/apps/one_shot_screen_translate_app.py
────────────────────────────────────────────────
最终应用 · 一次性屏幕翻译应用 = 一次性屏幕翻译 + 悬浮显示框（1:1 复刻）。
按钮：[mode, retry, copy, pin, close]，SHOW_ORIG=True（双语），
REQUIRES_REGION=True，USE_TRANSLATE=True（识别 + 翻译）。
"""

from apps.app_base import TranslateAppBase


class OneShotScreenTranslateApp(TranslateAppBase):
    NAME = "屏幕翻译"
    BUTTONS = ["mode", "retry", "copy", "pin", "close"]
    SHOW_ORIG = True
    REQUIRES_REGION = True
    USE_TRANSLATE = True

    def _install_overlay_signals(self, overlay):
        return {"retry_clicked": self._run_once}

    def _install_extra_widgets(self, rect):
        # 可见的识别框：运行中可拖动/缩放调整识别区域，下次识别生效
        box = self._make_region_box(rect)
        box.changed.connect(self._on_region_changed)
        self._region_box = box
        return [box]

    def _on_region_changed(self, x, y, w, h):
        self._region = (x, y, w, h)

    def _kick_off(self):
        self._run_once()

    def _initial_status(self) -> str:
        return "⏳ 正在加载 OCR / 翻译模型（首次约 30 秒）…"

    def _log_started(self, rect):
        self.log(f"🌐 {self.NAME}已启动（区域 {rect[2]}×{rect[3]}）")