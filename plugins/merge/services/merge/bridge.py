# -*- coding: utf-8 -*-
"""merge —— 跨插件调用 conversion（bridge）。

merge 后端无法 import conversion 的 venv（独立依赖隔离）。内核允许插件向自身
stdout 写一条带 method 的请求行，经 dispatchGate 的 `registry.call` 路由到目标
插件并回写响应到插件 stdin。因此这里向自身 stdout 写 registry.call，main.py
读循环把响应投递给对应 future。

为避免循环导入，main.py 启动时调用 `bridge.configure(write_fn)` 注入 stdout
写入函数与 pending 表；调用方（concat.py）通过 `call_conversion()` 发起。

注意：registry.call 走内核 `CallFunc(..., 15*time.Second)` 有 15s 硬超时。
单文件 TTS/STT/OCR（kokoro 冷启动约 20s）会超时 → 由前端逃生门直连 conversion
（timeoutMs=600000）规避，不走本 bridge。
"""
import concurrent.futures as _cf
import itertools

_WRITE = lambda obj: None  # noqa: E731 由 configure 注入
_PENDING = None
_RID = itertools.count(1)


def configure(write_fn, pending):
    """注入 stdout 写入函数与 pending future 表（由 main.py 启动时调用）。"""
    global _WRITE, _PENDING
    _WRITE = write_fn
    _PENDING = pending


def call_conversion(method, params, timeout_ms=12000):
    """原地调用 conversion 插件的 method，返回其 result 字典。

    method 取 conversion 登记的函数名（如 conversion.convert / conversion.formats）。
    返回 conversion 的内层 result（含 {ok, log, ...}）。
    """
    if _PENDING is None:
        return {"error": "bridge 未初始化（未调用 configure）"}
    rid = next(_RID)
    fut = _cf.Future()
    _PENDING[rid] = fut
    _WRITE({
        "v": 1, "jsonrpc": "2.0", "id": rid,
        "method": "registry.call",
        "params": {"name": method, "params": params or {}},
    })
    try:
        return fut.result(timeout_ms / 1000.0)
    except _cf.TimeoutError:
        _PENDING.pop(rid, None)
        return {"error": f"调用 conversion.{method} 超时（>{timeout_ms}ms）"}
    except Exception as e:
        _PENDING.pop(rid, None)
        return {"error": str(e)}