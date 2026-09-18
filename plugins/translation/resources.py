"""
translation/resources.py
───────────────────────────────────────────────
模型 / 外部依赖声明清单 + 状态查询 / 写入（资源配置）。

宿主设置面板不感知具体模型——通过本模块的 `models.status` 拿到声明式清单，
动态聚合成「外部地址与内存设置」卡片；本插件自己的资源配置持久化在
store/models.json（读取优先级：store/models.json → 环境变量 → 默认空）。

每个资源项：
  { key, label, type: model|binary, path, load, unload,
    cfg_attr (后端配置字段，读/写回 translator_config 或 stt_config),
    ready(fn) 就绪探测 }

load  ∈ lazy | startup | tab（默认 lazy）
unload∈ keep | idle | once（默认 idle；闲置时长由 idle_min 分钟自定义）
"""

import os

from config import presets
from config.ui_config import CONFIG as _C

# store/models.json
_STORE_REC = os.path.join(presets._STORE_DIR, "models.json")

# 启闲置释放的默认时长（分钟），与 idle_manager.DEFAULT_IDLE_S 对应
DEFAULT_IDLE_MIN = 10


# ════════════════════════════════════════════
#  资源清单（本插件的模型声明）
# ════════════════════════════════════════════

# 每项: key 唯一; cfg_attr 指向 translator_config 或 stt_config 的字段名
# load/unload 默认值与 store/models.json 的初始默认一致
RESOURCES = [
    {"key": "opusmt", "label": "Opus-MT（轻量翻译）",
     "type": "model", "cfg_attr": "opusmt_base", "cfg_obj": "translator",
     "default_load": "lazy", "default_unload": "idle"},
    {"key": "hymt2", "label": "Hy-MT2-1.8B（本地大模型）",
     "type": "model", "cfg_attr": "hy_model_path", "cfg_obj": "translator",
     "default_load": "lazy", "default_unload": "idle"},
    {"key": "paddle", "label": "PaddleOCR（屏幕识别）",
     "type": "model", "cfg_attr": "paddle_cache", "cfg_obj": "env",
     "default_load": "lazy", "default_unload": "idle"},
    {"key": "stt", "label": "SenseVoice 语音识别",
     "type": "model", "cfg_attr": "model_dir", "cfg_obj": "stt",
     "default_load": "lazy", "default_unload": "idle"},
]


def _load_store():
    try:
        with open(_STORE_REC, "r", encoding="utf-8") as f:
            import json
            return (json.load(f).get("resources") or {})
    except Exception:
        return {}


def _save_store(resources: dict):
    try:
        presets._ensure_dir()
        import json
        with open(_STORE_REC, "w", encoding="utf-8") as f:
            json.dump({"resources": resources}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_value(item, host):
    """读取某项配置当前值（translator_config / stt_config / 环境变量）"""
    obj = item["cfg_obj"]
    attr = item["cfg_attr"]
    if obj == "translator":
        return getattr(host.translator_config, attr, "") or ""
    if obj == "stt":
        return getattr(host.stt_config, attr, "") or ""
    if obj == "env":
        return _C.model_path(attr.replace("_cache", "")) if "cache" in attr else (_C.model_path(attr) or "")
    return ""


def _set_value(item, host, path):
    """把用户填的路径写回对应配置字段并持久化"""
    obj = item["cfg_obj"]
    attr = item["cfg_attr"]
    if obj == "translator":
        if hasattr(host.translator_config, attr):
            setattr(host.translator_config, attr, str(path or ""))
            presets.save_last_translator_config(host.translator_config)
    elif obj == "stt":
        if hasattr(host.stt_config, attr):
            setattr(host.stt_config, attr, str(path or ""))
            presets.save_last_stt_config(host.stt_config)
    elif obj == "env":
        # 环境变量仅运行时生效当前进程；同时写 store 供下次启动 ui_config 读取
        _C_model_env = "OCTTR_MODEL_" + attr.upper().replace("_CACHE", "")
        os.environ.setdefault(_C_model_env, str(path or ""))


def _ready(item, host):
    """就绪探测：路径存在 或 引擎可用"""
    v = _get_value(item, host)
    if item["key"] == "paddle":
        try:
            from core.engines import ocr_engine
            return ocr_engine.get_ocr("ch") is not None
        except Exception:
            return bool(v and os.path.isdir(v))
    if item["key"] == "stt":
        if not (v and os.path.isdir(v)):
            return False
        return (os.path.exists(os.path.join(v, "model.onnx"))
                or os.path.exists(os.path.join(v, "model.int8.onnx"))) \
                and os.path.exists(os.path.join(v, "tokens.txt"))
    if item["key"] == "opusmt":
        return bool(v and os.path.isdir(v))
    if item["key"] == "hymt2":
        return bool(v and os.path.isfile(v))
    return bool(v)


def models_status(host):
    """返回声明清单 + 当前配置 + 就绪状态（宿主据此动态渲染）"""
    store = _load_store()
    out = []
    for item in RESOURCES:
        key = item["key"]
        rec = store.get(key) or {}
        out.append({
            "key": key,
            "label": item["label"],
            "type": item["type"],
            "path": _get_value(item, host),
            "load": rec.get("load", item["default_load"]),
            "unload": rec.get("unload", item["default_unload"]),
            "idle_min": rec.get("idle_min", DEFAULT_IDLE_MIN),
            "ready": _ready(item, host),
        })
    return {"ok": True, "result": {"groups": out}}


def models_set(host, item_cfg: dict):
    """写入单个资源配置：{key, path?, load?, unload?, idle_min?} → store + 后端字段"""
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
        _set_value(item, host, rec["path"])
    if "load" in item_cfg:
        rec["load"] = str(item_cfg.get("load") or item["default_load"])
    if "idle_min" in item_cfg:
        try:
            rec["idle_min"] = int(item_cfg.get("idle_min") or DEFAULT_IDLE_MIN)
        except (TypeError, ValueError):
            rec["idle_min"] = DEFAULT_IDLE_MIN
    if "unload" in item_cfg:
        rec["unload"] = str(item_cfg.get("unload") or item["default_unload"])
        _apply_unload_policy(host, key, rec["unload"], rec.get("idle_min", DEFAULT_IDLE_MIN))

    store[key] = rec
    _save_store(store)
    return {"ok": True, "result": {"key": key, **rec}}


def _apply_unload_policy(host, key, unload, idle_min=DEFAULT_IDLE_MIN):
    """unload 策略落地到闲置回收器（路径变更后模型路径已改，release 先清旧缓存）。

    idle → 闲置 idle_min 分钟后自动释放；once → 用完即退；keep → 常驻（撤销先前挂号）。
    """
    try:
        from core.idle_manager import get_manager
        mgr = get_manager()
        rfn = _release_fn_for(host, key)
        idle_s = float(idle_min or 1) * 60.0
        if unload == "keep":
            # 常驻：若有旧登记则释放当前缓存并注销
            if mgr.is_registered(key):
                mgr.release(key)
        else:
            # idle / once：无论先前策略如何，统一用 ensure 幂等挂号
            mgr.ensure(key, rfn, unload="idle" if unload == "idle" else "once", idle_s=idle_s)
    except Exception:
        pass


def _release_fn_for(host, key):
    """返回释放该资源引擎缓存的函数"""
    def _release():
        try:
            if key in ("opusmt", "hymt2"):
                from core.engines import translation_engine
                translation_engine.release()
            elif key == "paddle":
                from core.engines import ocr_engine
                ocr_engine.release()
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