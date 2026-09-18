"""
conversion/resources.py
───────────────────────────────────────────────
模型 / 外部依赖声明清单 + 状态查询 / 写入（资源配置）。

宿主设置面板通过 `conversion.resources.status` 拿到本插件声明式清单，动态聚合成
「外部地址与内存设置」卡片；本插件自己的资源配置持久化在 store/models.json
（读取优先级：store/models.json → 环境变量 → 默认空）。

每项：{ key, label, type: model|binary, path, load, unload, idle_min, ready }
load∈lazy|startup|tab；unload∈keep|idle|once；默认 lazy / idle；闲置时长由 idle_min（分钟）自定义。
"""

import os

from config.ui_config import CONFIG as _C

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STORE_DIR = os.path.join(_PLUGIN_ROOT, "store")
_STORE_REC = os.path.join(_STORE_DIR, "models.json")

# 闲置释放的默认时长（分钟），与 idle_manager.DEFAULT_IDLE_S 对应
DEFAULT_IDLE_MIN = 10


# ════════════════════════════════════════════
#  资源清单（本插件的模型 / 外部依赖声明）
# ════════════════════════════════════════════

RESOURCES = [
    {"key": "kokoro", "label": "Kokoro 语音合成",
     "type": "model", "default_load": "lazy", "default_unload": "idle"},
    {"key": "moss", "label": "MOSS 语音合成",
     "type": "model", "default_load": "lazy", "default_unload": "idle"},
    {"key": "stt", "label": "SenseVoice 语音识别",
     "type": "model", "default_load": "lazy", "default_unload": "idle"},
    {"key": "bin.ffmpeg", "label": "FFmpeg 媒体引擎",
     "type": "binary", "default_load": "lazy", "default_unload": "idle"},
    {"key": "bin.pandoc", "label": "Pandoc 文档引擎",
     "type": "binary", "default_load": "lazy", "default_unload": "idle"},
]


def _ensure_dir():
    try:
        os.makedirs(_STORE_DIR, exist_ok=True)
    except Exception:
        pass


def _load_store():
    try:
        import json
        with open(_STORE_REC, "r", encoding="utf-8") as f:
            return (json.load(f).get("resources") or {})
    except Exception:
        return {}


def _save_store(resources: dict):
    try:
        _ensure_dir()
        import json
        with open(_STORE_REC, "w", encoding="utf-8") as f:
            json.dump({"resources": resources}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_value(item):
    """读取某项当前值：model → model_path(key)；binary → path(key)。"""
    key = item["key"]
    if item["type"] == "binary":
        return _C.path(key.replace("bin.", "")) or ""
    return _C.model_path(key) or ""


def _ready(item):
    v = _get_value(item)
    key = item["key"]
    if key in ("kokoro", "moss") or key == "stt":
        # 模型目录存在且非空
        return bool(v and os.path.isdir(v))
    if key == "bin.ffmpeg":
        return bool(v and os.path.exists(v)) or bool(os.environ.get("FFMPEG") or "")
    if key == "bin.pandoc":
        return bool(v and os.path.exists(v))
    return bool(v)


def models_status(_host):
    """返回声明清单 + 当前配置 + 就绪状态"""
    store = _load_store()
    out = []
    for item in RESOURCES:
        rec = store.get(item["key"]) or {}
        out.append({
            "key": item["key"],
            "label": item["label"],
            "type": item["type"],
            "path": _get_value(item),
            "load": rec.get("load", item["default_load"]),
            "unload": rec.get("unload", item["default_unload"]),
            "idle_min": rec.get("idle_min", DEFAULT_IDLE_MIN),
            "ready": _ready(item),
        })
    return {"ok": True, "result": {"groups": out}}


def models_set(_host, item_cfg: dict):
    """写入单个资源配置：{key, path?, load?, unload?, idle_min?} → store + 缓存失效"""
    key = (item_cfg or {}).get("key")
    if not key:
        return {"ok": False, "error": "缺少资源 key"}
    item = next((r for r in RESOURCES if r["key"] == key), None)
    if item is None:
        return {"ok": False, "error": f"未知资源: {key}"}

    store = _load_store()
    rec = store.get(key) or {}

    if "path" in item_cfg:
        rec["path"] = str(item_cfg.get("path") or "")
        if key == "bin.ffmpeg":
            from core.engines.ffmpeg_utils import reset_ffmpeg_cache
            reset_ffmpeg_cache()
    if "load" in item_cfg:
        rec["load"] = str(item_cfg.get("load") or item["default_load"])
    if "idle_min" in item_cfg:
        try:
            rec["idle_min"] = int(item_cfg.get("idle_min") or DEFAULT_IDLE_MIN)
        except (TypeError, ValueError):
            rec["idle_min"] = DEFAULT_IDLE_MIN
    if "unload" in item_cfg:
        rec["unload"] = str(item_cfg.get("unload") or item["default_unload"])
        _apply_unload_policy(key, rec["unload"], rec.get("idle_min", DEFAULT_IDLE_MIN))

    store[key] = rec
    _save_store(store)
    return {"ok": True, "result": {"key": key, **rec}}


def _apply_unload_policy(key, unload, idle_min=DEFAULT_IDLE_MIN):
    """unload 策略落地到闲置回收器：
    idle → 闲置 idle_min 分钟后自动释放；once → 用完即退；keep → 常驻（撤销先前挂号）。
    """
    try:
        from core.idle_manager import get_manager
        mgr = get_manager()
        rfn = _release_fn_for(key)
        idle_s = float(idle_min or 1) * 60.0
        if unload == "keep":
            if mgr.is_registered(key):
                mgr.release(key)
        else:
            mgr.ensure(key, rfn, unload="idle" if unload == "idle" else "once", idle_s=idle_s)
    except Exception:
        pass


def _release_fn_for(key):
    def _release():
        try:
            if key in ("kokoro", "moss"):
                from core.engines import tts_engine
                tts_engine.release()
            elif key == "stt":
                from core.engines import speech_engine
                speech_engine.release()
        except Exception:
            pass
    return _release


def register_engine(key, release_fn):
    """引擎入口自注册：按 store 中用户配置的 unload / idle_min 挂号（幂等）。

    宿主「外部地址与内存设置」直接写 store（不经插件 RPC），引擎在首次使用此资源时
    按下述策略自查挂号，保证闲置释放的时长（idle_min）真正生效。
    返回当前生效的 unload 策略。
    """
    try:
        from core.idle_manager import get_manager
        store = _load_store()
        rec = store.get(key) or {}
        unload = rec.get("unload") or "idle"
        try:
            idle_min = int(rec.get("idle_min") or DEFAULT_IDLE_MIN)
        except (TypeError, ValueError):
            idle_min = DEFAULT_IDLE_MIN
        idle_s = float(idle_min or 1) * 60.0
        mgr = get_manager()
        if unload == "keep":
            mgr.set_policy(key, "keep", idle_s)
        else:
            mgr.ensure(key, release_fn,
                       unload="idle" if unload == "idle" else "once", idle_s=idle_s)
        return unload
    except Exception:
        return "idle"