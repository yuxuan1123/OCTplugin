# -*- coding: utf-8 -*-
"""demo_hello 插件：stdio JSON-RPC 示例 + gate 演示（FR-7）。

协议：stdout 只输出 JSON Lines，均为协议帧；日志走 stderr。
- 插件 → 内核：{"id":n,"method":"...","params":{}}   （如 gate.file_read）
- 内核 → 插件：{"id":n,"result":...} 或 {"error":...}
- 内核 → 插件请求：{"id":n,"method":"..."}; 插件回 {"id":n,"result":...} 或 error
"""
import json
import sys

try:
    import packaging
    _packaging = packaging.__version__
except ImportError:
    _packaging = None  # 依赖未装；安装隔离依赖后重启插件即生效

_req_seq = [0]


def kernel_call(cmd, params=None):
    """向内核发一次性请求并阻塞等响应。"""
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
        # 内核对我们的响应：id 匹配且含 result 或 error
        if "method" in msg:
            # 内核主动发来的请求（双向），先回 PENDING 忽略简化为不支持
            sys.stdout.write(json.dumps(
                {"v": 1, "jsonrpc": "2.0", "id": msg.get("id", 0),
                 "result": {}}) + "\n")
            sys.stdout.flush()
            continue
        if msg.get("id") == req["id"]:
            return msg
    return None


def handle(client_req_id, method, params):
    if method == "hello":
        name = (params or {}).get("name") or "OCTplugin"
        return {"message": f"Hello, {name}!", "python": sys.version.split()[0],
                "packaging": _packaging, "inVenv": sys.prefix != sys.base_prefix}
    if method == "echo":
        return {"echo": params}
    if method == "readfile":
        # 经内核 gate 读文件；未授权 file_read 时内核回 error(-32005)
        resp = kernel_call("gate.file_read", params)
        if resp is None:
            return {"error": "no kernel response"}
        if "error" in resp:
            return {"error": resp["error"]}
        return {"kernel": resp.get("result")}
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