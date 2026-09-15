# -*- coding: utf-8 -*-
"""tree —— 目录树迁移后的无界面后端（自包含 stdin/stdout JSON-RPC）。

目录树生成推荐走前端（webkitdirectory 选文件夹在 JS 里递归构建，无后端、
无权限问题）。本后端另保留一个 `tree.scan` 桥：输入绝对路径用 os.walk
扫描生成目录树文本，供“路径扫描”入口使用。仅用标准库。
"""
import json
import os
import sys


def _scan(root, max_depth, ignore_hidden):
    """os.walk 式扫描 root，返回目录树文本字符串。"""
    root = os.path.abspath(os.path.expanduser(str(root or "")))
    if not os.path.isdir(root):
        return "⚠ 路径不是有效目录：%s" % root

    def _should_skip(name):
        if not ignore_hidden and name.startswith("."):
            return True
        return False

    lines = [os.path.basename(root) or root]

    def walk(current, prefix, depth):
        try:
            entries = sorted(
                os.scandir(current),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except (OSError, PermissionError):
            lines.append("%s⚠ 无法访问 %s" % (prefix, os.path.basename(current)))
            return
        entries = [e for e in entries if not _should_skip(e.name)]
        for idx, e in enumerate(entries):
            is_last = idx == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append("%s%s%s" % (prefix, connector, e.name))
            if e.is_dir() and (max_depth is None or depth < max_depth):
                child_prefix = prefix + ("    " if is_last else "│   ")
                walk(e.path, child_prefix, depth + 1)

    walk(root, "", 1)
    return "\n".join(lines)


def handle(req_id, method, params):
    params = params or {}
    if method == "ping":
        return {"pong": True}
    if method == "tree.scan":
        depth = params.get("maxDepth")
        if depth is None or depth == 0:
            depth = None
        return _scan(params.get("root", ""), depth, bool(params.get("ignoreHidden", False)))
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
