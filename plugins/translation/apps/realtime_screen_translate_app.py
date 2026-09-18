"""
translation/apps/realtime_screen_translate_app.py
────────────────────────────────────────────────
最终应用 · 屏幕实时翻译应用 = 图翻译 + 悬浮显示框 + 自动区域截图（1:1 复刻）。
按钮：[mode, pause, manual, copy, pin, close]，SHOW_ORIG=True（双语），
REQUIRES_REGION=True。

worker = AutoRegionCapture；extras = RegionBoxClient（运行中可拖动/缩放调区）；
pause 暂停定时截图，manual 立即触发一轮，region 变化即生效。
"""

import threading

from apps.app_base import TranslateAppBase
from services.recipes.auto_region_capture import AutoRegionCapture
from services.recipes.image_translate import recognize_translate

DEFAULT_INTERVAL_MS = 1000


class RealtimeScreenTranslateApp(TranslateAppBase):
    NAME = "屏幕实时翻译"
    BUTTONS = ["mode", "pause", "manual", "copy", "pin", "close"]
    SHOW_ORIG = True
    REQUIRES_REGION = True
    USE_TRANSLATE = True

    def _install_overlay_signals(self, overlay):
        return {
            "pause_toggled": self._on_pause,
            "manual_clicked": self._on_manual,
        }

    def _install_extra_widgets(self, rect):
        box = self._make_region_box(rect)
        box.changed.connect(self._on_region_changed)
        self._region_box = box
        return [box]

    def _make_worker(self, rect):
        capture = AutoRegionCapture(interval_ms=DEFAULT_INTERVAL_MS)
        capture.on_frame = self._on_frame
        return capture

    def _initial_status(self) -> str:
        return "⏳ 首次识别加载模型中…（约 30 秒，请稍候）"

    def _log_started(self, rect):
        self.log(f"📺 {self.NAME}已启动（区域 {rect[2]}×{rect[3]}，"
                f"每 {DEFAULT_INTERVAL_MS // 1000}s 刷新）")

    def _on_pause(self, paused: bool):
        if self._worker is None:
            return
        if paused:
            self._worker.stop()
        else:
            if self._worker.region() is None:
                return
            self._worker.start()
        if self._overlay is not None:
            self._overlay.set_status("⏸ 已暂停（点击 ▶ 继续）" if paused else "⏵ 运行中")

    def _on_manual(self):
        if self._worker is None:
            return
        if self._overlay is not None:
            self._overlay.set_status("识别中…")
        self._worker.capture_now()

    def _on_region_changed(self, x, y, w, h):
        rect = (x, y, w, h)
        self._region = rect
        if self._worker is not None:
            self._worker.set_region(rect)

    def _on_frame(self, rect, img):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._process_frame, args=(img,), daemon=True).start()

    def _process_frame(self, img):
        try:
            orig, trans = recognize_translate(img, self.direction, config=self.config())
            self._busy = False
            if self.is_running():
                self._bridge.result_ready.emit(orig, trans)
        except Exception as e:
            self._busy = False
            self._bridge.status.emit(f"❌ {e}")
            self.log(f"❌ {self.NAME}: {e}")