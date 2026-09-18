"""
translation/apps/screen_subtitle_app.py
───────────────────────────────────────
最终应用 · 屏幕字幕 = 内置语音识别 + 悬浮显示框（1:1 复刻）。
按钮：[pause, copy, pin, close]，SHOW_ORIG=False（仅纯文本字幕），
REQUIRES_REGION=False（语音类），USE_TRANSLATE=False（仅识别不翻译）。
"""

from apps.app_base import TranslateAppBase
from services.recipes.builtin_speech_recognize import BuiltinSpeechRecognize

DEFAULT_INTERVAL_MS = 3000


class ScreenSubtitleApp(TranslateAppBase):
    NAME = "屏幕字幕"
    BUTTONS = ["pause", "copy", "pin", "close"]
    SHOW_ORIG = False
    REQUIRES_REGION = False
    USE_TRANSLATE = False

    def _install_overlay_signals(self, overlay):
        return {"pause_toggled": self._on_pause}

    def _make_worker(self, rect):
        voice = BuiltinSpeechRecognize(
            mode="loopback", interval_ms=DEFAULT_INTERVAL_MS,
            stt_config=self.stt_config())
        voice.on_result = self._bridge.text_ready.emit
        voice.on_status = self._bridge.status.emit
        voice.on_error = self._on_error
        return voice

    def _initial_status(self) -> str:
        return "⏳ 正在加载语音识别模型…（首次约 30 秒）"

    def _log_started(self, rect):
        self.log(f"🎙️ {self.NAME}已启动（系统内置声音，每 {DEFAULT_INTERVAL_MS // 1000}s 刷新）")

    def _on_pause(self, paused: bool):
        if self._worker is not None:
            self._worker.set_paused(paused)
            if self._overlay is not None:
                self._overlay.set_status("⏸ 已暂停（点击 ▶ 继续）" if paused else "⏵ 运行中")

    def _on_error(self, err):
        if self._overlay is not None:
            self._overlay.set_status(f"❌ {err}")
        self.log(f"❌ {self.NAME}: {err}")