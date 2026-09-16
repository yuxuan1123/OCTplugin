"""
conversion/config/heading.py
───────────────────────────────────────────────
标题样式（最小功能单元）：HeadingStyle，1:1 迁移自 OCTools/config/heading.py。
"""

from dataclasses import dataclass

from config.enums import Alignment


@dataclass
class HeadingStyle:
    """单个标题级别的样式"""
    font_cn: str = "黑体"
    font_en: str = "Arial"
    font_size: float = 16.0        # pt
    bold: bool = True
    color: str = "#000000"
    alignment: Alignment = Alignment.LEFT