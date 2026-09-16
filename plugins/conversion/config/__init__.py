"""
conversion/config/
───────────────────────────────────────────────
转换插件配置层（纯 Python dataclass，无 PySide / QSettings），
1:1 迁移自 OCTools/config/ 的相关子集。
"""

from config.enums import (  # noqa: F401
    PaperSize,
    Orientation,
    LineSpacingMode,
    Alignment,
)
from config.page_layout import PageLayout  # noqa: F401
from config.heading import HeadingStyle  # noqa: F401
from config.typography import Typography  # noqa: F401
from config.content import ContentStyles  # noqa: F401
from config.advanced import AdvancedFeatures  # noqa: F401
from config.format_config import FormatConfig, default_config as _fmt  # noqa: F401
from config.image_docx_config import ImageDocxConfig  # noqa: F401
from config.pdf_docx_config import PdfDocxConfig, MODE_LABELS  # noqa: F401
from config.tts_config import TtsConfig, ENGINE_LABELS, ENGINE_ORDER  # noqa: F401
from config.stt_config import (  # noqa: F401
    SttConfig,
    STT_LANGUAGE_LABELS,
    STT_DEVICE_LABELS,
)

__all__ = [
    "PaperSize", "Orientation", "LineSpacingMode", "Alignment",
    "PageLayout", "HeadingStyle", "Typography", "ContentStyles",
    "AdvancedFeatures", "FormatConfig", "ImageDocxConfig", "PdfDocxConfig",
    "MODE_LABELS", "TtsConfig", "ENGINE_LABELS", "ENGINE_ORDER",
    "SttConfig", "STT_LANGUAGE_LABELS", "STT_DEVICE_LABELS",
]