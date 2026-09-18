"""
translation/apps/worker_bridge.py
─────────────────────────────────
最终应用 · 工作线程 → 应用回调 信号桥（1:1 复刻 OCTools worker_bridge，
去掉 Qt 依赖，改为线程安全信号：子线程 emit 同步调用已注册槽）。

回调由工作线程直接触发，槽实现方必须保证线程安全
（本插件中槽仅写 overlay 子进程 stdin / 推送事件 / 追加日志，均加锁，安全）。
"""

import threading


class Signal:
    """最小线程安全信号（等价于 Qt Signal 的 connect/emit 子集）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._slots = []

    def connect(self, slot):
        with self._lock:
            if slot not in self._slots:
                self._slots.append(slot)

    def disconnect(self, slot):
        with self._lock:
            if slot in self._slots:
                self._slots.remove(slot)

    def emit(self, *args):
        with self._lock:
            slots = list(self._slots)
        for s in slots:
            try:
                s(*args)
            except Exception:
                pass


class _WorkerBridge:
    """工作线程 → 应用回调 信号桥（信号与 OCTools 完全同名）"""

    def __init__(self):
        self.text_ready = Signal()          # 单文本结果（屏幕OCR / 屏幕字幕）
        self.result_ready = Signal()        # 双语结果 (orig, trans)
        self.status = Signal()
        self.log_line = Signal()
