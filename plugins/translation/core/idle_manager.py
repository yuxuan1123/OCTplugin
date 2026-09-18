"""
core/idle_manager.py
───────────────────────────────────────────────
模型闲置回收器（每插件各一份，插件 venv 隔离）。

为用户配置为 `unload=idle`（闲置 N 分钟释放）的模型维护闲置计时：
  - register(key, release_fn, unload)  注册一个可释放模型及其释放函数
  - ensure(key, release_fn, unload, idle_s) 未注册则注册（幂等），供引擎入口自注册
  - touch(key)       模型被使用时重置闲置计时
  - set_policy(key, unload, idle_s)    更新策略
  - release(key)      立即释放（清缓存 + gc.collect）

unload ∈ keep（常驻不回收）/ once（用完即退）/ idle（闲置 N 分钟后释放）
闲置时长 idle_s 由用户配置的「闲置分钟数」换算，默认 10 分钟。
后台 daemon 线程每 60s tick 一次，对策略为 idle 且闲置超时的 key 调用其 release_fn。
"""

import gc
import threading
import time

DEFAULT_IDLE_S = 10 * 60  # 默认闲置 10 分钟


class _IdleModelManager:
    def __init__(self):
        self._lock = threading.Lock()
        # key -> {"release_fn": callable, "last": float, "unload": str, "idle_s": float}
        self._models = {}
        self._started = False

    # ── 后台 tick 线程（首次 register 时自动启动）──
    def _ensure_thread(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._tick_loop, daemon=True, name="model-idle").start()

    def _tick_loop(self):
        while True:
            try:
                time.sleep(60)
            except Exception:
                break
            self._sweep()

    def _sweep(self):
        now = time.time()
        with self._lock:
            items = list(self._models.items())
        for key, m in items:
            if m["unload"] != "idle":
                continue
            if now - m["last"] >= m["idle_s"]:
                try:
                    m["release_fn"]()
                except Exception:
                    pass
                finally:
                    with self._lock:
                        self._models.pop(key, None)

    # ── 对外 API ──

    def register(self, key, release_fn, unload="idle", idle_s=DEFAULT_IDLE_S):
        with self._lock:
            self._models[key] = {
                "release_fn": release_fn,
                "last": time.time(),
                "unload": unload,
                "idle_s": float(idle_s or DEFAULT_IDLE_S),
            }
        self._ensure_thread()

    def ensure(self, key, release_fn, unload="idle", idle_s=DEFAULT_IDLE_S):
        """未注册则注册（引擎入口自注册的幂等入口）。已注册仅更新策略/时长。"""
        with self._lock:
            m = self._models.get(key)
            if m is None:
                self._models[key] = {
                    "release_fn": release_fn,
                    "last": time.time(),
                    "unload": unload,
                    "idle_s": float(idle_s or DEFAULT_IDLE_S),
                }
            else:
                m["unload"] = unload
                m["idle_s"] = float(idle_s or DEFAULT_IDLE_S)
                m["last"] = time.time()
        self._ensure_thread()

    def touch(self, key):
        with self._lock:
            m = self._models.get(key)
            if m is not None:
                m["last"] = time.time()

    def set_policy(self, key, unload, idle_s=DEFAULT_IDLE_S):
        with self._lock:
            m = self._models.get(key)
            if m is not None:
                m["unload"] = unload
                m["idle_s"] = float(idle_s or DEFAULT_IDLE_S)

    def release(self, key):
        with self._lock:
            m = self._models.pop(key, None)
        if m is not None:
            try:
                m["release_fn"]()
            except Exception:
                pass
            gc.collect()

    def is_registered(self, key):
        with self._lock:
            return key in self._models


# 单例
_idle = _IdleModelManager()


def get_manager():
    return _idle