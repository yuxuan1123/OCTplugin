# -*- coding: utf-8 -*-
"""demo_web —— iframe 桥验证插件。

无界面 Python 后端：stdin 读 JSON-RPC 请求，stdout 写响应。
前端在宿主 iframe 中直连内核 WS，经 plugin.call 调本插件。
"""
import datetime
import json
import sys


def handle(req_id, method, params):
    if method == "hello":
        return {
            "ok": True,
            "now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": "self",
            "python": sys.version.split()[0],
            "inVenv": sys.prefix != sys.base_prefix,
        }
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
            sys.stdout.write(json.dumps({"v": 1, "jsonrpc": "2.0", "id": 0,
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