"""
translation/apps/overlay/region_box.py
───────────────────────────────────────
实时识别区域调整框（子进程，纯 stdlib）。

独立进程：只显示"识别框"本体，内部透明（不遮挡被识别内容）：
  - 整体采用与悬浮窗一致的 -alpha 半透明机制（Tk 跨平台标准参数）
  - 内部不填充任何色块（canvas 底色极浅 + 低透明度 → 近乎透明）
  - 白色外发光 + 暗金双线边框 + 四角暗金括号 + 四边中点小握柄（静态，无动画）
  - 交互：框内任意处拖动移动，边缘/四角拖拽缩放

  ← stdin   {"type":"show","x","y","w","h"} / {"type":"style","border":"#.."}
            {"type":"hide"} / {"type":"close"}
  → stdout  {"type":"changed","x","y","w","h"}  （每次位置/尺寸变化）
            {"type":"closed"}                   （Esc 关闭 或收到 close）

Tkinter 不可用时输出 {"type":"fatal","msg":...} 并退出。
"""

import json
import queue
import sys
import threading

# Windows 下强制 UTF-8 stdio：父进程以 UTF-8 写入/读取本子进程管道。
if sys.platform == "win32":
    for _s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    del _s


def _out(obj):
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class RegionBox:
    BORDER = "#a8875a"        # 暗金主边框
    BORDER_LIGHT = "#c9a97c"  # 内层浅金细边
    ACCENT = "#d9b988"        # 括号/握柄亮金（避免暗金在低透明度下显黑）
    GLOW = "#ffffff"          # 白色外发光（深浅背景都醒目）
    FILL = "#ffffff"          # canvas 底色（纯白；低透明度下内部近似透明）
    ALPHA = 0.42              # 整体透明度（与悬浮窗同机制，跨平台通用）
    HANDLE = 12               # 四角命中区
    EDGE = 6                  # 边缘缩放热区宽度
    MIN_SIZE = 24

    def __init__(self):
        self._q = queue.Queue()
        self._root = None
        self._canvas = None
        self._geom = (0, 0, 320, 170)
        self._drag = None

    # ── stdin 读线程 ──
    def _read_loop(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "close":
                self._q.put(msg)
                break
            self._q.put(msg)

    def setup(self):
        import tkinter as tk
        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self.ALPHA)
        except tk.TclError:
            pass
        canvas = tk.Canvas(root, bg=self.FILL, highlightthickness=0, cursor="fleur")
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas
        self._apply_geom()
        self._root.after(40, self._pump)
        root.deiconify()

    def _apply_geom(self):
        x, y, w, h = self._geom
        self._root.geometry(f"{w}x{h}+{x}+{y}")
        c = self._canvas
        c.delete("all")

        # 白色外发光（静态）
        c.create_rectangle(0, 0, w - 1, h - 1, outline=self.GLOW, width=6)
        # 暗金主边框（只描边，不填充内部）
        c.create_rectangle(3, 3, w - 4, h - 4, outline=self.BORDER, width=4)
        # 内层浅金细边
        c.create_rectangle(7, 7, w - 8, h - 8, outline=self.BORDER_LIGHT, width=1)

        # 四角括号（暗金，实心小方块拼 L 形，避免圆头线条的渲染黑角问题）
        L = min(20, w // 4, h // 4)
        TH = 7
        corners = (
            (4, 4, 1, 1),                    # 左上
            (w - 5, 4, -1, 1),               # 右上
            (4, h - 5, 1, -1),               # 左下
            (w - 5, h - 5, -1, -1),          # 右下
        )
        for cx, cy, dx, dy in corners:
            ex = cx + dx * L
            ey = cy + dy * L
            # 横臂 + 竖臂，亮金实心方块 + 白色细描边（醒目且不显黑）
            for (ax1, ay1, ax2, ay2) in (
                (min(cx, ex), min(cy, cy + TH - 1), max(cx, ex), max(cy, cy + TH - 1)),
                (min(cx, cx + TH - 1), min(cy, ey), max(cx, cx + TH - 1), max(cy, ey)),
            ):
                c.create_rectangle(ax1, ay1, ax2, ay2,
                                   fill=self.ACCENT, outline=self.GLOW, width=1)

        # 四边中点小握柄（缩放提示）
        mw, mh = w / 2.0, h / 2.0
        for x1, y1, x2, y2 in (
            (mw - 3, 1, mw + 3, 8),
            (mw - 3, h - 8, mw + 3, h - 1),
            (1, mh - 3, 8, mh + 3),
            (w - 8, mh - 3, w - 1, mh + 3),
        ):
            c.create_rectangle(x1, y1, x2, y2, fill=self.ACCENT, outline=self.GLOW, width=1)

    def _pump(self):
        try:
            while True:
                msg = self._q.get_nowait()
                self.apply(msg)
        except queue.Empty:
            pass
        if self._root is not None:
            self._root.after(40, self._pump)

    # ── stdin 指令 ──
    def apply(self, msg):
        t = msg.get("type")
        if t == "show":
            self._geom = (msg.get("x", 0), msg.get("y", 0),
                          msg.get("w", 320) or 320, msg.get("h", 170) or 170)
            self._apply_geom()
            # hide() 用 withdraw 隐藏后，restore() 再次 show 必须 deiconify，
            # 否则截图隐藏后识别框一去不复返（一次性应用在截一张图后即不可见）。
            if self._root is not None:
                self._root.deiconify()
                self._root.lift()
        elif t == "style":
            border = msg.get("border")
            if border:
                self.BORDER = border
                self._apply_geom()
        elif t == "hide":
            self._root.withdraw()
        elif t == "close":
            self._root.destroy()
            self._root = None

    # ── 命中检测：四角 / 边缘缩放；内部拖动移动 ──
    def _hit(self, x, y):
        w, h = self._geom[2], self._geom[3]
        hs = self.HANDLE
        left, right = x <= hs + self.EDGE, x >= w - hs - self.EDGE
        top, bottom = y <= hs + self.EDGE, y >= h - hs - self.EDGE
        if left and top:
            return "nw"
        if right and top:
            return "ne"
        if left and bottom:
            return "sw"
        if right and bottom:
            return "se"
        if top:
            return "n"
        if bottom:
            return "s"
        if left:
            return "w"
        if right:
            return "e"
        return "move"

    def on_press(self, ev):
        mode = self._hit(ev.x, ev.y)
        x, y, w, h = self._geom
        self._drag = {
            "mode": mode,
            "rx": ev.x_root, "ry": ev.y_root,
            "gx": x, "gy": y, "gw": w, "gh": h,
        }
        self._canvas.configure(cursor={
            "nw": "sizing", "ne": "sizing", "sw": "sizing", "se": "sizing",
            "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
            "move": "fleur"}[mode])

    def on_drag(self, ev):
        if self._drag is None:
            return
        d = self._drag
        mode = d["mode"]
        dx = ev.x_root - d["rx"]
        dy = ev.y_root - d["ry"]
        x, y, w, h = d["gx"], d["gy"], d["gw"], d["gh"]
        if mode == "move":
            x, y = x + dx, y + dy
        elif mode == "se":
            w, h = w + dx, h + dy
        elif mode == "sw":
            w = w - dx
            x = x + dx
            h = h + dy
        elif mode == "ne":
            w = w + dx
            h = h - dy
            y = y + dy
        elif mode == "nw":
            w = w - dx
            x = x + dx
            h = h - dy
            y = y + dy
        else:  # 边缘：按主拖拽方向缩放
            if abs(dx) >= abs(dy):
                w = w + dx
            else:
                h = h + dy
        w = max(self.MIN_SIZE, w)
        h = max(self.MIN_SIZE, h)
        self._geom = (x, y, w, h)
        self._apply_geom()
        _out({"type": "changed", "x": x, "y": y, "w": w, "h": h})

    def on_release(self, _ev):
        self._drag = None
        self._canvas.configure(cursor="fleur")

    def on_esc(self, _ev=None):
        _out({"type": "closed"})
        self._root.destroy()
        self._root = None

    def run(self):
        import tkinter as tk
        try:
            self.setup()
        except Exception as e:
            _out({"type": "fatal", "msg": f"区域调整框初始化失败: {e}"})
            return
        c = self._canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        self._root.bind("<Escape>", self.on_esc)
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._root.mainloop()


def main():
    try:
        RegionBox().run()
    except Exception as e:
        _out({"type": "fatal", "msg": str(e)})


if __name__ == "__main__":
    main()