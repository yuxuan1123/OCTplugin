"""
translation/services/recipes/auto_region_capture.py
──────────────────────────────────────────────────
拼接配方 · 自动区域截图 = 定时器 + 截图（实时）。1:1 复刻 OCTools 逻辑，
改用后台线程驱动（无 Qt 定时器）。

输出：通过 on_frame(rect, PIL Image) 回调每帧。
运行中可随时 set_region 修改识别范围。
"""

import threading
import time

from services.recipes.common import capture_excluded


class AutoRegionCapture:
    """后台线程定期截取指定区域（区域可运行中修改）。

    回调（由采集线程触发，调用方须保证线程安全）：
      on_frame(rect, img)
      on_error(msg)
    """

    def __init__(self, interval_ms: int = 1000, exclude_widgets=None):
        self._region = None            # (x, y, w, h)
        self._interval = max(200, int(interval_ms))
        self.on_frame = None
        self.on_error = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def set_region(self, rect):
        with self._lock:
            self._region = tuple(int(v) for v in rect)

    def region(self):
        with self._lock:
            return tuple(self._region) if self._region else None

    def set_interval(self, ms: int):
        self._interval = max(200, int(ms))

    def interval(self) -> int:
        return self._interval

    def start(self, region=None):
        if region is not None:
            self.set_region(region)
        if self._region is None or not (self._region[2] > 0 and self._region[3] > 0):
            raise ValueError("自动区域截图需要先设置截图区域")
        if self.is_running():
            self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.0)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def capture_now(self):
        """立即截取一帧（手动翻译 / 首帧立即输出）"""
        with self._lock:
            region = tuple(self._region) if self._region else None
        if region is None:
            return
        try:
            img = capture_excluded(None, region)
        except Exception:
            return
        if self.on_frame:
            try:
                self.on_frame(region, img)
            except Exception:
                pass

    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                region = tuple(self._region) if self._region else None
            if region:
                try:
                    img = capture_excluded(None, region)
                except Exception as e:
                    if self.on_error:
                        try:
                            self.on_error(str(e))
                        except Exception:
                            pass
                    continue
                if self.on_frame:
                    try:
                        self.on_frame(region, img)
                    except Exception:
                        pass
            self._stop.wait(self._interval / 1000.0)


def capture_frame(rect, exclude_widgets=None):
    """截取指定区域返回 PIL Image（截图失败返回 None）"""
    try:
        return capture_excluded(exclude_widgets, rect)
    except Exception:
        return None