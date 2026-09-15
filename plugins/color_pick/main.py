# -*- coding: utf-8 -*-
"""color_pick —— 取色器后端。

颜色转换在插件前端；本后端提供：
  - ping
  - colorpick.screen  ：纯标准库(ctypes)抓整块屏幕，写 .cache/screen.bmp，返回 {ok,width,height,url}
  - colorpick.eyedrop ：派生 _eyedrop.py 子进程（原生全屏实时吸管窗口），
                        单击取色/Esc 取消，回传 {ok,color:[r,g,b]} 或 {ok,false,cancelled:true}
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _capture_bmp():
    """抓取主屏幕，返回 (width, height, bmp_bytes)。纯 stdlib + ctypes。"""
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    if width <= 0 or height <= 0:
        raise RuntimeError("无法获取屏幕尺寸")

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    gdi32.SelectObject(hdc_mem, hbmp)
    ok = gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, 0x00CC0020)
    if not ok:
        raise RuntimeError("BitBlt 截屏失败")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    bim = BITMAPINFOHEADER()
    bim.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bim.biWidth = width
    bim.biHeight = -height  # top-down
    bim.biPlanes = 1
    bim.biBitCount = 32
    bim.biCompression = 0

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bim), 0)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    off = 14 + bim.biSize
    out = bytearray(b'BM')
    out += (off + width * height * 4).to_bytes(4, "little")
    out += (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
    out += off.to_bytes(4, "little")
    out += ctypes.string_at(ctypes.byref(bim), bim.biSize)
    out += buf.raw
    return width, height, bytes(out)


def _eyedrop(timeout_s=600):
    """派生 _eyedrop.py 子进程，等待其输出最终 JSON 结果。"""
    script = os.path.join(_PLUGIN_DIR, "_eyedrop.py")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run([sys.executable, "-u", script],
                          capture_output=True, timeout=timeout_s,
                          creationflags=flags)
    out = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    if out:
        try:
            return json.loads(out[0])
        except ValueError:
            return {"ok": False, "error": "eyedrop 输出解析失败"}
    return {"ok": False, "error": "eyedrop 无结果"}


def handle(req_id, method, params):
    if method == "ping":
        return {"pong": True}
    if method == "colorpick.screen":
        try:
            w, h, data = _capture_bmp()
            cache = os.path.join(_PLUGIN_DIR, ".cache")
            os.makedirs(cache, exist_ok=True)
            path = os.path.join(cache, "screen.bmp")
            with open(path, "wb") as f:
                f.write(data)
            return {"ok": True, "width": w, "height": h,
                    "url": "/plugin/color_pick/.cache/screen.bmp"}
        except Exception as e:  # noqa
            return {"ok": False, "error": str(e)}
    if method in ("colorpick.eyedrop", "eyedrop"):
        try:
            return _eyedrop()
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "eyedrop 超时"}
        except Exception as e:  # noqa
            return {"ok": False, "error": str(e)}
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