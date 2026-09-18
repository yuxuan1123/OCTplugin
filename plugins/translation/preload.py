"""
translation/preload.py
───────────────────────────────────────────────
翻译模型后台预加载编排（按「模型管理」的资源级 load 策略）。

不再全量预热。启动时读取 store/models.json 中各资源 key 的 load 策略：
  - load=startup → 本插件进程启动后后台预热
  - load=tab     → 进入页面时（宿主/页面 JS 调 preload {timing:"tab"}）预热
  - load=lazy（默认）→ 不预加载，首次使用时现场加载

幂等：进程内每个 key 只启动一次。加载失败静默（不抛异常、不影响主程序）。
"""

import threading

_PRELOAD_STARTED = {}


def _load_policy():
    """返回 {key: load}（来自 store/models.json；异常时返回空）。"""
    try:
        import json
        import os
        _d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "store", "models.json")
        with open(_d, "r", encoding="utf-8") as f:
            resources = (json.load(f).get("resources") or {})
        return {k: (v or {}).get("load", "lazy") for k, v in resources.items()}
    except Exception:
        return {}


def _warm(key, cfg):
    """按 key 预热特定引擎（互不依赖，各自线程）"""
    t = threading.Thread(
        target=_warm_impl, args=(key, cfg), daemon=True)
    t.start()


def _warm_impl(key, cfg):
    try:
        if key == "paddle":
            from core.engines.ocr_engine import warmup_ocr
            warmup_ocr()
        elif key == "stt":
            from core.engines import speech_engine
            speech_engine._ensure_model(cfg) if False else None
        elif key in ("opusmt", "hymt2"):
            from core.engines import translation_engine
            translation_engine.warmup(direction="auto", config=cfg)
    except Exception:
        pass


def start_preload(config=None, timing: str = "startup") -> bool:
    """按资源级 load 策略启动匹配时序的模型预热。返回是否启动了任一预热。"""
    cfg = config
    if cfg is None:
        from config.translator_config import TranslatorConfig
        cfg = TranslatorConfig()
    if not getattr(cfg, "preload_models", True):
        return False
    policy = _load_policy()
    started = False
    for key, load in policy.items():
        if load != timing:
            continue
        if _PRELOAD_STARTED.get(key):
            continue
        _PRELOAD_STARTED[key] = True
        _warm(key, cfg)
        started = True
    return started