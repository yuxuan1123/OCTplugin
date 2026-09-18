"""
translation/services/recipes/realtime_speech_translate.py
────────────────────────────────────────────────────────
拼接配方 · 实时语音翻译 = 录音 + 语音识别 + 文字翻译（1:1 复刻 OCTools 逻辑）。

纯逻辑拼接，不可增减功能：
  1. 录音（core/engines/audio_capture_engine）持续采集系统内置声音
  2. 语音识别（core/engines/speech_engine）把音频转换为文本
  3. 文字翻译（core/engines/translation_engine）翻译识别文本

录音 + 定时缓冲识别逻辑复用 内置语音识别（builtin_speech_recognize），
仅追加文字翻译，不增减功能。运行中可暂停（set_paused）。不含任何 UI 控件。

回调（由工作线程触发，调用方须保证线程安全）：
  on_result(orig, trans)   识别原文 + 翻译译文
  on_status(msg)           阶段反馈
  on_error(msg)            异常（不中断循环）
  on_paused(bool)          暂停状态变化
"""

from core.engines.speech_engine import transcribe_pcm16k
from core.engines.translation_engine import DIRECTION_LABELS, translate
from services.recipes.builtin_speech_recognize import (
    BuiltinSpeechRecognize,
    DEFAULT_INTERVAL_MS,
)


class RealtimeSpeechTranslate(BuiltinSpeechRecognize):
    """定时循环：后台录音 → 语音识别 → 翻译为中文"""

    def __init__(self, direction: str = "en2zh", interval_ms: int = DEFAULT_INTERVAL_MS,
                 config=None, stt_config=None):
        super().__init__(mode="loopback", interval_ms=interval_ms, stt_config=stt_config)
        if direction not in DIRECTION_LABELS:
            direction = "en2zh"
        self._direction = direction
        self._trans_config = config
        # 覆盖父类语义：on_result(orig, trans)，识别 + 翻译两段内容

    def set_direction(self, direction: str):
        """运行前/运行中切换翻译方向"""
        if direction in DIRECTION_LABELS:
            self._direction = direction

    # ── 覆盖：识别后追加翻译 ──

    def _process(self, data):
        """工作线程：语音识别 → 翻译 → 回调回传（不碰任何 UI 线程）"""
        try:
            if self.on_status:
                self.on_status("⏳ 正在加载语音识别模型（首次约 20-30 秒）…")
            text = transcribe_pcm16k(
                data, log=lambda m: None, config=self._stt_config)
            if self._stopped:
                return
            if not text:
                if self.on_status:
                    self.on_status("（未识别到有效语音）")
                return
            if self.on_status:
                self.on_status("⏳ 正在翻译…")
            trans = translate(text, self._direction,
                              config=self._trans_config) if text else ""
            if self._stopped:
                return
            if self.on_result:
                try:
                    self.on_result(text, trans)
                except Exception:
                    pass
        except Exception as e:
            if not self._stopped and self.on_error:
                try:
                    self.on_error(str(e))
                except Exception:
                    pass
        finally:
            self._busy = False