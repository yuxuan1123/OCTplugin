"""
translation/apps/app_base.py
───────────────────────────
最终应用 · 公共基类（挂载 UI＝原生悬浮窗子进程）

对齐 OCTools `apps/app_base.py`（vertical_split_titlebar 骨架模型，基类固定
start/stop 步骤，子类只填差异），把 Qt 信号桥替换为 worker_bridge（线程安全
信号），把 Qt overlay / region_box 组件替换为 `overlay_client`（spawn 独立子
进程的置顶半透明悬浮窗 / 区域调整框）。

子类接口（按需提供 / 覆写，详见各 `apps/*.py`）：
  类属性：
    REQUIRES_REGION  bool   默认 True（语音类设为 False）
    USE_TRANSLATE    bool   默认 True（纯 OCR 应用设为 False）
    BUTTONS          list   悬浮窗按钮子集（恒定顺序排版）
    SHOW_ORIG        bool   是否显示原文行（双语 True / 纯文本 False）
    OVERLAY_TITLE    str    悬浮窗标题文本（默认空 = 不显示）
  方法：
    _make_worker(rect)              工厂：默认 None = 不创建 worker
    _install_overlay_signals(ovr)   返回 {overlay信号属性: 回调} 映射
    _install_extra_widgets(rect)    返回额外子进程客户端列表（如 RegionBoxClient）
    _kick_off()                     worker 启动后的首轮动作
    _initial_status() / _log_started(rect)

start() 固定骨架（§1-§9，对齐 OCTools）：
  §1 防重入 → §2 解析识别区域 → §3 创建悬浮窗（构造/通用信号/模式持久化/钩子）
  §4 接应用专属信号 → §5 创建额外组件 → §6 创建 worker → §7 启动 worker
  §8 立即触发 → §9 显示/定位/初始状态/日志
"""

import threading

from apps.overlay_client import FloatOverlayClient, RegionBoxClient, copy_to_clipboard
from apps.worker_bridge import _WorkerBridge
from core.engines.screenshot_engine import grab_region
from services.recipes.image_translate import recognize, recognize_translate


class TranslateAppBase:
    """翻译类最终应用基类（原生悬浮窗 + 线程 worker + 事件桥）"""

    # ── 子类声明（按需覆写）──
    NAME = ""
    OVERLAY_TITLE = ""
    BUTTONS = []
    SHOW_ORIG = True
    REQUIRES_REGION = True
    USE_TRANSLATE = True

    def __init__(self, host):
        self._host = host            # main.py 编排宿主：log / config / emit
        self._overlay = None
        self._worker = None
        self._region_box = None
        self._extras = []
        self._region = None
        self._busy = False
        self._stopped_cb = None      # 悬浮窗关闭回调（app_controller 置）
        self._bridge = _WorkerBridge()
        self._bridge.text_ready.connect(self._on_text_ready)
        self._bridge.result_ready.connect(self._on_result_ready)
        self._bridge.status.connect(self._on_status)
        self._bridge.log_line.connect(self._log_line_slot)

    # ════════════════════════════════════════════════════════════
    # 公共 API
    # ════════════════════════════════════════════════════════════

    def log(self, msg):
        try:
            self._host.log(msg)
        except Exception:
            print(msg)

    def config(self):
        return getattr(self._host, "translator_config", None)

    def stt_config(self):
        return getattr(self._host, "stt_config", None)

    def screen_region_config(self):
        return getattr(self._host, "screen_region_config", None)

    def is_running(self) -> bool:
        return self._overlay is not None

    def set_stopped_callback(self, cb):
        self._stopped_cb = cb

    @property
    def direction(self) -> str:
        return "en2zh"

    def start(self):
        # §1 防重入
        if self.is_running():
            return
        # §2 解析识别区域（语音类跳过）
        rect = None
        if self.REQUIRES_REGION:
            rect = self._resolve_region(self.NAME)
            if rect is None:
                return
            self._region = rect
        # §3 创建悬浮窗
        self._overlay = self._create_overlay()
        # §4 接应用专属信号
        try:
            hooks = self._install_overlay_signals(self._overlay) or {}
        except Exception as e:
            self.log(f"❌ {self.NAME}._install_overlay_signals 失败: {e}")
            hooks = {}
        for attr, callback in hooks.items():
            sig = getattr(self._overlay, attr, None)
            if sig is None or callback is None:
                continue
            try:
                sig.connect(callback)
            except Exception as e:
                self.log(f"❌ {self.NAME} 接 overlay.{attr} 失败: {e}")
        # §5 创建额外组件（实时翻译类的区域调整框）
        try:
            extras = self._install_extra_widgets(rect) or []
        except Exception as e:
            self.log(f"❌ {self.NAME}._install_extra_widgets 失败: {e}")
            extras = []
        self._extras = list(extras)
        for w in self._extras:
            try:
                w.start(rect)
            except Exception:
                pass
        # §6/§7 创建并启动 worker
        self._worker = self._make_worker(rect)
        if self._worker is not None and hasattr(self._worker, "start"):
            try:
                self._worker.start(rect)
            except Exception as e:
                self.log(f"❌ {self.NAME} worker.start 失败: {e}")
                self._cleanup()
                return
        # §8 立即触发
        try:
            self._kick_off()
        except Exception as e:
            self.log(f"❌ {self.NAME}._kick_off 失败: {e}")
        # §9 显示 + 定位 + 初始状态 + 日志
        self._overlay.start()
        if rect is not None:
            try:
                self._overlay.show_near(rect)
            except Exception:
                self._overlay.show()
        else:
            self._overlay.show()
        self._overlay.set_status(self._initial_status())
        self._log_started(rect)

    def stop(self):
        if not self.is_running():
            if self._worker is not None:
                try:
                    if hasattr(self._worker, "stop"):
                        self._worker.stop()
                except Exception:
                    pass
                self._worker = None
            return
        self._busy = False
        if self._worker is not None:
            try:
                if hasattr(self._worker, "stop"):
                    self._worker.stop()
            except Exception:
                pass
            self._worker = None
        self._region_box = None
        for w in self._extras:
            try:
                w.close()
            except Exception:
                pass
        self._extras = []
        self._close_overlay()

    def toggle(self):
        if self.is_running():
            self.stop()
        else:
            self.start()

    # ════════════════════════════════════════════════════════════
    # 子类钩子（默认安全空操作）
    # ════════════════════════════════════════════════════════════

    def _make_worker(self, rect):
        return None

    def _install_overlay_signals(self, overlay):
        return {}

    def _install_extra_widgets(self, rect):
        return []

    def _kick_off(self):
        return None

    def _initial_status(self) -> str:
        return f"⏳ 启动{self.NAME}…"

    def _log_started(self, rect):
        if rect is not None:
            self.log(f"▶ {self.NAME}已启动（区域 {rect[2]}×{rect[3]}）")
        else:
            self.log(f"▶ {self.NAME}已启动")

    # ════════════════════════════════════════════════════════════
    # 一次性图像类：_run_once / _thread_ocr / _process_ocr_once
    # ════════════════════════════════════════════════════════════

    def _run_once(self, *_args):
        if self._busy:
            if self._overlay is not None:
                self._overlay.set_status("处理中…")
            return
        img = self._capture()
        if img is None:
            if self._overlay is not None:
                self._overlay.set_status("❌ 截图失败")
            return
        self._thread_ocr(img, use_translate=self.USE_TRANSLATE)

    def _thread_ocr(self, img, use_translate: bool):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._process_ocr_once,
                         args=(img, use_translate), daemon=True).start()

    def _process_ocr_once(self, img, use_translate: bool):
        try:
            if not use_translate:
                self._bridge.status.emit("⏳ 正在加载 OCR 模型（首次约 20 秒）…")
                text = recognize(img)
                self._busy = False
                if self.is_running():
                    self._bridge.text_ready.emit(text)
                return
            self._bridge.status.emit("⏳ 正在加载 OCR / 翻译模型（首次约 30 秒）…")
            orig, trans = recognize_translate(img, self.direction, config=self.config())
            self._busy = False
            if self.is_running():
                self._bridge.result_ready.emit(orig, trans)
        except Exception as e:
            self._busy = False
            self._bridge.status.emit(f"❌ {e}")
            self.log(f"❌ {self.NAME}: {e}")

    def _capture(self):
        """截图前隐藏悬浮窗/区域框，返回 PIL Image（复刻 OCTools exclude 语义）"""
        vis = [w for w in [self._overlay, self._region_box, *self._extras]
               if w is not None]
        for w in vis:
            try:
                if hasattr(w, "hide"):
                    w.hide()
            except Exception:
                pass
        try:
            img = grab_region(self._region)
        finally:
            for w in vis:
                try:
                    if hasattr(w, "restore"):
                        w.restore(self._region)
                        continue
                    if hasattr(w, "show_near"):
                        w.show_near(self._region)
                    elif hasattr(w, "show"):
                        w.show()
                except Exception:
                    pass
        return img

    # ════════════════════════════════════════════════════════════
    # 区域选择 / 区域调整框
    # ════════════════════════════════════════════════════════════

    def _resolve_region(self, log_hint: str):
        """固定截图框优先；否则全屏框选并写回配置"""
        region = self.screen_region_config()
        if region is not None and region.fixed and region.has_rect():
            x, y, w, h = region.rect_tuple()
            self.log(f"📌 {log_hint}：使用固定截图框 ({x}, {y}) {w}×{h}")
            return (x, y, w, h)
        from apps.overlay_client import select_region
        border = region.border_color if region is not None else "#a8875a"
        rect = select_region(border=border)
        if rect is None:
            self.log("⏹ 已取消区域选择")
            return None
        if region is not None:
            region.fixed = False
            region.set_rect(*rect)
            try:
                from config import presets
                presets.save_last_screen_region_config(region)
            except Exception:
                pass
        self.log(f"📌 {log_hint}：已选择区域 {rect[2]}×{rect[3]}")
        return tuple(rect)

    def _make_region_box(self, rect):
        """创建实时识别区域调整框客户端（可拖动/缩放，变化经回调更新区域）"""
        from config.screen_region_config import DEFAULT_BORDER_COLOR
        region = self.screen_region_config()
        border = getattr(region, "border_color", DEFAULT_BORDER_COLOR) or DEFAULT_BORDER_COLOR
        box = RegionBoxClient()
        box.set_style(border=border)
        return box

    # ════════════════════════════════════════════════════════════
    # 悬浮窗创建（构造 + 通用信号 + 模式持久化）
    # ════════════════════════════════════════════════════════════

    def _overlay_params(self) -> dict:
        cfg = self.config()
        params = dict(bg_color=None, font_size=14, alpha=0.95)
        if cfg is not None:
            bg = getattr(cfg, "overlay_bg_color", None)
            params["bg_color"] = str(bg) if bg else None
            try:
                params["font_size"] = int(getattr(cfg, "overlay_font_size", 14) or 14)
            except (TypeError, ValueError):
                params["font_size"] = 14
            try:
                params["alpha"] = float(getattr(cfg, "overlay_alpha", 0.95) or 0.95)
            except (TypeError, ValueError):
                params["alpha"] = 0.95
        params["font_size"] = max(8, min(24, params["font_size"]))
        return params

    def _create_overlay(self):
        p = self._overlay_params()
        ovl = FloatOverlayClient()
        ovl.set_buttons(list(self.BUTTONS))
        ovl.set_title(self.OVERLAY_TITLE)
        # 通用信号：关闭→stop；模式切换→持久化
        ovl.closed.connect(self.stop)
        ovl.mode_changed.connect(self._on_mode_changed)
        ovl.copy_clicked.connect(self._on_copy)
        # 样式
        if p["bg_color"] and p["bg_color"] != "transparent":
            ovl.set_style(bg=p["bg_color"], font_size=p["font_size"], alpha=p["alpha"])
        else:
            ovl.set_style(font_size=p["font_size"], alpha=p["alpha"])
        # 应用已保存显示模式
        cfg = self.config()
        if cfg is not None and hasattr(cfg, "overlay_mode"):
            try:
                ovl.set_dual_mode(getattr(cfg, "overlay_mode", "both") != "trans")
            except Exception:
                pass
        return ovl

    def _on_mode_changed(self, both: bool):
        cfg = self.config()
        if cfg is None:
            return
        try:
            cfg.overlay_mode = "both" if both else "trans"
            from config import presets
            presets.save_last_translator_config(cfg)
        except Exception:
            pass
        self.log(f"🖥️ {self.NAME} 显示模式: {'双语（原文+译文）' if both else '仅译文'}")

    def _on_copy(self):
        if self._overlay is None:
            return
        # 复制当前悬浮窗内容（仅译文模式复制译文，双语复制 原文+译文）
        # 后端维护最近一次结果，直接复制原文+译文
        if copy_to_clipboard(self._last_copy_text()):
            self.log(f"📋 {self.NAME} 内容已复制到剪贴板")
        else:
            self.log("❌ 复制到剪贴板失败")

    def _last_copy_text(self) -> str:
        txt = getattr(self, "_last_result", None)
        if not txt:
            return ""
        if isinstance(txt, tuple):
            return "\n\n".join(x for x in txt if x)
        return txt

    # ════════════════════════════════════════════════════════════
    # 信号桥 → 悬浮窗 / 事件推送
    # ════════════════════════════════════════════════════════════

    def _on_text_ready(self, text):
        self._last_result = text
        if self._overlay is not None:
            self._overlay.set_text(text)
            self._overlay.set_status("")

    def _on_result_ready(self, orig, trans):
        self._last_result = (orig, trans)
        if self._overlay is not None:
            self._overlay.set_result(orig, trans)
            self._overlay.set_status("")

    def _on_status(self, text):
        if self._overlay is not None:
            self._overlay.set_status(text)

    def _log_line_slot(self, msg):
        self.log(msg)

    # ════════════════════════════════════════════════════════════
    # 清理
    # ════════════════════════════════════════════════════════════

    def _cleanup(self):
        self._worker = None
        self._extras = []
        self._region = None
        self._overlay = None

    def _close_overlay(self):
        ovl = self._overlay
        self._overlay = None
        if ovl is not None:
            try:
                ovl.close()
            except Exception:
                pass
        if self._stopped_cb:
            try:
                self._stopped_cb(self.NAME)
            except Exception:
                pass