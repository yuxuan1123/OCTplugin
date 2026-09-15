# -*- coding: utf-8 -*-
"""demo_np2（numpy 2.2.1）：共享函数 np2.version 的提供方（阶段C 被调方）。

stdio JSON-RPC：stdout 只输出协议帧；处理 hello / npversion / ping。
"""
import json
import sys

try:
    import numpy
    _np = numpy.__version__
except ImportError:
    _np = None


def handle(method, params):
    if method in ("hello", "npversion"):
        return {"numpy": _np, "python": sys.version.split()[0],
                "inVenv": sys.prefix != sys.base_prefix}
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
        except ValueError:
            continue
        # 只处理内核发给本插件的请求（本项目为叶节点，无需主动发请求）
        if "method" not in req:
            continue
        req_id = req.get("id", 0)
        result = handle(req.get("method"), req.get("params"))
        out = {"v": 1, "jsonrpc": "2.0", "id": req_id}
        if result is None:
            out["error"] = {"code": -32601, "message": "method not found"}
        else:
            out["result"] = result
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()