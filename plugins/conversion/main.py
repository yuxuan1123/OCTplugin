# -*- coding: utf-8 -*-
"""conversion —— 文档格式转换插件后端（JSON-RPC 逐行协议）。

底层 1:1 复用 OCTools 的转换业务层（services/conversion）：
直达转换 + 星型自动寻路（BFS over 直达边 + 星型枢纽）+ 跨类 + 批量。

方法：
  ping                -> {"pong": true}
  conversion.formats  -> 两级选择器所需的全部元数据 + 可达目标
  conversion.convert  -> 单文件 / 文件夹批量转换，实时返回逐行日志
"""
import sys

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import json
import atexit

from core import formats as FMT
from services.conversion import (
    convert as _convert,
    reachable_targets,
)
from services.conversion import batch as _batch

# 插件进程退出时关闭 sharp 渲染子进程，防止孤儿 node 进程
try:
    from apps.sharp_client import close_sharp
    atexit.register(close_sharp)
except Exception:
    pass


# 最多保留日志行数（避免超长响应撑爆 8MB）
MAX_LOG_LINES = 400


class _LogCollector:
    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(str(msg))

    def truncated(self):
        lines = self.lines
        if len(lines) > MAX_LOG_LINES:
            lines = lines[:MAX_LOG_LINES] + ["…（日志过多已截断）"]
        return lines


def _format_meta(fmts):
    """格式 id 列表 -> {values, labels, icons}"""
    return {
        "values": list(fmts),
        "labels": [FMT.display(f) for f in fmts],
        "icons": [FMT.icon(f) for f in fmts],
    }


def _categories():
    """[(分类名, 图标, [格式id...])] -> [{key,name,formats}]，key 用家族名"""
    out = []
    for name, icon, fmts in FMT.categories():
        if not fmts:
            continue
        out.append({
            "key": str(icon or ""),
            "name": name,
            "formats": _format_meta(fmts),
        })
    return out


def _pick_config(src_fmt, dst_fmt, cfg):
    """按 (源, 目标) 挑选并构建配置对象；cfg 为前端传来的 dict。
    无法构建时返回 None（引擎将使用默认配置），保证健壮性。
    """
    if not isinstance(cfg, dict) or not cfg:
        return None
    try:
        dst_l = (dst_fmt or "").lower()
        src_l = (src_fmt or "").lower()
        if dst_l in ("txt-ocr", "txt_ocr") and src_l in (
                "png", "jpg", "jpeg", "bmp", "gif", "webp", "tiff",
                "heic", "pdf"):
            from config.ocr_config import OcrConfig
            return OcrConfig.from_dict(cfg)
        if dst_l == "txt" and src_l in ("mp3", "wav", "m4a", "flac", "ogg",
                                        "wma", "aac", "opus", "amr"):
            from config.stt_config import SttConfig
            return SttConfig.from_dict(cfg)
        if dst_l in ("mp3", "wav", "flac", "ogg", "aac", "opus", "m4a",
                     "wma") and src_l in ("txt", "md"):
            from config.tts_config import TtsConfig
            return TtsConfig.from_dict(cfg)
        if dst_l in ("doc", "docx") and src_l == "pdf":
            from config.pdf_docx_config import PdfDocxConfig
            return PdfDocxConfig.from_dict(cfg)
        if dst_l == "docx":
            if src_l in ("png", "jpg", "jpeg", "bmp", "gif", "webp", "tiff",
                         "heic", "svg"):
                from config.image_docx_config import ImageDocxConfig
                return ImageDocxConfig.from_dict(cfg)
            from config.format_config import FormatConfig
            return FormatConfig.from_dict(cfg)
        from config.format_config import FormatConfig
        return FormatConfig.from_dict(cfg)
    except Exception:
        return None


def handle(req_id, method, params):
    params = params or {}
    if method == "ping":
        return {"pong": True}

    if method == "conversion.formats":
        src = (params.get("src") or "").strip().lower()
        mode = (params.get("mode") or "").strip().lower() or "convert"
        reachable = reachable_targets(src, mode) if src else []
        return {
            "ok": True,
            "categories": _categories(),
            "source_formats": FMT.source_ids(),
            "all_formats": FMT.all_ids(),
            "mode": mode,
            "reachable": reachable,
        }

    if method == "conversion.convert":
        input_path = (params.get("input") or params.get("input_path") or "").strip()
        output_path = (params.get("output") or params.get("output_path") or "").strip()
        out_dir = (params.get("out_dir") or "").strip()
        target = (params.get("target") or "").strip().lower()
        cfg = params.get("config") or {}
        src_fmt = (params.get("src_format") or "").strip().lower()

        if not input_path:
            return {"ok": False, "log": ["错误: 未指定源文件/源文件夹"], "done": True}
        if not os.path.exists(input_path):
            return {"ok": False, "log": [f"错误: 路径不存在: {input_path}"], "done": True}

        log = _LogCollector()
        is_dir = os.path.isdir(input_path)

        # ── 文件夹批量转换 ──
        if is_dir:
            if not src_fmt:
                return {"ok": False, "log": ["错误: 文件夹批量需要指定源文件格式"], "done": True}
            if not target:
                return {"ok": False, "log": ["错误: 文件夹批量需要指定目标格式"], "done": True}
            if not out_dir:
                return {"ok": False, "log": ["错误: 文件夹批量需要指定输出文件夹"], "done": True}
            config = _pick_config(src_fmt, target, cfg)
            ok = _batch.batch_convert(input_path, src_fmt, target, out_dir,
                                      log=log, config=config)
            return {"ok": ok, "log": log.truncated(), "done": True}

        # ── 单文件转换 ──
        if not target and not output_path:
            return {"ok": False, "log": ["错误: 未指定目标格式或输出路径"], "done": True}
        if not src_fmt:
            src_fmt = FMT.resolve(os.path.splitext(input_path)[1].lstrip("."))
            if not src_fmt:
                return {"ok": False, "log": [f"错误: 无法识别源文件格式: {input_path}"], "done": True}
        if not output_path:
            base = os.path.splitext(input_path)[0]
            ext = "txt" if target in ("txt-ocr", "txt_ocr") else \
                ("pptx" if target == "pptx-img" else target)
            if not ext:
                return {"ok": False, "log": ["错误: 未指定输出路径"], "done": True}
            output_path = f"{base}.{ext}"
        log("📂 已选择: %s" % input_path)
        log(f"🎯 目标格式: {FMT.display(target or os.path.splitext(output_path)[1].lstrip('.'))}")
        config = _pick_config(src_fmt, target, cfg)
        ok = _convert(input_path, output_path, log=log, config=config, target=target)
        if ok:
            log(f"✅ 转换成功: {output_path}")
        return {"ok": ok, "log": log.truncated(), "done": True}

    if method == "conversion.resources.status":
        from resources import models_status
        return models_status(None)

    if method == "conversion.resources.set":
        from resources import models_set
        return models_set(None, (params or {}).get("item") or (params or {}))

    if method == "conversion.libreoffice_status":
        from core.engines import libreoffice_runtime as _LO
        return {"ok": True, "config": _LO.load_lo_config(),
                "bin": _LO.current_bin(), "state": _LO.export_state()}

    if method == "conversion.libreoffice_set":
        from core.engines import libreoffice_runtime as _LO
        cfg = _LO.save_lo_config(params or {})
        return {"ok": True, "config": cfg, "bin": _LO.current_bin(),
                "state": _LO.export_state()}

    if method == "conversion.libreoffice_install":
        from core.engines import libreoffice_runtime as _LO
        started = _LO.start_install(log=None)
        return {"ok": True, "started": started, "state": _LO.export_state()}

    return None


import threading
from concurrent.futures import ThreadPoolExecutor

_stdout_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2)   # 慢转换放子线程，主循环保持能回心跳


def _write(obj):
    _stdout_lock.acquire()
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    finally:
        _stdout_lock.release()


def _worker(req):
    req_id = req.get("id", 0)
    method = req.get("method")
    try:
        result = handle(req_id, method, req.get("params"))
    except Exception as e:
        _write({"v": 1, "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": "转换出错: %s" % e}})
        return
    out = {"v": 1, "jsonrpc": "2.0", "id": req_id}
    if result is None:
        out["error"] = {"code": -32601, "message": "method not found"}
    else:
        out["result"] = result
    _write(out)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            _write({"v": 1, "jsonrpc": "2.0", "id": 0,
                    "error": {"code": -32700, "message": str(e)}})
            continue
        # 心跳立即回复，绝不能被慢转换阻塞（否则内核 15s 心跳超时踢进程）
        if req.get("method") == "ping":
            _write({"v": 1, "jsonrpc": "2.0", "id": req.get("id", 0),
                    "result": {"pong": True}})
            continue
        try:
            _pool.submit(_worker, req)
        except RuntimeError:
            _write({"v": 1, "jsonrpc": "2.0", "id": req.get("id", 0),
                    "error": {"code": -32000, "message": "服务繁忙，请稍后再试"}})


if __name__ == "__main__":
    main()