"""
translation/services/recipes/builtin_speech_recognize.py
───────────────────────────────────────────────────────
拼接配方 · 内置语音识别 = 录音 + 语音识别（1:1 复刻 OCTools 逻辑，纯线程实现）。

定时循环：后台采集系统内置声音 / 麦克风 → 每 interval 取走缓冲音频做语音识别。

回调（由工作线程触发，调用方须保证线程安全）：
  on_result(text)   识别文本（空文本不发）
  on_status(msg)    阶段反馈
  on_error(msg)     异常（不中断循环）
  on_paused(bool)   暂停状态变化
运行中可暂停（set_paused）——暂停时停止定时识别但保持录音缓冲。
"""

import threading

from core.engines.audio_capture_engine import (
    VOICE_TARGET_RATE, record, to_16k_mono,
)
from core.engines.speech_engine import transcribe_pcm16k

DEFAULT_INTERVAL_MS = 3000
MIN_SECONDS = 0.3


class BuiltinSpeechRecognize:
    """定时循环：后台录音 → 定时取走缓冲音频做语音识别"""

    def __init__(self, mode: str = "loopback", interval_ms: int = DEFAULT_INTERVAL_MS,
                 stt_config=None):
        self._mode = mode if mode in ("loopback", "mic") else "loopback"
        self._stt_config = stt_config
        self._busy = False
        self._paused = False
        self._stopped = True
        self._capture = None
        self._frames = []
        self._lock = threading.Lock()
        self._target_rate = VOICE_TARGET_RATE
        self._interval = max(500, int(interval_ms))
        self._timer = None
        # 回调
        self.on_result = None
        self.on_status = None
        self.on_error = None
        self.on_paused = None

    # ── 控制 ──

    def start(self, rect=None):
        if not self._stopped and self._capture is not None:
            return
        self._stopped = False
        self._paused = False
        self._frames = []
        self._capture = record(self._mode, self._on_frames, self._on_capture_error)
        self._capture.start()
        self._timer = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer.start()
        self._on_tick()   # 立即尝试第一轮（缓冲未满时自动跳过）

    def stop(self):
        self._stopped = True
        if self._capture is not None:
            try:
                self._capture.stop()
            except Exception:
                pass
            self._capture = None
        with self._lock:
            self._frames = []

    def set_paused(self, paused: bool):
        paused = bool(paused)
        if paused == self._paused or self._stopped:
            return
        self._paused = paused
        if not paused:
            self._on_tick()
        if self.on_paused:
            try:
                self.on_paused(self._paused)
            except Exception:
                pass

    def is_paused(self) -> bool:
        return self._paused

    def is_running(self) -> bool:
        return not self._stopped and self._capture is not None

    def refresh_now(self):
        if not self._stopped and not self._paused:
            self._on_tick()

    def set_interval(self, ms: int):
        self._interval = max(500, int(ms))

    def _timer_loop(self):
        while not self._stopped:
            try:
                self._on_tick()
            except Exception:
                pass
            # 分块等待，退出及时
            waited = 0
            while waited < self._interval and not self._stopped:
                import time
                time.sleep(0.1)
                waited += 100

    # ── 音频回调（采集线程）──

    def _on_frames(self, rate, channels, data):
        try:
            frame = to_16k_mono(rate, channels, data)
        except Exception:
            return
        with self._lock:
            if not self._stopped:
                self._frames.append(frame)

    def _on_capture_error(self, msg):
        if self.on_error:
            try:
                self.on_error(f"录音失败: {msg}")
            except Exception:
                pass

    # ── 定时识别 ──

    def _on_tick(self):
        if self._busy or self._stopped or self._paused:
            return
        min_len = int(self._target_rate * MIN_SECONDS * 2)
        with self._lock:
            frames = self._frames
            if not frames:
                return
            # 拼接全部缓冲；仅当长度足够才取出
            data = b"".join(frames)
            if len(data) < min_len:
                return
            self._frames = []
        self._busy = True
        threading.Thread(target=self._process, args=(data,), daemon=True).start()

    def _process(self, data):
        """工作线程：语音识别 → 回调回传"""
        try:
            if self.on_status:
                self.on_status("⏳ 正在加载语音识别模型（首次约 20-30 秒）…")
            text = transcribe_pcm16k(data, log=lambda m: None, config=self._stt_config)
            if self._stopped:
                return
            if not text:
                if self.on_status:
                    self.on_status("（未识别到有效语音）")
                return
            if self.on_result:
                self.on_result(text)
        except Exception as e:
            if not self._stopped and self.on_error:
                try:
                    self.on_error(str(e))
                except Exception:
                    pass
        finally:
            self._busy = False