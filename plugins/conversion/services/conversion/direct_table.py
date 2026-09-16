"""
conversion/services/conversion/direct_table.py
───────────────────────────────────────────────
直达转换表，1:1 迁移自 OCTools/services/conversion/direct_table.py。
"""

import os

from core.engines import (
    document_engine,
    presentation_engine,
    spreadsheet_engine,
)
from core.engines.audio_engine import audio_to_audio
from core.engines.media_engine import (
    MEDIA_TARGETS,
    convert as media_convert,
    is_media_format,
)
from core.engines.speech_engine import audio_to_text
from core.engines.tts_engine import text_to_audio as tts_text_to_audio
from core.engines.ffmpeg_utils import AUDIO_FORMATS
from core.utils.file_handler import file_exists, safe_name


# ════════════════════════════════════════════
#  格式归一化映射
# ════════════════════════════════════════════

FORMAT_ALIASES = {
    ".markdown": ".md", ".mdown": ".md", ".mkd": ".md",
    ".htm": ".html",
    ".xls": ".xlsx", ".xlsm": ".xlsx",
    ".jpeg": ".jpg", ".tif": ".tiff",
    ".png": ".png", ".bmp": ".bmp", ".gif": ".gif", ".webp": ".webp",
    ".m4v": ".mp4", ".oga": ".ogg", ".mka": ".mkv",
}


# ════════════════════════════════════════════
#  主转换表：(from_ext, to_ext) -> func
# ════════════════════════════════════════════

CONVERSION_TABLE = {
    # ── 文档类 ──
    (".md", ".docx"): document_engine.md_to_docx,
    (".md", ".pdf"): document_engine.md_to_pdf,
    (".md", ".txt"): document_engine.md_to_txt,
    (".md", ".png"): document_engine.md_to_images,
    (".md", ".jpg"): document_engine.md_to_images,
    (".md", ".jpeg"): document_engine.md_to_images,
    (".docx", ".md"): document_engine.docx_to_md,
    (".docx", ".pdf"): document_engine.docx_to_pdf,
    (".docx", ".txt"): document_engine.docx_to_txt,
    (".docx", ".png"): document_engine.docx_to_images,
    (".docx", ".jpg"): document_engine.docx_to_jpgs,
    (".docx", ".jpeg"): document_engine.docx_to_jpgs,
    (".pdf", ".md"): document_engine.pdf_to_md,
    (".pdf", ".docx"): document_engine.pdf_to_docx,
    (".pdf", ".txt"): document_engine.pdf_to_txt,
    (".pdf", ".png"): document_engine.pdf_to_images,
    (".pdf", ".jpg"): document_engine.pdf_to_jpgs,
    (".pdf", ".jpeg"): document_engine.pdf_to_jpgs,
    (".txt", ".md"): document_engine.txt_to_md,
    (".txt", ".docx"): document_engine.txt_to_docx,
    (".txt", ".pdf"): document_engine.txt_to_pdf,
    (".txt", ".png"): document_engine.txt_to_images,
    (".txt", ".jpg"): document_engine.txt_to_images,
    (".txt", ".jpeg"): document_engine.txt_to_images,
    (".png", ".pdf"): document_engine.images_to_pdf,
    (".png", ".md"): document_engine.images_to_md,
    (".png", ".txt"): document_engine.images_to_txt,
    (".png", ".docx"): document_engine.images_to_docx,
    (".jpg", ".docx"): document_engine.images_to_docx,
    (".jpeg", ".docx"): document_engine.images_to_docx,
    (".bmp", ".docx"): document_engine.images_to_docx,
    (".gif", ".docx"): document_engine.images_to_docx,
    (".webp", ".docx"): document_engine.images_to_docx,
    (".tiff", ".docx"): document_engine.images_to_docx,
    (".svg", ".docx"): document_engine.images_to_docx,
    (".svg", ".pdf"): document_engine.images_to_pdf,
    (".svg", ".md"): document_engine.images_to_md,
    (".svg", ".txt"): document_engine.images_to_txt,
    # ── 表格类 ──
    (".xlsx", ".json"): spreadsheet_engine.xlsx_to_json,
    (".xlsx", ".csv"): spreadsheet_engine.xlsx_to_csv,
    (".json", ".xlsx"): spreadsheet_engine.json_to_xlsx,
    (".json", ".csv"): spreadsheet_engine.json_to_csv,
    (".csv", ".xlsx"): spreadsheet_engine.csv_to_xlsx,
    (".csv", ".json"): spreadsheet_engine.csv_to_json,
    # ── 演示类 ──
    (".md", ".html"): presentation_engine.md_to_html,
    (".md", ".pptx"): presentation_engine.md_to_pptx,
    (".html", ".md"): presentation_engine.html_to_md,
    (".html", ".pptx"): presentation_engine.html_to_pptx,
    (".pptx", ".md"): presentation_engine.pptx_to_md,
    (".pptx", ".html"): presentation_engine.pptx_to_html,
    (".pptx", ".pdf"): presentation_engine.pptx_to_pdf,
    (".pptx", ".pptx"): presentation_engine.pptx_to_pptx_image,  # PPT → 图片版PPT
}

# ── OCR 目标：txt-ocr ──
TXT_OCR_SRC_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif",
                    ".webp", ".tiff", ".heic", ".pdf"}
TXT_OCR_SOURCES = {ext.lstrip(".") for ext in TXT_OCR_SRC_EXTS}
for _ext in TXT_OCR_SRC_EXTS:
    CONVERSION_TABLE[(_ext, ".txt-ocr")] = document_engine._to_txt_ocr

# ── TTS 目标：txt / md → 音频 ──
AUDIO_TARGET_EXTS = {f".{a}" for a in AUDIO_FORMATS}
for _aext in AUDIO_TARGET_EXTS:
    CONVERSION_TABLE[(".txt", _aext)] = tts_text_to_audio
    CONVERSION_TABLE[(".md", _aext)] = tts_text_to_audio

# ── STT 目标：音频 → txt ──
STT_SOURCE_EXTS = AUDIO_TARGET_EXTS
for _aext in STT_SOURCE_EXTS:
    CONVERSION_TABLE[(_aext, ".txt")] = audio_to_text


def _register_media_conversions():
    for src, targets in MEDIA_TARGETS.items():
        for dst in targets:
            key = (f".{src}", f".{dst}")
            if key not in CONVERSION_TABLE:
                CONVERSION_TABLE[key] = media_convert


_register_media_conversions()


def _normalize_ext(path):
    ext = os.path.splitext(path)[1].lower()
    return FORMAT_ALIASES.get(ext, ext)


_IMAGE_TO_DOCX_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".svg"}

_KNOWN_SRC_EXTS = {k[0] for k in CONVERSION_TABLE}


def _infer_source_format(path: str) -> str:
    """从路径推断源格式（含扩展名）。文件夹扫描返回第一个受支持文件格式。"""
    if os.path.isdir(path):
        try:
            for name in sorted(os.listdir(path)):
                p = os.path.join(path, name)
                if not os.path.isfile(p):
                    continue
                ext = _normalize_ext(p)
                if ext in _KNOWN_SRC_EXTS:
                    return ext
        except Exception:
            pass
        return ""
    return _normalize_ext(path)


def _bridge(input_path, output_path, via_ext, direct_fn, log):
    tmp = output_path + ".bridge" + via_ext
    try:
        if not direct_fn(input_path, tmp, log):
            return False
        return convert(tmp, output_path, log)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def convert(input_path, output_path, log=lambda m: print(m), config=None, target=None):
    """统一转换入口（兼容旧 convert 语义）——直接调用各引擎（无星型寻路）"""
    if not file_exists(input_path, log):
        return False
    src_ext = _infer_source_format(input_path)
    if not src_ext:
        log(f"❌ 无法识别源文件/文件夹格式: {input_path}")
        return False
    dst_ext = _normalize_ext(output_path)
    if target and target.lower().replace("-", "_") == "txt_ocr":
        dst_ext = ".txt-ocr"
    elif not dst_ext and target:
        dst_ext = "." + target.lstrip(".")
    key = (src_ext, dst_ext)
    if key in CONVERSION_TABLE:
        func = CONVERSION_TABLE[key]
        if key == (".md", ".docx"):
            return func(input_path, output_path, log, config=config)
        if key == (".pdf", ".docx"):
            return func(input_path, output_path, log, config=config)
        if dst_ext == ".docx" and src_ext in _IMAGE_TO_DOCX_EXTS:
            return func(input_path, output_path, log, image_config=config)
        if src_ext in (".txt", ".md") and dst_ext in AUDIO_TARGET_EXTS:
            return func(input_path, output_path, log, config=config)
        if src_ext in AUDIO_TARGET_EXTS and dst_ext == ".txt":
            return func(input_path, output_path, log, config=config)
        return func(input_path, output_path, log)
    if is_media_format(src_ext.strip(".")) and is_media_format(dst_ext.strip(".")):
        log(f"❌ 不支持的媒体转换: {src_ext} → {dst_ext}")
        return False
    log(f"⚠ 没有直达路径 {src_ext} → {dst_ext}，尝试桥接…")
    doc_exts = {".md", ".docx", ".pdf", ".txt"}
    img_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".svg"}
    tab_exts = {".xlsx", ".json", ".csv"}
    if src_ext in doc_exts and dst_ext in doc_exts:
        return _bridge(input_path, output_path, ".md",
                       lambda i, o, lg: CONVERSION_TABLE.get((src_ext, ".md"), lambda *_: False)(i, o, lg),
                       log)
    if src_ext in img_exts and dst_ext in doc_exts - {".png"}:
        return document_engine.images_to_pdf(input_path, output_path, log) if dst_ext == ".pdf" else False
    log(f"❌ 不支持的转换: {src_ext} → {dst_ext}")
    return False