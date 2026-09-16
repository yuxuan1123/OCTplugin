"""
conversion/config/tts_config.py
───────────────────────────────────────────────
TTS 配置模型：引擎选择 + 各引擎参数，支持保存 / 加载。

1:1 迁移自 OCTools/config/tts_config.py（去除 QSettings / ui_config 依赖，纯 Python）。
"""

import os
import json
from dataclasses import dataclass

# ════════════════════════════════════════════
#  引擎常量（UI 下拉 / 配置共用）
# ════════════════════════════════════════════

ENGINE_LABELS = {
    "kokoro": "Kokoro（默认）",
    "edge": "Edge-TTS（在线）",
    "moss": "MOSS-TTS（模仿音频）",
}
ENGINE_ORDER = ["kokoro", "edge", "moss"]


@dataclass
class TtsConfig:
    """TTS 配置：引擎选择 + 各引擎参数（未用到的引擎参数忽略）"""
    engine: str = "kokoro"          # kokoro / edge / moss

    # ── Edge-TTS ──
    edge_voice: str = "zh-CN-XiaoxiaoNeural"
    edge_rate: str = "+0%"
    edge_volume: str = "+0%"
    edge_pitch: str = "+0Hz"

    # ── Kokoro ──
    kokoro_lang: str = "z"          # z=中文
    kokoro_voice: str = "zf_xiaoxiao"
    kokoro_speed: float = 1.0       # 0.5–2.0
    kokoro_split_pattern: str = ""  # 留空 = 使用自定义分句

    # ── MOSS-TTS ──
    moss_model_dir: str = ""        # 空 = MOSS_DEFAULT_MODEL_DIR
    moss_voice: str = ""            # 内置音色（可选）
    moss_reference: str = ""        # 模仿音频（参考音频，声音克隆）
    moss_voice_clone_max_text_tokens: int = 75
    moss_max_new_frames: int = 0    # 0 = 模型默认
    moss_do_sample: bool = True
    moss_seed: int = -1             # -1 = 不固定随机种子

    def validate(self):
        errors = []
        if self.engine not in ENGINE_LABELS:
            errors.append(f"未知语音引擎: {self.engine}")
        if not (0.2 <= self.kokoro_speed <= 3.0):
            errors.append(f"Kokoro 语速应在 0.2-3.0 之间（当前: {self.kokoro_speed}）")
        if self.moss_voice_clone_max_text_tokens < 1:
            errors.append("MOSS 单段最大 token 数应 ≥ 1")
        return errors

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
        return cfg

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, s):
        return cls.from_dict(json.loads(s))

    def save_to_file(self, path):
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


def default_config() -> TtsConfig:
    return TtsConfig()