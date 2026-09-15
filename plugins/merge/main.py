# -*- coding: utf-8 -*-
"""merge —— 合并/追加插件后端（自包含 stdin/stdout JSON-RPC）。

核心：把文本追加/合并到目标文件（需要写文件，必须走后端）。
复用 color_pick/main.py 骨架，仅用 Python 标准库，无任何第三方依赖。

方法：
- ping               : 连通性探测 {'pong': true}
- merge.append       : 追加文本到文件；返回 {ok, count} 或 {ok:false, error}
- merge.read         : 读取文件全部内容；返回 {content} 或 {ok:false, error}
"""
import json
import os
import sys
import time


def handle(req_id, method, params):
    if method == "ping":
        return {"pong": True}

    if method == "merge.append":
        return _append(params)

    if method == "merge.read":
        return _read(params)

    return None


def _append(params):
    params = params or {}
    path = (params.get("path") or "").strip()
    content = params.get("content") or ""
    if not path:
        return {"ok": False, "error": "目标文件路径为空"}
    if not isinstance(content, str):
        return {"ok": False, "error": "追加内容必须是文本"}

    opts = params.get("opts") or {}
    newline = bool(opts.get("newline", False))
    timestamp = bool(opts.get("timestamp", False))
    prefix = opts.get("prefix") or ""

    # 组装要追加的片段
    seg = ""
    if newline:
        seg += "\n"
    if prefix:
        seg += str(prefix) + "\n"
    if timestamp:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        seg += "[" + ts + "] "
    seg += content

    # 追加写文件（文件不存在则创建）
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(seg)
    except FileNotFoundError:
        return {"ok": False, "error": "目标目录不存在，无法创建文件: %s" % os.path.dirname(path)}
    except PermissionError:
        return {"ok": False, "error": "无写文件权限: %s" % path}
    except IsADirectoryError:
        return {"ok": False, "error": "目标路径是目录而非文件: %s" % path}
    except OSError as e:
        return {"ok": False, "error": "写入文件失败: %s" % e}
    except Exception as e:
        return {"ok": False, "error": "写入文件异常: %s" % e}

    return {"ok": True, "count": len(seg)}


def _read(params):
    params = params or {}
    path = (params.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "目标文件路径为空"}
    if not os.path.exists(path):
        return {"ok": False, "error": "文件不存在: %s" % path}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {"ok": True, "content": f.read()}
    except PermissionError:
        return {"ok": False, "error": "无读文件权限: %s" % path}
    except UnicodeDecodeError:
        return {"ok": False, "error": "文件不是 utf-8 文本，无法预览: %s" % path}
    except Exception as e:
        return {"ok": False, "error": "读取文件失败: %s" % e}


def ok(success, error=None):
    return {"ok": success, "error": error}


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
