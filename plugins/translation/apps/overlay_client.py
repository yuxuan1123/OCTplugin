"""
translation/apps/overlay_client.py
──────────────────────────────────
原生悬浮窗子进程客户端（编排层 ←→ overlay/ 子进程的通信封装）。

  FloatOverlayClient   置顶半透明文本悬浮窗（float_overlay.py）
  RegionBoxClient      实时识别区域调整框（region_box.py）
  select_region()      全屏拖拽框选（region_select.py），返回 bbox 或 None
  copy_to_clipboard()  跨平台剪贴板（win: clip / mac: pbcopy / linux: xclip/xsel）

子进程 stdin/stdout 均为 JSON 行（UTF-8 二进制管道），退出即关。
"""

import json
import os
import subprocess
import sys
import threading
import time

from apps.worker_bridge import Signal

_OVERLAY_DIR = os.path.dirname(os.path.abspath(__file__))
_FLOAT_PY = os.path.join(_OVERLAY_DIR, "overlay", "float_overlay.py")
_REGION_SELECT_PY = os.path.join(_OVERLAY_DIR, "overlay", "region_select.py")
_REGION_BOX_PY = os.path.join(_OVERLAY_DIR, "overlay", "region_box.py")

_WRITE_LOCK = threading.Lock()


def _spawn(script, *args):
    """spawn 子进程（UTF-8 二进制管道），返回 Popen"""
    cmd = [sys.executable, script, *args]
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0)


class _SubprocessClient:
    """子进程客户端基类：stdout 读线程 → 信号分发。

    未启动时发出的指令进入 pending 缓冲，start() 后自动补发，
    保证"先配置后启动"与"先启动后配置"两种时序都正确。
    """

    def __init__(self):
        self._proc = None
        self._reader = None
        self._closed = False
        self._pending = []
        self.fatal = Signal()      # (msg)

    def _send_msg(self, msg):
        """写一条 JSON 行（线程安全）；未启动则缓冲"""
        if self._proc is not None and self._proc.stdin is not None:
            try:
                with _WRITE_LOCK:
                    self._proc.stdin.write(
                        json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
                    self._proc.stdin.flush()
                return True
            except Exception:
                return False
        if not self._closed:
            self._pending.append(msg)
        return True

    def _flush_pending(self):
        for msg in self._pending:
            self._send_msg(msg)
        self._pending = []

    # ── 读线程 ──

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
                self._on_msg(msg)
        except Exception:
            pass

    def _on_msg(self, msg):
        raise NotImplementedError


class FloatOverlayClient(_SubprocessClient):
    """置顶半透明文本悬浮窗客户端。

    信号（与 OCTools FloatingOverlay 同义）：
      closed        悬浮窗被关闭（用户点✕ / close 指令）
      mode_changed(both)
      retry_clicked / pause_toggled(paused) / manual_clicked
      copy_clicked / pin_toggled(pinned)
    """

    def __init__(self):
        super().__init__()
        self.closed = Signal()
        self.mode_changed = Signal()
        self.retry_clicked = Signal()
        self.pause_toggled = Signal()
        self.manual_clicked = Signal()
        self.copy_clicked = Signal()
        self.pin_toggled = Signal()

    def start(self):
        self._proc = _spawn(_FLOAT_PY)
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._flush_pending()

    def _on_msg(self, msg):
        t = msg.get("type")
        if t == "closed":
            self.closed.emit()
        elif t == "mode":
            self.mode_changed.emit(bool(msg.get("both", True)))
        elif t == "retry":
            self.retry_clicked.emit()
        elif t == "pause":
            self.pause_toggled.emit(bool(msg.get("paused", False)))
        elif t == "manual":
            self.manual_clicked.emit()
        elif t == "copy":
            self.copy_clicked.emit()
        elif t == "pin":
            self.pin_toggled.emit(bool(msg.get("pinned", False)))
        elif t == "fatal":
            self.fatal.emit(msg.get("msg", "悬浮窗进程异常退出"))

    # ── 控制指令 ──

    def show(self, x=200, y=160, w=460, h=240):
        self._send_msg( {"type": "show", "x": int(x), "y": int(y),
                           "w": int(w), "h": int(h)})

    def show_near(self, rect):
        """显示在识别区域附近（屏幕右下侧，避免遮挡）"""
        x, y, w, h = (int(v) for v in rect)
        sw = 1920
        try:
            import tkinter as tk
            r = tk.Tk()
            sw = r.winfo_screenwidth()
            r.destroy()
        except Exception:
            pass
        ow = min(w, 520)
        ox = min(max(8, x + w - 40), max(8, sw - ow - 8))
        oy = y + h + 12
        self.show(ox, oy, ow, max(120, h))

    def hide(self):
        self._send_msg( {"type": "hide"})

    def set_title(self, text):
        self._send_msg( {"type": "title", "text": text or ""})

    def set_style(self, bg=None, font_size=None, alpha=None):
        msg = {"type": "style"}
        if bg:
            msg["bg"] = bg
        if font_size:
            msg["font_size"] = int(font_size)
        if alpha:
            msg["alpha"] = float(alpha)
        self._send_msg( msg)

    def set_buttons(self, buttons):
        self._send_msg( {"type": "buttons", "list": list(buttons)})

    def set_text(self, text):
        self._send_msg( {"type": "set", "orig": "", "trans": text or ""})

    def set_result(self, orig, trans):
        self._send_msg( {"type": "set", "orig": orig or "", "trans": trans or ""})

    def set_status(self, text):
        self._send_msg( {"type": "status", "text": text or ""})

    def set_dual_mode(self, both: bool):
        self._send_msg( {"type": "set_dual", "both": bool(both)})

    def set_pinned(self, pinned: bool):
        self._send_msg({"type": "pin", "pinned": bool(pinned)})

    def restore(self, rect):
        """截图/隐藏后恢复显示（对齐 app_base._capture 的收回逻辑）"""
        if rect is not None:
            self.show_near(rect)
        else:
            self.show()

    def close(self, timeout: float = 2.0):
        if self._closed:
            return
        self._closed = True
        self._send_msg( {"type": "close"})
        try:
            self._proc.wait(timeout=timeout)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None


class RegionBoxClient(_SubprocessClient):
    """实时识别区域调整框客户端。

    信号：changed(x, y, w, h) / closed
    """

    def __init__(self):
        super().__init__()
        self.changed = Signal()
        self.closed = Signal()

    def start(self, rect=None):
        self._proc = _spawn(_REGION_BOX_PY)
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._flush_pending()
        if rect is not None:
            self.show(rect)

    def _on_msg(self, msg):
        t = msg.get("type")
        if t == "changed":
            self.changed.emit(int(msg["x"]), int(msg["y"]),
                              int(msg["w"]), int(msg["h"]))
        elif t == "closed":
            self.closed.emit()
        elif t == "fatal":
            self.fatal.emit(msg.get("msg", "区域调整框进程异常退出"))

    def show(self, rect):
        x, y, w, h = (int(v) for v in rect)
        self._send_msg( {"type": "show", "x": x, "y": y, "w": w, "h": h})

    def set_style(self, border=None):
        msg = {"type": "style"}
        if border:
            msg["border"] = border
        self._send_msg( msg)

    def hide(self):
        self._send_msg({"type": "hide"})

    def restore(self, rect):
        """恢复显示（区域调整框保持其几何位置）"""
        if rect is not None:
            self.show(rect)

    def close(self, timeout: float = 2.0):
        if self._closed:
            return
        self._closed = True
        self._send_msg({"type": "close"})
        try:
            self._proc.wait(timeout=timeout)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None


def select_region(border="#a8875a", timeout=120.0):
    """全屏拖拽框选（阻塞）。成功返回 (x, y, w, h)，取消返回 None。

    子进程可能被用户卡在框选界面，timeout 兜底防挂死。
    """
    proc = _spawn(_REGION_SELECT_PY, "--border", border)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "selected":
                return (int(msg["x"]), int(msg["y"]),
                        int(msg["w"]), int(msg["h"]))
            if msg.get("type") == "cancelled":
                return None
            if msg.get("type") == "fatal":
                return None
        return None
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def copy_to_clipboard(text):
    """跨平台复制到剪贴板。成功返回 True；失败返回 False。"""
    text = text or ""
    try:
        if sys.platform == "win32":
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-16le"), timeout=5)
            return True
        if sys.platform == "darwin":
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=5)
            return True
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"), timeout=5)
                return True
            except Exception:
                continue
        return False
    except Exception:
        return False
