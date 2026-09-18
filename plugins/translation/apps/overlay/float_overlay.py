"""
translation/apps/overlay/float_overlay.py
──────────────────────────────────────────
原生悬浮显示框（子进程，纯 stdlib）。对齐 OCTools `ui/ui_component/overlay.py`
（CustomTitleBar + 上下两栏 + WindowResizer）的交互：

  ┌─────────────────────────────────────────────────────────┐
  │ 标题/状态              [双语][再次][暂停][手动][复制][固定][✕] │  ← 顶栏（可拖动）
  ├─────────────────────────────────────────────────────────┤
  │  原文（上栏，双语模式才显示）                               │
  │  译文（下栏 + 底部状态行）                                 │
  └─────────────────────────────────────────────────────────┘

- 顶栏右侧排布按钮（mode/retry/pause/manual/copy/pin/close），
  与 OCTools 一致，按恒定顺序，未启用者自动隐藏；
- 顶栏（按钮区以外）可拖动移动窗口；
- 全窗口四条边 + 四角可缩放（对齐 OCTools WindowResizer，仅按钮不触发）；
- 固定（pin）时锁定位置与大小（禁用拖动/缩放）。

子进程独立运行，由 main.py 编排层 spawn，通过 stdin/stdout 收发 JSON 行：
  ← stdin  控制指令（show / hide / set / status / style / title /
            set_dual / pin / buttons / close）
  → stdout 事件（closed / mode / retry / pause / manual / copy / pin / fatal）

仅 Tkinter 实现（纯 stdlib，跨平台 -topmost / -alpha / overrideredirect）。
无 Tkinter 时输出 fatal 并退出（明确报错，不静默失败）。
"""

import json
import sys
import threading
import queue

# Windows 下强制 UTF-8 stdio：父进程以 UTF-8 写入/读取本子进程管道，
# 若不强制，默认走 GBK 会导致中文（OCR 结果/fatal 提示）乱码。
if sys.platform == "win32":
    for _s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    del _s

try:
    import tkinter as tk
    TK_OK = True
except Exception:
    TK_OK = False

# 恒定按钮顺序（对齐 OCTools _BTN_ORDER）
_BTN_ORDER = ("mode", "retry", "pause", "manual", "copy", "pin", "close")


def _out(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class _BaseOverlay:
    """子进程外壳：stdin 读线程 → 指令队列 → 主循环 apply；stdout 事件"""

    def __init__(self):
        self._q = queue.Queue()
        self._ev_out = queue.Queue()
        self._running = True

    def poll_events(self):
        while True:
            try:
                ev = self._ev_out.get_nowait()
            except queue.Empty:
                return
            _out(ev)

    def emit(self, ev):
        self._ev_out.put(ev)

    def _read_loop(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._q.put(msg)
            if msg.get("type") == "close":
                break

    def run(self):
        threading.Thread(target=self._read_loop, daemon=True).start()
        self.setup()
        while self._running:
            self.poll_events()
            try:
                msg = self._q.get(timeout=0.05)
            except queue.Empty:
                continue
            self.apply(msg)
        self.teardown()

    def setup(self):
        raise NotImplementedError

    def apply(self, msg):
        raise NotImplementedError

    def teardown(self):
        pass


class TkFloatOverlay(_BaseOverlay):
    """Tkinter 悬浮窗：顶栏(按钮) + 上下两栏 + 状态行 + 全缘缩放"""

    FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Sans"
    TOPBAR_H = 32            # 顶栏高度
    RESIZE_M = 8             # 边缘/四角热区宽度
    MIN_W, MIN_H = 240, 120

    # 缩放方向 → Windows 光标
    _ZONE_CURSOR = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "nw": "sizing", "ne": "sizing", "sw": "sizing", "se": "sizing",
    }

    def __init__(self):
        super().__init__()
        self._root = None
        self._title = ""
        self._orig = ""
        self._trans = ""
        self._status = ""
        self._mode = "both"          # both | trans
        self._pinned = False
        self._paused = False
        self._bg = "#ffffff"
        self._fg = "#1e1b17"
        self._accent = "#a8875a"
        self._hint = "#5c564c"
        self._font_size = 14
        self._alpha = 0.95
        self._buttons = []
        self._radix = []             # 已加载的按钮 key（用于渲染）
        self._action = None          # {"kind":"drag"/"resize", ...}
        self._scroll = None          # 内容画布（滚轮滚动长文本）

    # ── setup ──
    def setup(self):
        root = tk.Tk()
        self._root = root
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self._alpha)
        except tk.TclError:
            pass
        self._build()
        # 全局指针反馈：按下定位拖动/缩放，移动切缩放光标，松开复位
        root.bind("<ButtonPress-1>", self._on_press)
        root.bind("<B1-Motion>", self._on_motion)
        root.bind("<ButtonRelease-1>", self._on_release)
        root.bind("<Motion>", self._on_hover)
        root.after(50, self._tk_pump)
        root.deiconify()

    def _tk_pump(self):
        """每 50ms：事件出队 + 非阻塞 apply（单条异常不得中断后续）"""
        self.poll_events()
        try:
            while True:
                msg = self._q.get_nowait()
                try:
                    self.apply(msg)
                except Exception:
                    # 单条指令异常仅忽略该条，继续泵，避免后续消息（含 OCR 文本）丢失
                    import traceback
                    traceback.print_exc()
        except queue.Empty:
            pass
        if self._running:
            self._root.after(50, self._tk_pump)

    def run(self):
        threading.Thread(target=self._read_loop, daemon=True).start()
        self.setup()
        try:
            self._root.mainloop()
        finally:
            self.teardown()

    # ── UI 构建 ──
    def _btn_text(self, key):
        return {
            "mode": "双语", "retry": "再次", "pause": "暂停",
            "manual": "手动", "copy": "复制", "pin": "固定",
            "close": "✕",
        }[key]

    def _make_button(self, key):
        b = tk.Button(self._topbar_right, text=self._btn_text(key),
                      bg=self._bg, fg=self._accent, relief="flat", bd=0,
                      padx=3, pady=0, takefocus=0,
                      activebackground="#f3ecdd", activeforeground=self._accent,
                      cursor="hand2", highlightthickness=0,
                      font=(self.FONT_FAMILY, 8),
                      command=lambda k=key: self._btn_clicked(k))
        b.pack(side="left", padx=0)
        return b

    def _build(self):
        root = self._root
        root.configure(bg=self._bg)

        # 外框容器（accent 细描边，营造悬浮质感）
        outer = tk.Frame(root, bg=self._bg, bd=0, highlightthickness=1,
                         highlightbackground=self._accent)
        outer.pack(fill="both", expand=True)

        # ── 顶栏：标题(左) + 按钮行(右) ──
        self._topbar = tk.Frame(outer, bg=self._bg, bd=0, height=self.TOPBAR_H)
        self._topbar.pack(fill="x")
        self._topbar.pack_propagate(False)

        self._title_lbl = tk.Label(self._topbar, text=self._title, bg=self._bg,
                                   fg=self._accent, font=(self.FONT_FAMILY, 9))
        self._title_lbl.pack(side="left", padx=5, pady=0)

        self._topbar_right = tk.Frame(self._topbar, bg=self._bg, bd=0)
        self._topbar_right.pack(side="right", padx=1)

        # 恒序渲染按钮（未启用的不占位，构造时即固定）
        self._btn_widgets = {}
        for key in _BTN_ORDER:
            if key not in self._buttons:
                continue
            self._btn_widgets[key] = self._make_button(key)

        # ── 内容区：原文(上)/译文(下)，画布承载 + 滚轮滚动长文本 ──
        self._content = tk.Frame(outer, bg=self._bg, bd=0)
        self._content.pack(fill="both", expand=True)

        self._scroll = tk.Canvas(self._content, bg=self._bg, bd=0,
                                 highlightthickness=0, yscrollincrement=3,
                                 width=1, height=1)
        self._scroll.pack(fill="both", expand=True)
        self._inner = tk.Frame(self._scroll, bg=self._bg)
        self._inner_id = self._scroll.create_window(0, 0, window=self._inner, anchor="nw")

        self._orig_lbl = tk.Label(self._inner, text="", bg=self._bg,
                                  fg=self._fg, justify="left", anchor="nw",
                                  wraplength=1, font=(self.FONT_FAMILY, max(self._font_size - 1, 8)))
        self._trans_lbl = tk.Label(self._inner, text="", bg=self._bg,
                                   fg=self._fg, justify="left", anchor="nw",
                                   wraplength=1, font=(self.FONT_FAMILY, self._font_size))

        self._scroll.bind("<Configure>", self._on_canvas_resize)
        self._root.bind_all("<MouseWheel>", self._on_wheel)
        self._refresh_mode()  # 按当前模式排布原文/译文

        # ── 底部状态行 ──
        self._status_lbl = tk.Label(outer, text="", bg=self._bg, fg=self._hint,
                                    anchor="w", font=(self.FONT_FAMILY, 8))
        self._status_lbl.pack(fill="x", padx=6, pady=(0, 1))

        root.geometry("440x240+200+160")

    # ── 内容滚动（滚轮）──
    def _on_canvas_resize(self, e):
        if self._scroll is None:
            return
        self._scroll.itemconfigure(self._inner_id, width=e.width)
        w = max(50, e.width - 10)
        self._orig_lbl.configure(wraplength=w)
        self._trans_lbl.configure(wraplength=w)
        self._sync_scroll()

    def _on_wheel(self, e):
        if self._scroll is None:
            return
        d = getattr(e, "delta", None)
        if d is None:
            d = getattr(e, "num", 5) - 5  # X11 (num 4=上,5=下)
        step = 1 if d > 0 else -1
        self._scroll.yview_scroll(-step * 3, "units")

    def _sync_scroll(self):
        try:
            self._scroll.configure(scrollregion=self._scroll.bbox("all"))
        except tk.TclError:
            pass

    # ── 顶部/边缘缩放热区 ──
    def _resize_zone(self, x, y):
        w = self._root.winfo_width()
        h = self._root.winfo_height()
        m = self.RESIZE_M
        left, right = x < m, x >= w - m
        top, bottom = y < m, y >= h - m
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
        return None

    # ── 统一鼠标处理：按下定位，移动缩放/拖动，松开复位 ──
    def _on_press(self, ev):
        if self._pinned:
            self._action = None
            return
        zone = self._resize_zone(ev.x, ev.y)
        if zone:
            self._action = {"kind": "resize", "zone": zone,
                            "sx": ev.x_root, "sy": ev.y_root,
                            "x": self._root.winfo_x(), "y": self._root.winfo_y(),
                            "w": self._root.winfo_width(), "h": self._root.winfo_height()}
            self._set_cursor(self._ZONE_CURSOR[zone])
        elif ev.y <= self.TOPBAR_H and str(ev.widget.winfo_class()).lower() != "button":
            # 顶栏（非按钮区）拖动
            self._action = {"kind": "drag", "sx": ev.x_root, "sy": ev.y_root,
                            "x": self._root.winfo_x(), "y": self._root.winfo_y()}
            self._set_cursor("fleur")
        else:
            self._action = None
            self._set_cursor("arrow")

    def _on_hover(self, ev):
        if self._pinned or self._action is not None:
            return
        zone = self._resize_zone(ev.x, ev.y)
        self._set_cursor(self._ZONE_CURSOR[zone] if zone else "arrow")

    def _on_motion(self, ev):
        if self._action is not None:
            self._apply_action(ev)

    def _on_release(self, ev):
        self._action = None
        self._set_cursor("arrow")

    def _set_cursor(self, cur):
        try:
            self._root.configure(cursor=cur)
        except tk.TclError:
            pass

    def _apply_action(self, ev):
        a = self._action
        sx, sy = ev.x_root, ev.y_root
        if a["kind"] == "drag":
            self._root.geometry(f"+{a['x'] + (sx - a['sx'])}"
                                f"+{a['y'] + (sy - a['sy'])}")
            return
        x, y, w, h = a["x"], a["y"], a["w"], a["h"]
        dx, dy = sx - a["sx"], sy - a["sy"]
        z = a["zone"]
        if "e" in z:
            w = max(self.MIN_W, a["w"] + dx)
        if "s" in z:
            h = max(self.MIN_H, a["h"] + dy)
        if "w" in z:
            nw = max(self.MIN_W, a["w"] - dx)
            x = a["x"] + (a["w"] - nw)
            w = nw
        if "n" in z:
            nh = max(self.MIN_H, a["h"] - dy)
            y = a["y"] + (a["h"] - nh)
            h = nh
        self._root.geometry(f"{w}x{h}+{x}+{y}")

    # ── 按钮行为 ──
    def _btn_clicked(self, key):
        if key == "mode":
            self._mode = "trans" if self._mode == "both" else "both"
            self._refresh_mode()
            self.emit({"type": "mode", "both": self._mode == "both"})
        elif key == "retry":
            self.emit({"type": "retry"})
        elif key == "pause":
            self._paused = not self._paused
            self._btn_widgets["pause"].configure(
                text="继续" if self._paused else "暂停")
            self.emit({"type": "pause", "paused": self._paused})
        elif key == "manual":
            self.emit({"type": "manual"})
        elif key == "copy":
            self.emit({"type": "copy"})
        elif key == "pin":
            self._pinned = not self._pinned
            self._apply_pin()
            self.emit({"type": "pin", "pinned": self._pinned})
        elif key == "close":
            self._running = False
            self.emit({"type": "closed"})
            try:
                self._root.after(60, self._root.destroy)
            except Exception:
                pass

    def _apply_pin(self):
        if "pin" in self._btn_widgets:
            self._btn_widgets["pin"].configure(
                text="解锁" if self._pinned else "固定",
                fg=self._accent if self._pinned else self._accent,
                bg="#f3ecdd" if self._pinned else self._bg)
        if self._pinned:
            self._action = None

    def _refresh_mode(self):
        """双语 ↔ 仅译文：展开/收起原文上栏；同步按钮文案。

        收起时 pack_forget 原文；展开时须保证 原文在上、译文在下。
        不能对已 pack_forget 的控件用 `before=xxx`（TclError：isn't packed），
        故改为：先卸下双方，再依次 pack 原文→译文，顺序天然正确。
        填充使用 fill="x"（不撑满窗口高度），正文超长时靠滚轮在画布内滚动。
        """
        both = self._mode == "both"
        if both:
            if not self._orig_lbl.winfo_ismapped():
                self._trans_lbl.pack_forget()
                self._orig_lbl.pack(fill="x", padx=6, pady=(3, 0))
                self._trans_lbl.pack(fill="x", padx=6, pady=(1, 2))
        else:
            if self._orig_lbl.winfo_ismapped():
                self._orig_lbl.pack_forget()
        if "mode" in self._btn_widgets:
            self._btn_widgets["mode"].configure(
                text="仅译文" if both else "双语")
        self._sync_scroll()

    # ── 内容刷新 ──
    def _refresh_text(self):
        if self._mode == "trans":
            self._orig_lbl.configure(text="")
        else:
            self._orig_lbl.configure(text=self._orig)
        self._trans_lbl.configure(text=self._trans)
        self._sync_scroll()

    # ── 指令分发 ──
    def apply(self, msg):
        t = msg.get("type")
        if t == "show":
            x = msg.get("x", 200) or 200
            y = msg.get("y", 160) or 160
            w = max(self.MIN_W, msg.get("w", 460) or 460)
            h = max(self.MIN_H, msg.get("h", 260) or 260)
            self._root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            self._root.deiconify()
        elif t == "hide":
            self._root.withdraw()
        elif t == "set":
            self._orig = msg.get("orig", "") or ""
            self._trans = msg.get("trans", "") or ""
            self._refresh_text()
        elif t == "status":
            self._status = msg.get("text", "") or ""
            self._status_lbl.configure(text=self._status)
        elif t == "style":
            bg = msg.get("bg")
            if bg and bg != "transparent":
                self._bg = bg
                self._root.configure(bg=bg)
                for w in self._root.winfo_children():
                    try:
                        w.configure(bg=bg)
                    except tk.TclError:
                        pass
            fs = msg.get("font_size")
            if fs:
                self._font_size = max(8, min(24, int(fs)))
                self._orig_lbl.configure(font=(self.FONT_FAMILY, max(self._font_size - 1, 8)))
                self._trans_lbl.configure(font=(self.FONT_FAMILY, self._font_size))
            alpha = msg.get("alpha")
            if alpha:
                try:
                    self._root.attributes("-alpha", float(alpha))
                except tk.TclError:
                    pass
        elif t == "title":
            self._title = msg.get("text", "") or ""
            self._title_lbl.configure(text=self._title)
        elif t == "set_dual":
            self._mode = "both" if msg.get("both", True) else "trans"
            self._refresh_mode()
            self._refresh_text()
        elif t == "pin":
            self._pinned = bool(msg.get("pinned", False))
            self._apply_pin()
        elif t == "buttons":
            self._buttons = [b for b in _BTN_ORDER if b in (msg.get("list") or [])]
            self._rebuild_buttons()
        elif t == "close":
            self._running = False
            self._root.destroy()

    def _rebuild_buttons(self):
        """buttons 指令：按新子集重建顶栏按钮行"""
        if self._topbar_right is None:
            return
        for w in self._topbar_right.winfo_children():
            w.destroy()
        self._btn_widgets = {}
        for key in _BTN_ORDER:
            if key not in self._buttons:
                continue
            self._btn_widgets[key] = self._make_button(key)
        # 重建后应用当前暂停/固定状态
        if self._paused and "pause" in self._btn_widgets:
            self._btn_widgets["pause"].configure(text="继续")
        if self._pinned:
            self._apply_pin()

    def teardown(self):
        _out({"type": "closed"})


# ════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════


def main():
    if not TK_OK:
        _out({"type": "fatal", "msg": "无可用悬浮窗后端（缺少 tkinter）"})
        return
    try:
        TkFloatOverlay().run()
    except Exception as e:
        _out({"type": "fatal", "msg": f"Tkinter 悬浮窗失败: {e}"})


if __name__ == "__main__":
    main()