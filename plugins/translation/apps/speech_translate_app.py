"""
translation/apps/speech_translate_app.py
────────────────────────────────────────
最终应用 · 语音翻译应用 = 实时语音翻译 + 悬浮显示框（1:1 复刻）。
按钮：[mode, pause, copy, pin, close]，SHOW_ORIG=True（双语），
REQUIRES_REGION=False（语音类），USE_TRANSLATE=True（识别 + 翻译）。
"""

from apps.app_base import TranslateAppBase
from services.recipes.realtime_speech_translate import RealtimeSpeechTranslate

DEFAULT_INTERVAL_MS = 3000


class SpeechTranslateApp(TranslateAppBase):
    NAME = "语音翻译"
    BUTTONS = ["mode", "pause", "copy", "pin", "close"]
    SHOW_ORIG = True
    REQUIRES_REGION = False
    USE_TRANSLATE = True

    def _install_overlay_signals(self, overlay):
        return {"pause_toggled": self._on_pause}

    def _make_worker(self, rect):
        speech = RealtimeSpeechTranslate(
            direction=self.direction,
            interval_ms=DEFAULT_INTERVAL_MS,
            config=self.config(),
            stt_config=self.stt_config())
        speech.on_result = self._bridge.result_ready.emit
        speech.on_status = self._bridge.status.emit
        speech.on_error = self._on_error
        return speech

    def _initial_status(self) -> str:
        return "⏳ 正在采集系统声音 / 加载模型…（首次约 30 秒）"

    def _log_started(self, rect):
        self.log(f"🎙️ {self.NAME}已启动（系统内置声音 → 中文字幕，每 {DEFAULT_INTERVAL_MS // 1000}s 刷新）")

    def _on_pause(self, paused: bool):
        if self._worker is not None:
            self._worker.set_paused(paused)
            if self._overlay is not None:
                self._overlay.set_status("⏸ 已暂停（点击 ▶ 继续）" if paused else "⏵ 运行中")

    def _on_error(self, err):
        if self._overlay is not None:
            self._overlay.set_status(f"❌ {err}")
        self.log(f"❌ {self.NAME}: {err}")