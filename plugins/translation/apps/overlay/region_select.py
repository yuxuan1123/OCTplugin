"""
translation/apps/overlay/region_select.py
──────────────────────────────────────────
原生区域框选（子进程，纯 stdlib）。

以独立进程运行：全屏半透明覆盖层 + 十字准星，鼠标拖拽画框。
结束后向 stdout 输出一行 JSON（唯一输出）：
  {"type":"selected","x":..,"y":..,"w":..,"h":..}
  {"type":"cancelled"}        （Esc / 右键 / 未画框即松手）

用法：python region_select.py [--border #RRGGBB]
Tkinter 不可用时输出 {"type":"fatal","msg":...} 并退出（明确报错）。
"""

import json
import sys

# Windows 下强制 UTF-8 stdio：父进程以 UTF-8 写入/读取本子进程管道，
# 若不强制，默认走 GBK 会导致中文（OCR 结果/fatal 提示）乱码。
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


def main():
    border = "#a8875a"
    args = sys.argv[1:]
    if args and args[0] == "--border" and len(args) > 1:
        border = args[1]

    try:
        import tkinter as tk
    except Exception as e:
        _out({"type": "fatal", "msg": f"区域框选需要 tkinter: {e}"})
        return

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.35)
    except tk.TclError:
        pass
    # 全屏覆盖（跨屏用 geometry 覆盖主屏；Windows 可用 user32 取虚拟屏，此处取主屏）
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    canvas = tk.Canvas(root, cursor="crosshair", highlightthickness=0,
                       bg="#000000")
    canvas.pack(fill="both", expand=True)

    state = {"x0": None, "y0": None, "box": None}

    def on_press(ev):
        state["x0"] = ev.x
        state["y0"] = ev.y
        if state["box"] is not None:
            canvas.delete(state["box"])
        state["box"] = canvas.create_rectangle(
            ev.x, ev.y, ev.x, ev.y, outline=border, width=2)

    def on_drag(ev):
        if state["x0"] is None:
            return
        canvas.coords(state["box"], state["x0"], state["y0"], ev.x, ev.y)

    def on_release(ev):
        if state["x0"] is None:
            return
        x0, y0 = state["x0"], state["y0"]
        x1, y1 = ev.x, ev.y
        state["x0"] = None
        x, y = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 4 or h < 4:
            _out({"type": "cancelled"})
        else:
            _out({"type": "selected", "x": x, "y": y, "w": w, "h": h})
        root.destroy()

    def on_cancel(_ev=None):
        _out({"type": "cancelled"})
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_cancel)
    root.bind("<Button-3>", on_cancel)

    root.mainloop()


if __name__ == "__main__":
    main()
