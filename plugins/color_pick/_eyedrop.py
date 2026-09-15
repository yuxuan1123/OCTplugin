# -*- coding: utf-8 -*-
"""_eyedrop.py —— color_pick 的原生全屏实时吸管窗口。

由 main.py(colorpick.eyedrop) 派生的子进程。开一个小型置顶浮窗作为“偏移放大镜”，
它不盖在光标上，因此 GetPixel / GetDIBits 透过桌面 DC 读到的是【光标下真实屏幕像素】
（真·实时、全屏任意位置）。单击取色，Esc 取消。结果以单行 JSON 写 stdout。

用法：
  python _eyedrop.py            # 进入吸管
  python _eyedrop.py --once     # 无窗口冒烟：抓一次屏幕中心像素即退
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time
import zlib

# 实时颜色共享文件：前端吸管期间轮询它更新“当前颜色”。
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "live.json")
_last = None  # 避免每次 poll 重复刷盘


def write_live(r, g, b):
    global _last
    if _last == (r, g, b):
        return
    _last = (r, g, b)
    try:
        os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
        with open(_CACHE, "w") as f:
            f.write(json.dumps({"ok": True, "color": [r, g, b]}))
    except Exception:
        pass  # 写失败不影响吸管本身

# ---- Win32 ----
_SRCCOPY = 0x00CC0020
_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02
_VK_ESCAPE = 0x1B
ZOOM = 6        # 放大倍数
MAG = 40        # 采样区域边长（中心为光标）
OFF = 26        # 浮窗相对光标偏移，避免挡住采样点

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class _Point(wt.POINT):
    pass


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


def cursor():
    p = _Point()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def pixel(x, y):
    hdc = user32.GetDC(0)
    c = gdi32.GetPixel(hdc, x, y)
    user32.ReleaseDC(0, hdc)
    return (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF)


# 剪贴板相关 API 必须显式声明指针类型（64 位下默认 c_int 会截断句柄导致复制失败）
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

_CF_UNICODETEXT = 13


def copy_hex(text):
    """把 #RRGGBB 写入系统剪贴板（CF_UNICODETEXT）。进程退出后仍保留。"""
    user32.OpenClipboard(0)
    try:
        wcs = (text + "\x00").encode("utf-16le")
        h = kernel32.GlobalAlloc(0x0042, len(wcs))   # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, wcs, len(wcs))
        kernel32.GlobalUnlock(h)                     # 先解锁再交给剪贴板
        user32.EmptyClipboard()
        user32.SetClipboardData(_CF_UNICODETEXT, h)  # 所有权移交剪贴板，不可 GlobalFree
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


def grab_region(x, y, w, h):
    """抓桌面 DC 上 (x,y,w,h) 区域，返回 top-down BGRA bytes。越界返回 None。"""
    vw = user32.GetSystemMetrics(0)
    vh = user32.GetSystemMetrics(1)
    if x < 0 or y < 0 or x + w > vw or y + h > vh:
        return None
    hdc = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, _SRCCOPY)
    bim = _BMIH()
    bim.biSize = ctypes.sizeof(_BMIH)
    bim.biWidth = w
    bim.biHeight = -h
    bim.biPlanes = 1
    bim.biBitCount = 32
    bim.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bim), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, hdc)
    return buf.raw


def bgra_to_png_b64(bgra, w, h):
    """BGRA(top-down) -> PNG(RGB) base64，供 Tk PhotoImage(-data) 解码。"""
    rgb = bytearray(w * h * 3)
    i = 0
    for p in range(0, len(bgra), 4):
        b = bgra[p]; g = bgra[p + 1]; r = bgra[p + 2]
        rgb[i] = r; rgb[i + 1] = g; rgb[i + 2] = b
        i += 3

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) \
        + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def run_once():
    vw = user32.GetSystemMetrics(0)
    vh = user32.GetSystemMetrics(1)
    r, g, b = pixel(vw // 2, vh // 2)
    raw = grab_region(vw // 2 - 20, vh // 2 - 20, 40, 40)
    size = len(raw) if raw else -1
    print(json.dumps({"ok": True, "color": [r, g, b],
                      "note": "px #%02X%02X%02X reg=%d" % (r, g, b, size)}))
    sys.stdout.flush()


def run_gui():
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#1e1b17")
    root.geometry("1x1+0+0")
    root.lift()

    sw = tk.Canvas(root, width=36, height=22, highlightthickness=0)
    hexlab = tk.Label(root, text="#000000", bg="#1e1b17", fg="#F3ECD3",
                      font=("Consolas", 13, "bold"))
    mag = tk.Label(root, bg="#1e1b17", highlightthickness=1,
                   highlightbackground="#a8875a", bd=0)
    sw.pack(side="left", padx=6, pady=6)
    hexlab.pack(side="left", padx=(0, 8))
    mag.pack(side="top")

    half = MAG // 2
    state = {"prev_r_down": False, "prev_l_down": False, "l_last": 0.0,
             "done": False, "flash": 0.0}

    def poll():
        if state["done"]:
            return
        if key_down(_VK_ESCAPE):                 # Esc → 退出
            state["done"] = True
            print(json.dumps({"ok": False, "cancelled": True}))
            sys.stdout.flush()
            root.destroy()
            return
        # 左键双击 → 取色并退出；单击无作用
        l_down = key_down(_VK_LBUTTON)
        if l_down and not state["prev_l_down"]:
            if time.time() - state["l_last"] < 0.4:
                cx, cy = cursor()
                r, g, b = pixel(cx, cy)
                write_live(r, g, b)
                state["done"] = True
                print(json.dumps({"ok": True, "color": [r, g, b]}))
                sys.stdout.flush()
                root.destroy()
                return
            else:
                state["l_last"] = time.time()
        state["prev_l_down"] = l_down

        r_down = key_down(_VK_RBUTTON)
        if r_down and not state["prev_r_down"]:  # 右键按下沿 → 复制当前色值
            cx, cy = cursor()
            r, g, b = pixel(cx, cy)
            write_live(r, g, b)                  # 顺带让前端“当前颜色”保持一致
            if copy_hex("#%02X%02X%02X" % (r, g, b)):
                state["flash"] = time.time()
        state["prev_r_down"] = r_down            # 左键与“拖拽中”的右键均无动作

        cx, cy = cursor()
        raw = grab_region(cx - half, cy - half, MAG, MAG)   # 实时放大镜
        if raw:
            try:
                b64 = bgra_to_png_b64(raw, MAG, MAG)
                bmp = tk.PhotoImage(master=root, data=b64, width=MAG, height=MAG).zoom(ZOOM)
                mag.configure(image=bmp)
                mag.image = bmp
            except Exception:
                pass
        r, g, b = pixel(cx, cy)
        write_live(r, g, b)                       # 实时同步“当前颜色”到前端
        sw.configure(bg="#%02X%02X%02X" % (r, g, b))
        if time.time() - state["flash"] < 0.7:    # 右键复制成功反馈
            hexlab.configure(text="✓ 已复制", fg="#a8875a")
        else:
            hexlab.configure(text="#%02X%02X%02X" % (r, g, b), fg="#F3ECD3")
        root.update_idletasks()
        # 跟随光标（右下偏移，贴边翻到另一侧）
        w = mag.winfo_reqwidth()
        hh = mag.winfo_reqheight() + sw.winfo_reqheight() + 4
        vw = user32.GetSystemMetrics(0)
        vh = user32.GetSystemMetrics(1)
        ox, oy = OFF, OFF
        if cx + ox + w > vw:
            ox = -w - OFF
        if cy + oy + hh > vh:
            oy = -hh - OFF
        root.geometry("%dx%d+%d+%d" % (w, hh, cx + ox, cy + oy))
        root.lift()
        root.after(30, poll)

    root.after(10, poll)
    root.mainloop()


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_gui()