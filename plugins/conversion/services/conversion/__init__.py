"""
conversion/services/conversion/
───────────────────────────────────────────────
格式转换业务层：星型 + 直接转换调度，1:1 迁移自 OCTools/services/conversion/。

公共 API（本包聚合导出，供 adapter / UI / 测试直接使用）。
"""

from services.conversion.registry import (  # noqa: F401
    REGISTRY,
    Registry,
    ConversionSpec,
    build_registry,
)
from services.conversion.pipeline import convert  # noqa: F401
from services.conversion.planner import (  # noqa: F401
    is_reachable,
    path_of,
    reachable_from,
    can_batch,
    can_concat,
    reachable_targets,
)
from services.conversion.direct_table import (  # noqa: F401
    CONVERSION_TABLE,
    FORMAT_ALIASES,
    TXT_OCR_SRC_EXTS,
    TXT_OCR_SOURCES,
    AUDIO_TARGET_EXTS,
    STT_SOURCE_EXTS,
    _IMAGE_TO_DOCX_EXTS,
    _infer_source_format,
)
from services.conversion.star.hubs import HUBS, hub_of, hub_star_edges  # noqa: F401
from services.conversion.star.router import StarRouter, router as STAR  # noqa: F401
from services.conversion.star.runner import run_path  # noqa: F401
from services.conversion.converter_factory import ConverterFactory, factory  # noqa: F401
from services.conversion.cross_category import (  # noqa: F401
    is_cross_category,
    cross_path,
    cross_reachable,
    cross_targets_from,
)

from core.engines.ffmpeg_utils import (  # noqa: F401
    VIDEO_FORMATS,
    AUDIO_FORMATS,
    IMAGE_FORMATS,
    MEDIA_EXTENSIONS,
    SOURCE_ONLY_FORMATS as MEDIA_SOURCE_ONLY_FORMATS,
)
from core.engines.media_engine import (  # noqa: F401
    MEDIA_TARGETS,
    supported_targets,
    is_media_format,
    convert as media_convert,
)

__all__ = [
    "REGISTRY", "Registry", "ConversionSpec", "build_registry",
    "convert", "is_reachable", "path_of", "reachable_from",
    "can_batch", "can_concat", "reachable_targets",
    "CONVERSION_TABLE", "FORMAT_ALIASES", "TXT_OCR_SRC_EXTS", "TXT_OCR_SOURCES",
    "AUDIO_TARGET_EXTS", "STT_SOURCE_EXTS", "_IMAGE_TO_DOCX_EXTS",
    "_infer_source_format",
    "HUBS", "hub_of", "hub_star_edges", "StarRouter", "STAR", "run_path",
    "ConverterFactory", "factory", "is_cross_category", "cross_path",
    "cross_reachable", "cross_targets_from",
    "VIDEO_FORMATS", "AUDIO_FORMATS", "IMAGE_FORMATS", "MEDIA_EXTENSIONS",
    "MEDIA_SOURCE_ONLY_FORMATS", "MEDIA_TARGETS", "supported_targets",
    "is_media_format", "media_convert",
]