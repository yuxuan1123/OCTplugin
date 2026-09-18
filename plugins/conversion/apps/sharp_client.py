# -*- coding: utf-8 -*-
"""
conversion/apps/sharp_client.py
────────────────────────────────
sharp 渲染子进程客户端（懒加载）。

仅在真正执行 SVG → 位图 / 图像转码时才 spawn Node 子进程（sharp_worker.js）
并加载 sharp；不执行渲染则始终不启动，零开销。

  SharpClient.render_svg(input, output, width=None, height=None, scale=None)
    SVG → PNG。首次调用会启动子进程（含 sharp 加载，约数百 ms）。
  SharpClient.close()
    关闭子进程（防孤儿进程），供插件退出钩子调用。
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sharp_worker.js")

if sys.platform == "win32":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
else:
    _CREATE_NO_WINDOW = 0

# 单次请求超时：Node + sharp 启动已由进程 spawn 承担，请求本身应很快
_REQUEST_TIMEOUT = 120.0


class SharpClient:
    """sharp 子进程客户端（进程内单例复用）。"""

    def __init__(self):
        self._proc = None
        self._reader = None
        self._pending = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._start_error = None

    # ── 生命周期 ──

    def _node_exe(self):
        exe = shutil.which("node")
        if exe:
            return exe
        raise RuntimeError(
            "未找到 node 可执行文件，无法启动 sharp 渲染进程。"
            "请安装 Node.js 并确保 node 在 PATH 中。")

    def _ensure(self):
        """确保子进程已启动（仅首次渲染时触发）。"""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return
        if self._start_error is not None:
            raise self._start_error
        try:
            node = self._node_exe()
            flags = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                     "stderr": subprocess.DEVNULL, "bufsize": 0,
                     "creationflags": _CREATE_NO_WINDOW}
            self._proc = subprocess.Popen([node, _WORKER], **flags)
            self._pending = {}
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        except Exception as e:
            self._start_error = RuntimeError(f"启动 sharp 渲染进程失败: {e}")
            self._proc = None
            raise self._start_error

    def _read_loop(self):
        try:
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                with self._cond:
                    entry = self._pending.pop(msg.get("id"), None)
                    if entry:
                        entry[0] = msg
                        self._cond.notify_all()
        except Exception:
            pass
        # 进程意外退出：唤醒所有等待者
        with self._cond:
            for entry in self._pending.values():
                entry[0] = {"ok": False, "error": "sharp 子进程已退出"}
            self._pending = {}
            self._cond.notify_all()

    def _call(self, method, params):
        self._ensure()
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        box = [None]
        with self._cond:
            self._pending[req_id] = box
        try:
            payload = json.dumps({"id": req_id, "method": method,
                                  "params": params}, ensure_ascii=False) + "\n"
            with self._lock:
                self._proc.stdin.write(payload.encode("utf-8"))
                self._proc.stdin.flush()
            deadline = time.time() + _REQUEST_TIMEOUT
            with self._cond:
                while box[0] is None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self._pending.pop(req_id, None)
                        raise RuntimeError("sharp 渲染超时")
                    self._cond.wait(remaining)
            msg = box[0]
            if not msg.get("ok"):
                raise RuntimeError(msg.get("error") or "sharp 渲染失败")
            return msg.get("result")
        finally:
            with self._cond:
                self._pending.pop(req_id, None)

    # ── 对外接口 ──

    def render_svg(self, input_path, output_path, width=None, height=None,
                   scale=None):
        """SVG → PNG（sharp 渲染）。返回输出路径；失败抛异常。"""
        params = {"input": os.path.abspath(input_path),
                  "output": os.path.abspath(output_path)}
        if width:
            params["width"] = int(width)
        if height:
            params["height"] = int(height)
        if scale and scale != 1:
            params["scale"] = float(scale)
        self._call("svg2png", params)
        return output_path

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @property
    def started(self):
        return self._proc is not None and self._proc.poll() is None


# 模块级单例
_client = None


def get_client():
    global _client
    if _client is None:
        _client = SharpClient()
    return _client


def render_svg(input_path, output_path, width=None, height=None, scale=None):
    """便捷入口：SVG → PNG（懒启动 sharp 子进程）。"""
    return get_client().render_svg(input_path, output_path,
                                   width=width, height=height, scale=scale)


def close_sharp():
    if _client is not None:
        _client.close()
