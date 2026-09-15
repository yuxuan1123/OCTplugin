# -*- coding: utf-8 -*-
"""demo_np1（numpy 1.26.4）：验证依赖隔离 + (阶段C) 经内核跨插件调用 demo_np2。

标准 stdio JSON-RPC：
- 插件 → 内核：{"id":n,"method":"...","params":{}}   （gate.* / registry.call）
- 内核 → 插件：{"id":n,"result":...} 或 {"error":...}
- 内核 → 插件请求：{"id":n,"method":"..."}；插件回 {"id":n,"result":...}
"""
import json
import sys

try:
    import numpy
    _np = numpy.__version__
except ImportError:
    _np = None  # 隔离依赖未装；deps.install + restart 后出现

_req_seq = [0]


def kernel_call(cmd, params=None):
    """向内核发一次性请求并阻塞等响应（处理交错请求/心跳）。"""
    _req_seq[0] += 1
    req = {"v": 1, "jsonrpc": "2.0", "id": _req_seq[0],
           "method": cmd, "params": params or {}}
    sys.stdout.write(json.dumps(req) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "method" in msg:
            # 内核发来的请求（如 ping / 其它并发调用），应答后继续等待
            sys.stdout.write(json.dumps(
                {"v": 1, "jsonrpc": "2.0", "id": msg.get("id", 0),
                 "result": {"pong": True}}) + "\n")
            sys.stdout.flush()
            continue
        if msg.get("id") == req["id"]:
            return msg
    return None


def handle(req_id, method, params):
    if method == "hello":
        return {"numpy": _np, "python": sys.version.split()[0],
                "inVenv": sys.prefix != sys.base_prefix}
    if method == "npversion":
        return {"numpy": _np}
    if method == "call_peer":
        # 阶段C：跨插件——demo_np1 → 内核 → demo_np2.np2.version
        resp = kernel_call("registry.call", {"name": "np2.version", "params": {}})
        if resp is None:
            return {"error": "no kernel response"}
        if "error" in resp:
            return {"error": resp["error"]}
        # resp.result = {"ok":true,"result":{numpy:"2.2.1"}}
        inner = (resp.get("result") or {}).get("result") or {}
        return {"peer": inner.get("numpy"), "_full": resp.get("result")}
    if method == "emit_event":
        # 阶段E：插件 → 内核(event.emit) → 宿主 广播一条事件
        resp = kernel_call("event.emit", {"type": "demo.tick", "data": {"n": _np}})
        return {"sent": True, "kernel": resp}
    if method == "ping":
        return {"pong": True}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            sys.stdout.write(json.dumps(
                {"v": 1, "jsonrpc": "2.0", "id": 0,
                 "error": {"code": -32700, "message": str(e)}}) + "\n")
            sys.stdout.flush()
            continue
        req_id = req.get("id", 0)
        result = handle(req_id, req.get("method"), req.get("params"))
        out = {"v": 1, "jsonrpc": "2.0", "id": req_id}
        if result is None:
            out["error"] = {"code": -32601, "message": "method not found"}
        else:
            out["result"] = result
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()