"""
conversion/config/ocr_config.py
───────────────────────────────────────────────
OCR 文字识别（图片 / PDF → TXT）配置模型，支持保存 / 加载。

与 STT/TTS 配置风格一致。此项目 OCR 引擎固定使用 PP-OCRv6_tiny +
onnxruntime + CPU，故仅暴露「识别语言」这一真实可调参数。
"""

import os
import json
from dataclasses import dataclass

# 识别语言选项（PaddleOCR 支持的语言代码）
OCR_LANGUAGE_LABELS = [
    ("ch", "中文"),
    ("en", "英文"),
    ("japan", "日文"),
    ("korean", "韩文"),
    ("french", "法文"),
    ("german", "德文"),
]


@dataclass
class OcrConfig:
    """OCR 识别配置（图片 → TXT(OCR)）"""
    lang: str = "ch"               # 识别语言：ch / en / japan / korean / ...

    def validate(self):
        errors = []
        if not self.lang:
            errors.append("OCR 识别语言不能为空")
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


def default_config() -> OcrConfig:
    return OcrConfig()