# -*- coding: utf-8 -*-
"""merge —— 合并/拼接插件后端（自包含 stdin/stdout JSON-RPC）。

架构：
  - 逐行读 stdin，按「是否带 method」解复用：
      * 带 method + id  → 前端 RPC 请求，交给线程池 worker 处理
      * 无 method 有 id → 内核对 registry.call 的回复，投递给 bridge 的 pending future
  - stdout 写锁 + ThreadPoolExecutor，慢任务不阻塞心跳/其它请求
  - 「转换」经 bridge（registry.call → conversion.convert）委托 conversion 插件；
    「合并」由本插件自带合并器完成（same_format / image / media / audio / video）

方法：
  merge.ping     -> {"pong": true}
  merge.formats  -> 两级选择器元数据 + src 的【拼接】可达目标
  merge.concat   -> 把文件夹内全部 src 文件合并为单个 dst 文件（逐行返回日志）
  merge.presets  -> 四类设置项预设（docx/tts/stt/img/ocr）的 列表/读取/保存/删除
"""
import sys

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import json

from core import formats as FMT
from services.merge import planner, bridge
from services.merge.concat import concat as _concat


# 最多保留日志行数（避免超长响应撑爆 8MB）
MAX_LOG_LINES = 600


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


def _presets(params):
    params = params or {}
    kind = (params.get("kind") or "").strip().lower()
    action = (params.get("action") or "list").strip().lower()
    if kind not in ("docx", "tts", "stt", "img", "ocr"):
        return {"ok": False, "error": "未知预设类型: %s" % kind}
    from config import models
    key = "img" if kind == "img" else kind
    name = params.get("name")

    if action == "list":
        return {"ok": True, "presets": models.list_presets(key),
                "defaults": models.DEFAULTS[key]()}
    if action == "load":
        if not name:
            return {"ok": False, "error": "未指定预设名"}
        cfg = models.load_preset(key, name)
        if cfg is None:
            return {"ok": False, "error": "预设不存在: %s" % name}
        return {"ok": True, "config": cfg}
    if action == "save":
        if not name:
            return {"ok": False, "error": "未指定预设名"}
        cfg = params.get("config")
        if not isinstance(cfg, dict):
            return {"ok": False, "error": "config 必须是对象"}
        models.save_preset(key, name, cfg)
        return {"ok": True, "presets": models.list_presets(key)}
    if action == "delete":
        if not name:
            return {"ok": False, "error": "未指定预设名"}
        models.delete_preset(key, name)
        return {"ok": True, "presets": models.list_presets(key)}
    return {"ok": False, "error": "未知操作: %s" % action}


def handle(req_id, method, params):
    params = params or {}

    if method == "ping":
        return {"pong": True}

    if method == "merge.formats":
        src = (params.get("src") or "").strip().lower()
        return planner.formats_meta(src)

    if method == "merge.scan":
        folder = (params.get("input") or params.get("folder") or "").strip()
        if not folder or not os.path.isdir(folder):
            return {"ok": False, "error": "文件夹不存在: %s" % folder}
        from collections import Counter
        cnt = Counter()
        files = 0
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            return {"ok": False, "error": "读取文件夹失败: %s" % e}
        for n in names:
            p = os.path.join(folder, n)
            if os.path.isfile(p):
                ext = os.path.splitext(n)[1].lstrip(".").lower()
                cnt[ext if ext else "(无)"] += 1
                files += 1
        if files == 0:
            return {"ok": True, "count": 0, "src_format": "",
                    "formats": [], "files": 0}
        dominant = cnt.most_common(1)[0][0]
        src_auto = dominant if dominant in FMT.ALL_FORMATS else ""
        return {"ok": True, "count": files, "src_format": src_auto,
                "formats": [(ext, c) for ext, c in cnt.most_common()
                            if ext in FMT.ALL_FORMATS],
                "files": files}

    if method == "merge.presets":
        return _presets(params)

    if method == "merge.concat":
        folder = (params.get("input") or params.get("folder") or
                  params.get("input_dir") or "").strip()
        output = (params.get("output") or "").strip()
        src_fmt = (params.get("src_format") or "").strip().lower()
        target = (params.get("target") or "").strip().lower()
        cfg = params.get("config") or {}

        if not folder:
            return {"ok": False, "log": ["错误: 未指定源文件夹"], "done": True}
        if not os.path.isdir(folder):
            return {"ok": False, "log": [f"错误: 文件夹不存在: {folder}"], "done": True}
        if not src_fmt:
            return {"ok": False, "log": ["错误: 未指定源文件格式"], "done": True}
        if not target:
            return {"ok": False, "log": ["错误: 未指定目标格式"], "done": True}
        if not output:
            return {"ok": False, "log": ["错误: 未指定输出路径"], "done": True}
        if target not in FMT.ALL_FORMATS:
            return {"ok": False, "log": [f"错误: 不支持的目标格式: {target}"], "done": True}

        log = _LogCollector()
        log(f"🎯 目标格式: {FMT.display(target)}")
        ok = _concat(folder, src_fmt, target, output, log=log, config=cfg)
        if ok:
            log(f"✅ 拼接完成 → {output}")
        return {"ok": ok, "log": log.truncated()}

    return None


# ════════════════════════════════════════════
#  线程池 + stdout 锁 + bridge 的 pending future 表
# ════════════════════════════════════════════
import threading
from concurrent.futures import ThreadPoolExecutor

_stdout_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4)   # 慢拼接放子线程，主循环保持能回心跳

_PEND = {}  # rid -> concurrent.futures.Future（bridge 跨插件调用暂存）


def _write(obj):
    _stdout_lock.acquire()
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    finally:
        _stdout_lock.release()


bridge.configure(_write, _PEND)


def _dispatch(req):
    req_id = req.get("id", 0)
    method = req.get("method")
    try:
        result = handle(req_id, method, req.get("params"))
    except Exception as e:
        _write({"v": 1, "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": "merge 处理出错: %s" % e}})
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

        # 心跳立即回复，绝不能被慢拼接阻塞
        if req.get("method") == "ping":
            _write({"v": 1, "jsonrpc": "2.0", "id": req.get("id", 0),
                    "result": {"pong": True}})
            continue

        # 无 method、有 id → 内核 reply 给 registry.call，完成 bridge future
        if not req.get("method") and "id" in req:
            rid = req.get("id")
            fut = _PEND.pop(rid, None)
            if fut is not None:
                if "error" in req:
                    fut.set_result({"ok": False, "error":
                                    req["error"].get("message", "registry.call 失败")})
                else:
                    res = req.get("result") or {}
                    fut.set_result(res)
                continue

        try:
            _pool.submit(_dispatch, req)
        except RuntimeError:
            _write({"v": 1, "jsonrpc": "2.0", "id": req.get("id", 0),
                    "error": {"code": -32000, "message": "merge 服务繁忙，请稍后再试"}})


if __name__ == "__main__":
    main()