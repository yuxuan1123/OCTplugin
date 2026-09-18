"""
translation/core/engines/ocr_engine.py
───────────────────────────────────────────────
OCR 文字识别引擎（图片 → 文本）—— 核心引擎层。1:1 复刻 OCTools。

基于 PaddleOCR PP-OCRv6_tiny（onnxruntime），惰性单例初始化，线程安全。
"""

# -*- coding: utf-8 -*-

import os
import tempfile
import shutil
import threading

from config.ui_config import CONFIG as _C

# ════════════════════════════════════════════
#  运行环境（须在 import paddleocr 之前设置）
# ════════════════════════════════════════════

_PADDLE_CACHE = _C.model_path("paddle_cache")

if not os.environ.get("PADDLE_PDX_CACHE_HOME"):
    if _PADDLE_CACHE and os.path.isdir(_PADDLE_CACHE):
        os.environ["PADDLE_PDX_CACHE_HOME"] = _PADDLE_CACHE

os.environ.setdefault("FLAGS_use_mkldnn", "false")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "true")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".heic")


# ════════════════════════════════════════════
#  单例
# ════════════════════════════════════════════

_OCR = {}
_OCR_FAILED = {}
_OCR_LOCK = threading.Lock()


def get_ocr(lang: str = "ch"):
    """惰性创建 PaddleOCR 实例（线程安全），不同语言独立缓存。"""
    lang = (lang or "ch").strip() or "ch"
    if lang in _OCR:
        return _OCR[lang]
    if lang in _OCR_FAILED:
        raise _OCR_FAILED[lang]
    with _OCR_LOCK:
        if lang in _OCR:
            return _OCR[lang]
        if lang in _OCR_FAILED:
            raise _OCR_FAILED[lang]
        try:
            from paddleocr import PaddleOCR
            _OCR[lang] = PaddleOCR(
                text_detection_model_name="PP-OCRv6_tiny_det",
                text_recognition_model_name="PP-OCRv6_tiny_rec",
                engine="onnxruntime",
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            return _OCR[lang]
        except Exception as e:
            _OCR_FAILED[lang] = RuntimeError(
                f"OCR 初始化失败（请确认已安装 paddleocr / onnxruntime）: {e}")
            raise _OCR_FAILED[lang]


def warmup_ocr(lang: str = "ch") -> bool:
    """后台预热：提前加载 OCR 模型"""
    try:
        get_ocr(lang)
        return True
    except Exception:
        return False


# ════════════════════════════════════════════
#  识别入口
# ════════════════════════════════════════════

def ocr_image(image_path: str, lang: str = "ch") -> str:
    """识别单张图片，返回识别出的文本（每行一条）"""
    ocr = get_ocr(lang)
    result = ocr.predict(image_path)
    lines = []
    for res in result:
        if not isinstance(res, dict):
            continue
        for t in res.get("rec_texts", []):
            if t and str(t).strip():
                lines.append(str(t).strip())
    return "\n".join(lines)


def ocr_pil_image(pil, lang: str = "ch") -> str:
    """识别内存中的 PIL 图片，返回识别出的文本（无需写盘）"""
    import numpy as np
    ocr = get_ocr(lang)
    img = pil.convert("RGB")
    result = ocr.predict(np.array(img))
    lines = []
    for res in result:
        if not isinstance(res, dict):
            continue
        for t in res.get("rec_texts", []):
            if t and str(t).strip():
                lines.append(str(t).strip())
    return "\n".join(lines)


def ocr_images(image_paths, lang: str = "ch") -> str:
    """识别多张图片，按顺序拼接（每张之间空一行）"""
    parts = []
    for p in image_paths:
        parts.append(ocr_image(p, lang))
    return "\n\n".join(parts).strip()


def ocr_pdf(pdf_path: str, log=lambda m: print(m), lang: str = "ch") -> str:
    """识别 PDF：先把每页渲染为图片，再逐页 OCR，返回拼接文本"""
    pages = _render_pdf_pages(pdf_path, log)
    if not pages:
        log("❌ 无法渲染 PDF 页面（缺少 pypdfium2 等渲染依赖）")
        return ""
    try:
        parts = []
        for i, pg in enumerate(pages):
            log(f"   🔍 OCR 第 {i + 1}/{len(pages)} 页")
            try:
                parts.append(ocr_image(pg, lang))
            except Exception as e:
                log(f"   ⚠ 第 {i + 1} 页 OCR 失败: {e}")
        return "\n\n".join(parts).strip()
    finally:
        cleanup_temp_dir(os.path.dirname(pages[0]))


def _render_pdf_pages(pdf_path: str, log):
    tmp_dir = tempfile.mkdtemp(prefix="pdf_ocr_")
    generated = []
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            n_pages = len(pdf)
            for i in range(n_pages):
                bitmap = pdf[i].render(scale=200 / 72.0)
                pil = bitmap.to_pil()
                p = os.path.join(tmp_dir, f"page_{i + 1:03d}.png")
                pil.save(p, "PNG")
                generated.append(p)
        finally:
            pdf.close()
    except Exception as e:
        log(f"⚠ pypdfium2 渲染失败: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return []
    return generated


def cleanup_temp_dir(tmp_dir: str):
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)