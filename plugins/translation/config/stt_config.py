"""
translation/config/stt_config.py
───────────────────────────────────────────────
语音识别（STT）配置模型，支持保存 / 加载。

模型用 sherpa-onnx 离线推理；模型仍是 funasr/Paraformer 导出的 ONNX
目录（model.onnx + tokens.txt）。字段名沿用 OCTools 的 SttConfig
（model_dir / device / language / use_itn），新增 sherpa 推理参数。
"""

import os
from dataclasses import dataclass

from config.ui_config import CONFIG as _C, migrate_model_path

# ════════════════════════════════════════════
#  模型路径 / 常量
# ════════════════════════════════════════════

# 语音识别模型目录（内含 model.onnx + tokens.txt）
STT_DEFAULT_MODEL_DIR = _C.model_path("sensevoice")

# 识别语言选项
STT_LANGUAGE_LABELS = [
    ("auto", "自动检测（中/英等）"),
    ("zh", "中文"),
    ("en", "英文"),
    ("yue", "粤语"),
    ("ja", "日语"),
    ("ko", "韩语"),
]

STT_DEVICE_LABELS = [
    ("cpu", "CPU（纯离线）"),
    ("cuda", "CUDA（GPU，需显卡驱动）"),
]


@dataclass
class SttConfig:
    """语音识别配置（音频 → TXT）"""
    model_dir: str = STT_DEFAULT_MODEL_DIR
    device: str = "cpu"            # cpu / (cuda 需 sherpa-onnx 带 GPU 支持)
    language: str = "auto"         # auto / zh / en / yue / ja / ko
    use_itn: bool = True           # 数字归一化（保留，兼容接口）
    sherpa_num_threads: int = 2    # sherpa-onnx 推理线程数
    sherpa_provider: str = "cpu"   # cpu / cuda

    def validate(self):
        errors = []
        if not os.path.isdir(self.model_dir):
            errors.append(f"语音识别模型目录不存在: {self.model_dir}（应含 model.onnx + tokens.txt）")
        else:
            enc = os.path.join(self.model_dir, "model.onnx")
            tok = os.path.join(self.model_dir, "tokens.txt")
            enc_int8 = os.path.join(self.model_dir, "model.int8.onnx")
            if not (os.path.exists(enc) or os.path.exists(enc_int8)):
                errors.append(f"模型目录缺少 model.onnx 或 model.int8.onnx: {self.model_dir}")
            if not os.path.exists(tok):
                errors.append(f"模型目录缺少 tokens.txt: {self.model_dir}")
        if self.device not in ("cpu", "cuda"):
            errors.append(f"未知设备: {self.device}")
        return errors

    # ── 序列化（与 TtsConfig 保持一致接口）──
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
        cfg.model_dir = migrate_model_path(cfg.model_dir, STT_DEFAULT_MODEL_DIR)
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


def default_config() -> SttConfig:
    return SttConfig()