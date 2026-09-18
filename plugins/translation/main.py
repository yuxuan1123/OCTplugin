# -*- coding: utf-8 -*-
"""translation —— 翻译插件后端（自包含 stdin/stdout JSON-RPC），1:1 复刻 OCTools。

能力：
  - 文本翻译：Hy-MT2-1.8B（llama_cpp 本地 GGUF）/ Opus-MT（ctranslate2+SPM），
    方向 auto/zh2en/en2zh。
  - 五种实时功能应用：屏幕OCR / 屏幕翻译 / 屏幕实时翻译 / 屏幕字幕 / 语音翻译，
    每应用 = 拼接配方（services/recipes 线程版）+ 原生悬浮窗子进程
    （apps/overlay，Tkinter）+ 区域框选。
  - 配置持久化：store/ 下 translator.json / stt.json / screen_region.json。
  - 事件推送：经 event.emit 推 `translation.<evt>` 给宿主；UI 自身收不到本插件
    事件（若 iframe），用 600ms 轮询兜底。

协议：stdin/stdout JSON 行。
  内核 → 插件请求：{"id":n,"method":"translation.*","params":{}}；回 {"id":n,"result":{ok,result}}
  插件 → 内核：    {"id":n,"method":"event.emit","params":{"type":"translation.<evt>","data":{}}}
慢操作（翻译 / 应用启动）放线程池，保证 ping 始终秒回。
"""

import json
import os
import sys
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

# Windows 下强制 UTF-8 stdio，避免中文路径/文本乱码
if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 配置 ──────────────────────────────────────
from config import presets
from config.translator_config import (
    TranslatorConfig, ENGINE_LABELS, ENGINE_ORDER,
)
from config.stt_config import SttConfig
from config.screen_region_config import ScreenRegionConfig, DEFAULT_BORDER_COLOR

# ── 核心引擎 ──────────────────────────────────
from core.engines import translation_engine as TRANSLATOR

# ── 应用编排 ──────────────────────────────────
from registry import (_APP_ROWS, _SCREEN_APP_CARD, _VOICE_APP_CARD,
                      TR_ENGINE_LABELS, TR_ENGINE_ORDER)
from apps.app_controller import (
    init_apps, app, is_app_running, toggle_app, start_app, stop_app,
    stop_all_apps, app_states, set_app_running, append_log,
)

# LANGS 兼容返回（底层仅支持中英；保留名称供旧调用方）
LANGS = {
    "auto": "自动检测",
    "zh": "中文",
    "en": "英文",
}

MAX_LOGS = 500
_WRITE_LOCK = threading.Lock()
_SEQ = [0]


def _write(obj):
    with _WRITE_LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _reply(req_id, result=None, error=None):
    out = {"v": 1, "jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result if result is not None else {}
    _write(out)


# ════════════════════════════════════════════
#  宿主（应用编排的注入对象）
# ════════════════════════════════════════════


class Host:
    """应用编排宿主：提供 config / log / emit（对应 OCTools 的 page）"""

    def __init__(self):
        self.translator_config = presets.load_last_translator_config() or TranslatorConfig()
        self.stt_config = presets.load_last_stt_config() or SttConfig()
        self.screen_region_config = presets.load_last_screen_region_config() or ScreenRegionConfig()
        self._apps = {}
        self._logs = []
        self._event_pump = self.emit    # app_controller 回调入口 (name, data)

    def log(self, msg):
        with _WRITE_LOCK:
            self._logs.append(str(msg))
            if len(self._logs) > MAX_LOGS:
                del self._logs[: len(self._logs) - MAX_LOGS]
        try:
            self._event_pump("log_line", {"message": str(msg)})
        except Exception:
            pass

    def emit(self, name, data=None):
        """推 `translation.<name>` 事件给宿主（event.emit）"""
        _SEQ[0] += 1
        _write({"v": 1, "jsonrpc": "2.0", "id": _SEQ[0],
                "method": "event.emit",
                "params": {"type": "translation." + name, "data": data or {}}})


_HOST = Host()
_EXECUTOR = ThreadPoolExecutor(max_workers=8)


def _llama_importable() -> bool:
    """llama_cpp（llama-cpp-python）能否导入（Hy 引擎运行依赖是否就绪）"""
    try:
        import importlib.util
        return importlib.util.find_spec("llama_cpp") is not None
    except Exception:
        return False


# Hy 引擎运行依赖（llama-cpp-python）按需安装状态
_HY_INSTALLING = False
_HY_INSTALLED = _llama_importable()


def _install_hy_dep():
    """后台安装 llama-cpp-python 到插件 venv（与内核同源镜像/缓存）。"""
    global _HY_INSTALLING, _HY_INSTALLED
    if _HY_INSTALLING or _HY_INSTALLED:
        return
    _HY_INSTALLING = True
    _HOST.log("🔧 正在安装 Hy 引擎依赖（llama-cpp-python，首次需 MSVC 编译，可能需要数分钟）…")
    try:
        py = sys.executable
        cmd = ["uv", "pip", "install", "-q", "--python", py,
               "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
               "--cache-dir", os.path.join(os.path.expanduser("~"), ".cache", "uv"), "llama-cpp-python"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        if proc.returncode == 0 and _llama_importable():
            _HY_INSTALLED = True
            _HOST.log("✅ Hy 引擎依赖安装成功，可切换为 Hy-MT2-1.8B 引擎")
        else:
            _HOST.log(f"❌ Hy 引擎依赖安装失败: {' | '.join(tail)}")
    except Exception as e:
        _HOST.log(f"❌ Hy 引擎依赖安装异常: {e}")
    finally:
        _HY_INSTALLING = False


# ════════════════════════════════════════════
#  翻译 / 配置 / 模型状态
# ════════════════════════════════════════════


def _handle_translate(params, log):
    text = (params or {}).get("text", "")
    direction = (params or {}).get("direction", "auto")
    if not str(text or "").strip():
        return {"ok": False, "error": "原文为空，请输入要翻译的文本"}
    if direction not in TRANSLATOR.DIRECTION_ORDER:
        direction = "auto"
    _HOST.log(f"🌐 开始翻译（{TRANSLATOR.DIRECTION_LABELS.get(direction, direction)}，{len(str(text))} 字符）")
    try:
        out = TRANSLATOR.translate(text, direction, log=log, config=_HOST.translator_config)
        if not str(out or "").strip():
            return {"ok": False, "error": "翻译结果为空（请检查模型配置）", "result": {"source": direction, "text": ""}}
        src = TRANSLATOR.detect_language(text)
        _HOST.log(f"✅ 翻译完成（{len(out)} 字符）")
        return {"ok": True, "result": {"source": src, "direction": direction, "text": out}}
    except Exception as e:
        _HOST.log(f"❌ 翻译异常: {e}")
        return {"ok": False, "error": str(e)}


def _models_status():
    cfg = _HOST.translator_config
    hy_ready = bool(cfg.hy_model_path) and os.path.exists(cfg.hy_model_path)
    hy_runtime_ready = _llama_importable()
    opus_ready = bool(getattr(cfg, "opusmt_base", "")) and os.path.isdir(cfg.opusmt_base)
    stt = _HOST.stt_config
    stt_ready = False
    if os.path.isdir(stt.model_dir):
        enc = os.path.join(stt.model_dir, "model.onnx")
        enc8 = os.path.join(stt.model_dir, "model.int8.onnx")
        tok = os.path.join(stt.model_dir, "tokens.txt")
        stt_ready = (os.path.exists(enc) or os.path.exists(enc8)) and os.path.exists(tok)
    warnings = []
    if not hy_runtime_ready:
        warnings.append("Hy-MT2-1.8B 运行依赖（llama-cpp-python）未安装，请在引擎设置中一键安装")
    elif not hy_ready:
        warnings.append("Hy-MT2-1.8B 模型文件未配置（settings 中指定 GGUF 路径后可用）")
    if not opus_ready:
        warnings.append("Opus-MT 模型目录未配置（settings 中指定目录后可用）")
    if not stt_ready:
        warnings.append("语音识别模型缺失（需 model.onnx + tokens.txt），屏幕字幕 / 语音翻译不可用")
    return {
        "ok": True,
        "result": {
            "engine": cfg.engine,
            "hy_ready": hy_ready, "hy_runtime_ready": hy_runtime_ready,
            "opus_ready": opus_ready, "stt_ready": stt_ready,
            "ocr_ready": _ocr_ready(),
            "warnings": warnings,
        },
    }


def _ocr_ready():
    try:
        from config import ui_config
        cache = ui_config.CONFIG.model_path("paddle_cache")
        if cache and os.path.isdir(cache):
            return True
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


# ════════════════════════════════════════════
#  JSON-RPC 处理
# ════════════════════════════════════════════


def _handle(req_id, method, params, log):
    # ── 通用 ──
    if method == "ping":
        return {"pong": True}
    if method == "translation.langs":
        return {"ok": True, "result": LANGS}
    if method == "translation.directions":
        return {"ok": True, "result": {
            "order": TRANSLATOR.DIRECTION_ORDER,
            "labels": TRANSLATOR.DIRECTION_LABELS,
        }}

    # ── 翻译 ──
    if method == "translation.translate":
        return _handle_translate(params, log)
    if method == "translation.translate.file":
        in_p = (params or {}).get("input")
        out_p = (params or {}).get("output")
        direction = (params or {}).get("direction", "auto")
        if not in_p or not out_p:
            return {"ok": False, "error": "需提供 input 与 output 路径"}
        _HOST.log(f"📄 文件翻译：{os.path.basename(in_p)} → {os.path.basename(out_p)}")
        ok = TRANSLATOR.translate_file(in_p, out_p, log=log, config=_HOST.translator_config)
        return {"ok": ok, "result": {"output": out_p}}

    # ── 引擎 / 配置 ──
    if method == "translation.engine.get":
        return {"ok": True, "result": {
            "engine": _HOST.translator_config.engine,
            "labels": ENGINE_LABELS, "order": ENGINE_ORDER,
        }}
    if method == "translation.engine.installhy":
        # 按需安装 Hy 引擎运行依赖（llama-cpp-python，Windows 需源码编译，后台执行）
        if _llama_importable():
            return {"ok": True, "result": {"installing": False,
                                           "message": "Hy 引擎运行依赖已就绪"}}
        if _HY_INSTALLING:
            return {"ok": True, "result": {"installing": True,
                                           "message": "正在安装 Hy 引擎依赖…"}}
        threading.Thread(target=_install_hy_dep, daemon=True).start()
        return {"ok": True, "result": {"installing": True,
                                       "message": "已开始安装 Hy 引擎依赖（首次需编译，请耐心等待）"}}
    if method == "translation.engine.set":
        engine = (params or {}).get("engine")
        if engine not in ENGINE_ORDER:
            return {"ok": False, "error": f"未知引擎: {engine}"}
        _HOST.translator_config.engine = engine
        presets.save_last_translator_config(_HOST.translator_config)
        _HOST.log(f"🌐 翻译引擎: {ENGINE_LABELS[engine]}")
        _HOST.emit("engine", {"engine": engine})
        return {"ok": True, "result": {"engine": engine}}

    if method == "translation.config.get":
        return {"ok": True, "result": _HOST.translator_config.to_dict()}
    if method == "translation.config.set":
        updates = (params or {}).get("config") or (params or {}).get("updates") or {}
        if not isinstance(updates, dict):
            return {"ok": False, "error": "config 需为对象"}
        applied = {}
        for k, v in updates.items():
            if hasattr(_HOST.translator_config, k):
                try:
                    setattr(_HOST.translator_config, k, v)
                    applied[k] = v
                except Exception:
                    continue
        presets.save_last_translator_config(_HOST.translator_config)
        _HOST.log("⚙️ 翻译配置已更新")
        return {"ok": True, "result": {"applied": applied}}

    if method == "translation.stt.config.get":
        return {"ok": True, "result": _HOST.stt_config.to_dict()}
    if method == "translation.stt.config.set":
        updates = {}
        for src in ((params or {}).get("config"), (params or {}).get("updates")):
            if isinstance(src, dict):
                updates.update(src)
        for k, v in updates.items():
            if hasattr(_HOST.stt_config, k):
                try:
                    setattr(_HOST.stt_config, k, v)
                except Exception:
                    continue
        presets.save_last_stt_config(_HOST.stt_config)
        return {"ok": True, "result": _HOST.stt_config.to_dict()}

    if method == "translation.screen.config.get":
        return {"ok": True, "result": _HOST.screen_region_config.to_dict()}
    if method == "translation.screen.config.set":
        updates = (params or {}).get("config") or {}
        for k, v in (updates or {}).items():
            if hasattr(_HOST.screen_region_config, k):
                try:
                    setattr(_HOST.screen_region_config, k, v)
                except Exception:
                    continue
        presets.save_last_screen_region_config(_HOST.screen_region_config)
        return {"ok": True, "result": _HOST.screen_region_config.to_dict()}

    if method == "translation.models.status":
        return _models_status()

    if method == "translation.resources.status":
        from resources import models_status
        return models_status(_HOST)

    if method == "translation.resources.set":
        from resources import models_set
        return models_set(_HOST, (params or {}).get("item") or (params or {}))

    if method == "translation.preload":
        try:
            from preload import start_preload
            timing = (params or {}).get("timing") or "startup"
            ok = start_preload(_HOST.translator_config, timing=timing)
            return {"ok": True, "result": {"started": ok}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 应用控制（五种实时功能）──
    if method == "translation.app.start":
        name = (params or {}).get("name") or (params or {}).get("key")
        if name not in _APP_ROWS:
            return {"ok": False, "error": f"未知应用: {name}"}
        start_app(_HOST, name)
        return {"ok": True, "result": {"name": name, "running": is_app_running(_HOST, name)}}

    if method == "translation.app.stop":
        name = (params or {}).get("name") or (params or {}).get("key")
        if name not in _APP_ROWS:
            return {"ok": False, "error": f"未知应用: {name}"}
        stop_app(_HOST, name)
        return {"ok": True, "result": {"name": name, "running": False}}

    if method == "translation.app.toggle":
        name = (params or {}).get("name") or (params or {}).get("key")
        if name not in _APP_ROWS:
            return {"ok": False, "error": f"未知应用: {name}"}
        toggle_app(_HOST, name)
        return {"ok": True, "result": {"name": name, "running": is_app_running(_HOST, name)}}

    if method == "translation.app.status":
        states = app_states(_HOST)
        return {"ok": True, "result": {
            "apps": states,
            "screen": _SCREEN_APP_CARD,
            "voice": _VOICE_APP_CARD,
        }}

    if method == "translation.app.set_region":
        p = params or {}
        x, y, w, h = p.get("x"), p.get("y"), p.get("w"), p.get("h")
        if None in (x, y, w, h):
            return {"ok": False, "error": "需要 x/y/w/h"}
        region = _HOST.screen_region_config
        region.fixed = True
        region.set_rect(x, y, w, h)
        presets.save_last_screen_region_config(region)
        _HOST.log(f"📌 固定截图框已设置: ({x}, {y}) {w}×{h}")
        return {"ok": True, "result": region.to_dict()}

    if method == "translation.app.region":
        # 触发原生全屏框选，回传 bbox
        from apps.overlay_client import select_region
        rect = select_region(
            border=_HOST.screen_region_config.border_color, timeout=120.0)
        if rect is None:
            return {"ok": False, "error": "已取消区域选择"}
        region = _HOST.screen_region_config
        region.fixed = False
        region.set_rect(*rect)
        presets.save_last_screen_region_config(region)
        _HOST.log(f"📌 已选择区域: {rect[2]}×{rect[3]}")
        return {"ok": True, "result": {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]}}

    # ── 日志 ──
    if method == "translation.logs":
        return {"ok": True, "result": {"logs": list(_HOST._logs[-200:])}}
    if method == "translation.logs.clear":
        with _WRITE_LOCK:
            _HOST._logs = []
        return {"ok": True, "result": {}}

    return None


def _dispatch(req_id, method, params):
    """请求 → 处理。慢操作丢线程池，普通操作同步（保序）"""
    _SEQ[0] += 1
    log = _HOST.log
    try:
        result = _handle(req_id, method, params, log)
    except Exception as e:
        _reply(req_id, error={"code": -32603, "message": str(e)})
        return
    if result is None:
        _reply(req_id, error={"code": -32601, "message": "method not found"})
    else:
        _reply(req_id, result=result)


def _handle_line(req_id, method, params):
    """内部调用（线程池 worker）：同步执行并返回结果字典"""
    return _handle(req_id, method, params, _HOST.log)


def main():
    # 初始化应用实例（悬浮窗信号复位等，由 app_controller 连接）
    init_apps(_HOST)
    # 按配置后台预加载模型（仅 startup 时机匹配才启动）
    try:
        from preload import start_preload
        start_preload(_HOST.translator_config, timing="startup")
    except Exception:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as e:
            _reply(0, error={"code": -32700, "message": str(e)})
            continue
        req_id = msg.get("id", 0)
        method = msg.get("method")
        params = msg.get("params") or {}
        if not method:
            # 内核发来的响应行（如 event.emit 的回执），忽略
            continue
        if method == "ping":
            _reply(req_id, result={"pong": True})
            continue
        # 慢操作（翻译 / 应用控制）丢线程池，其余同步 —— 保证 ping 秒回
        _EXECUTOR.submit(_dispatch, req_id, method, params)


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            stop_all_apps(_HOST)
        except Exception:
            pass