"""
translation/config/translator_config.py
───────────────────────────────────────────────
翻译配置模型：引擎选择 + 各引擎参数，支持保存 / 加载。1:1 复刻 OCTools。

设计要点：
  - 配置模型统一集中在 config/，引擎逻辑留在 translation_engine.py
  - 模型路径优先读环境变量（OCTTR_MODEL_*），缺省空串 → 引擎层给清晰缺失提示
"""

import os
from dataclasses import dataclass

from config.ui_config import CONFIG as _C, migrate_model_path

# ════════════════════════════════════════════
#  模型路径（来自 config/ui_config.py，可用环境变量覆盖）
# ════════════════════════════════════════════

OPUS_MT_BASE = _C.model_path("opus_base")

# Hy-MT2-1.8B（llama_cpp GGUF）
HY_DEFAULT_MODEL_PATH = _C.model_path("hy_path")

# 翻译引擎（UI 下拉 / 配置共用）
ENGINE_LABELS = {
    "hy": "Hy-MT2-1.8B（本地大模型，推荐）",
    "opusmt": "Opus-MT（轻量）",
}
ENGINE_ORDER = ["hy", "opusmt"]


@dataclass
class TranslatorConfig:
    """翻译配置：引擎选择 + 各引擎参数（未用到的引擎参数忽略）"""
    engine: str = "hy"                      # hy / opusmt

    # ── Hy-MT2-1.8B（llama_cpp，默认）──
    hy_model_path: str = HY_DEFAULT_MODEL_PATH
    hy_n_ctx: int = 2048
    hy_n_threads: int = 4
    hy_n_gpu_layers: int = 0                # 0 = 纯 CPU
    hy_max_tokens: int = 256
    hy_temperature: float = 0.7
    hy_top_p: float = 0.6
    hy_top_k: int = 20
    hy_repeat_penalty: float = 1.05

    # ── Opus-MT（CTranslate2）──
    opusmt_base: str = OPUS_MT_BASE

    # ── 屏幕翻译悬浮窗显示 ──
    overlay_font_size: int = 14          # 译文悬浮窗字号（px）
    overlay_mode: str = "both"           # both=双语（原文+译文） / trans=仅译文
    overlay_bg_color: str = "#FFFFFF"    # 悬浮窗背景色（#RRGGBB 或 transparent）
    overlay_alpha: float = 0.95          # 悬浮窗不透明度（0~1，transparent 背景时生效）
    overlay_movable: bool = True         # 悬浮窗是否可移动
    overlay_resizable: bool = True       # 悬浮窗是否可调整大小
    overlay_pin_default: bool = False    # 启动时默认是否已「固定」

    # ── 语音翻译字幕（与 OCR 字幕区分，独立背景/字号/显示原文）──
    voice_sub_bg_color: str = "#0C121F"  # 语音字幕背景色
    voice_sub_font_size: int = 20        # 语音字幕字号
    voice_sub_show_orig: bool = True     # 语音字幕是否显示原文

    # ── 全局快捷键（系统级，托盘/后台也生效）──
    hotkey_ocr_once: str = "alt+x"       # OCR 单词屏幕翻译（单次）
    hotkey_ocr_live: str = "alt+c"       # OCR 实时屏幕翻译（连续）

    # ── 模型预加载（后台提前加载 OCR + 翻译模型，减少首次使用等待）──
    preload_models: bool = True          # 是否启用后台预加载
    preload_timing: str = "startup"      # startup / tab

    def validate(self):
        errors = []
        if self.engine not in ENGINE_LABELS:
            errors.append(f"未知翻译引擎: {self.engine}")
        if self.engine == "hy" and not os.path.exists(self.hy_model_path):
            errors.append(f"Hy-MT 模型文件不存在: {self.hy_model_path}")
        return errors

    # ── 序列化（与 TtsConfig / SttConfig 保持一致的接口）──
    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        cfg = cls()
        for k, v in (d or {}).items():
            if hasattr(cfg, k):
                try:
                    setattr(cfg, k, v)
                except Exception:
                    pass
        # 历史配置里保存的可能是改名前的模型目录，修正为当前路径
        cfg.hy_model_path = migrate_model_path(
            cfg.hy_model_path, HY_DEFAULT_MODEL_PATH)
        cfg.opusmt_base = migrate_model_path(cfg.opusmt_base, OPUS_MT_BASE)
        return cfg

    def to_json(self, indent=2):
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, s):
        import json
        return cls.from_dict(json.loads(s))

    def save_to_file(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_file(cls, path):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_json(f.read())
        except Exception:
            return None


def default_config() -> TranslatorConfig:
    return TranslatorConfig()