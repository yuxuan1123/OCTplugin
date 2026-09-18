"""
translation/config/screen_region_config.py
───────────────────────────────────────────────
截图框（OCR 识别区域）配置。1:1 复刻 OCTools。
"""

import os
import json
from dataclasses import dataclass

# 默认边框颜色
DEFAULT_BORDER_COLOR = "#3B82F6"


@dataclass
class ScreenRegionConfig:
    """截图框（OCR 识别区域）配置"""
    fixed: bool = False
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    border_color: str = DEFAULT_BORDER_COLOR

    def set_rect(self, x, y, w, h):
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    def has_rect(self) -> bool:
        return self.w > 0 and self.h > 0

    def rect_tuple(self):
        return (self.x, self.y, self.w, self.h) if self.has_rect() else None

    def summary(self) -> str:
        if not self.has_rect():
            return "未设置区域"
        state = "固定" if self.fixed else "未固定"
        return f"{state} · ({self.x}, {self.y}) {self.w}×{self.h}"

    def validate(self):
        errors = []
        if any(v < 0 for v in (self.x, self.y, self.w, self.h)):
            errors.append("区域坐标/尺寸不能为负数")
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


def default_config() -> ScreenRegionConfig:
    return ScreenRegionConfig()