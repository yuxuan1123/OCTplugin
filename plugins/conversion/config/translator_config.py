"""
conversion/config/translator_config.py
───────────────────────────────────────────────
机器翻译配置（最小占位实现）。

转换插件本不依赖翻译；此模块仅用于满足 translation_engine 的顶层
import（`from config.translator_config import ENGINE_LABELS, OPUS_MT_BASE,
TranslatorConfig, default_config`）。翻译与语音/OCR 同属「可选高级能力」，
模型缺失时引擎层会优雅降级。
"""

from dataclasses import dataclass

ENGINE_LABELS = {
    "opus": "Opus-MT（本地）",
    "hy": "华译（在线）",
    "llm": "本地大模型",
}
ENGINE_ORDER = ["opus", "hy", "llm"]

OPUS_MT_BASE = ""


@dataclass
class TranslatorConfig:
    """机器翻译配置（占位）"""
    engine: str = "opus"
    src_lang: str = "auto"
    tgt_lang: str = "zh"
    model_dir: str = ""

    def validate(self):
        return []

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        cfg = cls()
        for k, v in (d or {}).items():
            if hasattr(cfg, k):
                try:
                    setattr(cfg, k, v)
                except Exception:
                    pass
        return cfg


def default_config() -> TranslatorConfig:
    return TranslatorConfig()