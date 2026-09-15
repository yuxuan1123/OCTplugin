# -*- coding: utf-8 -*-
"""conversion —— 转换（单位换算）迁移后的无界面后端。

单位换算全部在前端 JS 完成（纯数学，无卡顿、无权限问题）；
本后端仅提供桥连通性探测，保证与 host/kernel 正常挂载。
"""
import json
import sys


def handle(req_id, method, params):
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