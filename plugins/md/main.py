# -*- coding: utf-8 -*-
"""md —— Markdown 管理后端（完整移植自 OCTools plugins/md）。

提供 md 目录的扫描(.md + 指向 .md 的 .lnk)、读、写、新建、删除、重命名，
以及 md_dir 目录的读取/切换与配置持久化。输出 Frontend 需要的展示字段。

仅用 Python 标准库 + ctypes(解析 .lnk)，无第三方依赖。
stdin/stdout 逐行 JSON-RPC，自包含可独立运行。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sys
import struct
import time
from datetime import datetime
from pathlib import Path

MD_SUFFIX = ".md"
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_DIR = Path(__file__).resolve().parent
_CACHE = _DIR / ".cache"
_CONFIG = _CACHE / "config.json"

# Windows 下 Python 进程默认按 locale(cp936) 编解码管道；内核以 UTF-8 读写，
# 若不固定为 UTF-8，中文文件名/路径经 stdin 传入会被解乱而找不到文件。
for _s in (sys.stdin, sys.stdout):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ── 配置（md 目录默认：插件目录下 md/，可切换并持久化）────────────
def _default_md_dir():
    return str(_DIR / "md")


def _load_config():
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_config(cfg):
    _CACHE.mkdir(parents=True, exist_ok=True)
    _CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_dir():
    cfg = _load_config()
    d = cfg.get("md_dir") or _default_md_dir()
    Path(d).mkdir(parents=True, exist_ok=True)
    return d


# ── 文件名工具（对应 md_core.safe_name / unique_name）────────────
def safe_name(name):
    name = (name or "").strip()
    name = os.path.basename(name)
    name = _ILLEGAL.sub("", name).strip().strip(".")
    if not name:
        raise ValueError("文件名不能为空")
    if not name.lower().endswith(MD_SUFFIX):
        name += MD_SUFFIX
    return name


def unique_name(directory, name):
    stem, ext = os.path.splitext(name)
    candidate, i = name, 2
    while (Path(directory) / candidate).exists():
        candidate = f"{stem} ({i}){ext}"
        i += 1
    return candidate


# ── 展示辅助 ────────────────────────────────────────────────
def first_heading(text, limit=80):
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:limit]
    return ""


def fmt_size(num):
    if not num:
        return ""
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num / (1024 * 1024):.1f} MB"


def fmt_time(ts):
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    now = datetime.now()
    if d.date() == now.date():
        day = "今天"
    elif d.year == now.year:
        day = f"{d.month:02d}-{d.day:02d}"
    else:
        day = d.strftime("%Y-%m-%d")
    return f"{day} {d.hour:02d}:{d.minute:02d}"


# ── .lnk 快捷方式解析（纯 ctypes：读原始字节挖掘含 .md 的目标路径）──
def _split_wstring(data, enc):
    """把二进制按宽/窄字符串切割成若干可打印片段，尽力找路径。"""
    if enc == "utf-16le":
        step, null = 2, b"\x00\x00"
        try:
            s = data.decode("utf-16le", "ignore")
        except Exception:  # noqa: BLE001
            return []
        return [seg for seg in s.replace("\x00", "\n").split("\n") if seg.strip()]
    try:
        s = data.decode("mbcs", "ignore")
    except Exception:  # noqa: BLE001
        return []
    return [seg for seg in s.replace("\x00", "\n").split("\n") if seg.strip()]


def _resolve_lnk(lnk_path):
    """解析 .lnk 目标 .md 的绝对路径；失败返回空串。"""
    try:
        data = Path(lnk_path).read_bytes()
    except OSError:
        return ""
    cands = []
    for enc in ("utf-16le", "mbcs"):
        for seg in _split_wstring(data, enc):
            low = seg.strip().rstrip("\x00").strip()
            if not low.lower().endswith(MD_SUFFIX):
                continue
            # 仅接受形如盘符路径或 UNC 且不含控制符的
            m = re.search(r"^[A-Za-z]:[\\/].*$|^[\\/]{2}", low)
            if m and len(low) < 2048:
                cands.append(low)
    # 按存在性优先，其次长度
    for c in sorted(cands, key=len, reverse=True):
        p = Path(c)
        if p.exists():
            return c
    return cands[0] if cands else ""


def _c_type(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


# ── 扫描列表（对应 md_core.list_md_files）────────────────────────
def list_md_files(directory):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    real_paths = set()
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.suffix.lower() == MD_SUFFIX:
                real_paths.add(str(entry.resolve()))
    except OSError:
        pass

    out = []
    for entry in directory.iterdir():
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        suffix = entry.suffix.lower()
        target, shortcut_path = None, ""
        if suffix == MD_SUFFIX:
            target = entry
        elif suffix == ".lnk":
            t = _resolve_lnk(entry)
            if not t:
                continue
            if str(Path(t).resolve()) in real_paths:
                continue
            target = Path(t)
            shortcut_path = str(entry.resolve())
        else:
            continue
        try:
            st = target.stat()
        except OSError:
            continue
        title = ""
        try:
            with open(target, "r", encoding="utf-8", errors="ignore") as f:
                title = first_heading(f.read(4096))
        except OSError:
            pass
        out.append({
            "name": target.name,
            "path": str(target.resolve()),
            "size": _c_type(st.st_size),
            "mtime": _c_type(st.st_mtime),
            "size_text": fmt_size(st.st_size),
            "time_text": fmt_time(st.st_mtime),
            "title": title,
            "shortcut_path": shortcut_path,
        })
    out.sort(key=lambda f: f.get("mtime", 0), reverse=True)
    return out


def read_md(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_md(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def create_md(directory, name, body=""):
    Path(directory).mkdir(parents=True, exist_ok=True)
    target = Path(directory) / unique_name(directory, safe_name(name))
    write_md(target, body)
    return str(target)


def delete_md(path):
    Path(path).unlink(missing_ok=True)


def rename_md(path, new_name):
    src = Path(path)
    target = src.parent / safe_name(new_name)
    if target.exists() and target != src:
        raise FileExistsError(f"目标已存在：{target.name}")
    src.rename(target)
    return str(target)


# ── JSON-RPC 处理 ───────────────────────────────────────────
def handle(req_id, method, params):
    params = params or {}
    if method == "ping":
        return {"ok": True, "pong": True}
    if method == "md.list":
        try:
            files = list_md_files(Path(_md_dir()))
            return {"ok": True, "dir": _md_dir(), "count": len(files), "files": files}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if method == "md.read":
        p = (params.get("path") or "").strip()
        try:
            return {"ok": True, "path": str(Path(p).resolve()), "content": read_md(p)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if method == "md.save":
        p = (params.get("path") or "").strip()
        try:
            write_md(p, params.get("content") or "")
            return {"ok": True, "path": str(Path(p).resolve())}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if method == "md.create":
        name = (params.get("name") or "").strip()
        try:
            path = create_md(_md_dir(), name, params.get("body") or "")
            return {"ok": True, "path": path, "name": os.path.basename(path)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if method == "md.delete":
        p = (params.get("path") or "").strip()
        try:
            # 快捷方式传 shortcut_path：删 .lnk 本体，不动源文件
            delete_md(p)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if method == "md.rename":
        p = (params.get("path") or "").strip()
        new = (params.get("newName") or "").strip()
        try:
            new_path = rename_md(p, new)
            return {"ok": True, "path": new_path, "name": os.path.basename(new_path)}
        except (ValueError, FileExistsError, OSError) as e:
            return {"ok": False, "error": str(e)}
    if method == "md.getDir":
        return {"ok": True, "dir": _md_dir()}
    if method == "md.setDir":
        p = (params.get("dir") or "").strip()
        try:
            Path(p).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        cfg = _load_config()
        cfg["md_dir"] = str(Path(p).resolve())
        _save_config(cfg)
        return {"ok": True, "dir": cfg["md_dir"]}
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