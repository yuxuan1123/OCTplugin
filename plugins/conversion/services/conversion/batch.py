"""
conversion/services/conversion/batch.py
───────────────────────────────────────────────
多文件「批量转换」引擎（业务逻辑层），1:1 迁移自 OCTools/services/conversion/batch.py。
"""

import os

from core import formats as FMT
from core.utils.file_handler import read_text, write_text, safe_name
from services.conversion import planner


DOC_FORMATS = set(FMT.doc_ids())
MERGEABLE_FORMATS = set(FMT.mergeable_ids())

from core.engines.ffmpeg_utils import (  # noqa: E402
    VIDEO_FORMATS,
    AUDIO_FORMATS,
    AUDIO_CODECS,
    AUDIO_EXTRA,
    VIDEO_CODECS,
    _run_ffmpeg,
)
MEDIA_MERGE_EXTS = {f".{x}" for x in VIDEO_FORMATS + AUDIO_FORMATS}
IMAGE_CONTACT_EXTS = {f".{x}" for x in FMT.media_image_ids() if x not in ("gif", "svg")}


def _norm_fmt(fmt):
    fmt = fmt.lstrip(".").lower()
    return "pptx" if fmt == "pptx-img" else fmt


def list_files(folder: str, src_fmt: str) -> list:
    fmt = _norm_fmt(src_fmt)
    files = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return files
    for name in names:
        p = os.path.join(folder, name)
        if not os.path.isfile(p):
            continue
        if FMT.resolve(os.path.splitext(p)[1].lstrip(".")) == fmt:
            files.append(p)
    return files


def can_batch(src_fmt: str, dst_fmt: str) -> bool:
    return planner.can_batch(_norm_fmt(src_fmt), _norm_fmt(dst_fmt))


def can_concat(src_fmt: str, dst_fmt: str) -> bool:
    dst_raw = dst_fmt.lstrip(".").lower()
    if dst_raw == "pptx-img":
        return False
    return planner.can_concat(_norm_fmt(src_fmt), _norm_fmt(dst_raw))


def batch_convert(folder: str, src_fmt: str, dst_fmt: str, out_dir: str,
                  log=lambda m: print(m), config=None) -> bool:
    from services.conversion.pipeline import convert as _convert
    files = list_files(folder, src_fmt)
    if not files:
        log(f"❌ 文件夹中没有 .{_norm_fmt(src_fmt)} 文件: {folder}")
        return False
    os.makedirs(out_dir, exist_ok=True)

    actual_ext = {"pptx-img": "pptx", "txt-ocr": "txt", "txt_ocr": "txt"}.get(dst_fmt, dst_fmt)
    ok_cnt = fail_cnt = 0
    for f in files:
        base = safe_name(f)
        name = (f"{base}_稳定版.{actual_ext}" if dst_fmt == "pptx-img"
                else f"{base}.{actual_ext}")
        out = os.path.join(out_dir, name)
        log(f"── 转换 {os.path.basename(f)} → {name}")
        try:
            if _convert(f, out, log=log, config=config, target=dst_fmt):
                ok_cnt += 1
            else:
                fail_cnt += 1
        except Exception as e:
            log(f"❌ {e}")
            fail_cnt += 1
    log(f"📊 批量转换完成: 成功 {ok_cnt}，失败 {fail_cnt} → {out_dir}")
    return ok_cnt > 0