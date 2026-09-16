"""
conversion/services/conversion/registry.py
───────────────────────────────────────────────
转换注册表，1:1 迁移自 OCTools/services/conversion/registry.py。
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ConversionSpec:
    """一条直达转换的完整声明"""
    func: Callable
    config_type: Optional[type] = None
    config_kwarg: str = "config"
    output_is_folder: bool = False
    transitive: bool = True


def _norm(fmt: str) -> str:
    return str(fmt or "").strip().lower().lstrip(".")


class Registry:
    """(src, dst) → ConversionSpec 的有序查找表"""

    def __init__(self):
        self._specs: Dict[Tuple[str, str], ConversionSpec] = {}

    def add(self, src: str, dst: str, spec: ConversionSpec):
        self._specs[(_norm(src), _norm(dst))] = spec

    def get(self, src: str, dst: str) -> Optional[ConversionSpec]:
        return self._specs.get((_norm(src), _norm(dst)))

    def has(self, src: str, dst: str) -> bool:
        return (_norm(src), _norm(dst)) in self._specs

    def edges(self) -> List[Tuple[str, str]]:
        return [(s, d) for (s, d) in sorted(self._specs.keys())]

    def funcs(self) -> Dict[Tuple[str, str], Callable]:
        return {k: v.func for k, v in self._specs.items()}

    def __len__(self):
        return len(self._specs)

    def __contains__(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            return self.has(key[0], key[1])
        return False


# ════════════════════════════════════════════
#  配置类型推导
# ════════════════════════════════════════════

def config_for(src: str, dst: str) -> Tuple[Optional[type], str]:
    from config.format_config import FormatConfig
    from config.image_docx_config import ImageDocxConfig
    from config.pdf_docx_config import PdfDocxConfig
    from config.tts_config import TtsConfig
    from config.stt_config import SttConfig
    from config.ocr_config import OcrConfig
    from services.conversion.direct_table import (
        _IMAGE_TO_DOCX_EXTS, AUDIO_TARGET_EXTS, TXT_OCR_SOURCES,
    )

    src, dst = _norm(src), _norm(dst)
    if dst in ("txt-ocr", "txt_ocr") and src in TXT_OCR_SOURCES:
        return OcrConfig, "config"
    if src == "md" and dst == "docx":
        return FormatConfig, "config"
    if dst == "docx" and f".{src}" in _IMAGE_TO_DOCX_EXTS:
        return ImageDocxConfig, "image_config"
    if src == "pdf" and dst == "docx":
        return PdfDocxConfig, "config"
    if src in ("txt", "md") and f".{dst}" in AUDIO_TARGET_EXTS:
        return TtsConfig, "config"
    if dst == "txt" and f".{src}" in AUDIO_TARGET_EXTS:
        return SttConfig, "config"
    return None, "config"


_FOLDER_OUTPUT_PAIRS = frozenset({
    (".md", ".png"), (".md", ".jpg"), (".md", ".jpeg"),
    (".docx", ".png"), (".docx", ".jpg"), (".docx", ".jpeg"),
    (".pdf", ".png"), (".pdf", ".jpg"), (".pdf", ".jpeg"),
})


def _is_stt_edge(src: str, dst: str) -> bool:
    from services.conversion.direct_table import AUDIO_TARGET_EXTS
    return dst == ".txt" and src in AUDIO_TARGET_EXTS


def build_registry(table: Dict[Tuple[str, str], Callable]) -> Registry:
    reg = Registry()
    for (src, dst), func in table.items():
        cfg_type, kwarg = config_for(src, dst)
        reg.add(src, dst, ConversionSpec(
            func=func,
            config_type=cfg_type,
            config_kwarg=kwarg,
            output_is_folder=(src, dst) in _FOLDER_OUTPUT_PAIRS,
            transitive=not _is_stt_edge(src, dst)))
    return reg


def _default_registry() -> Registry:
    from services.conversion.direct_table import CONVERSION_TABLE
    return build_registry(CONVERSION_TABLE)


REGISTRY: Registry = _default_registry()