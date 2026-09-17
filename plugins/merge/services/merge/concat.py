# -*- coding: utf-8 -*-
"""merge —— 多文件「拼接」编排（业务主入口，1:1 对齐 OCTools/services/merge/concat.py）。

把文件夹内所有文件合并为单个目标文件，支持三类拼接：
  1) 图片 → 文档类（pdf/docx/md/txt/txt-ocr）：每个文件一页/一张，先经 conversion
     逐张转目标格式，再用同格式合并器拼为单文件
  2) 自我拼接 src == dst：同格式合并（doc/image/media 星型枢纽）
  3) 格式转换拼接 src != dst：逐个经 conversion 转换到目标格式，再同格式合并
     （如 两个 mp3 → FLAC：先转 wav 枢纽再合并……由 conversion 完成单文件转换，
       merge 负责把转换结果拼接）

「转换」都委托 conversion 插件（bridge.call_conversion → conversion.convert）；
「合并」都在本插件（same_format_merger / image_merger / media_merger /
audio_merger / video_merger）。
"""
import os
import shutil
import tempfile

from core import formats as FMT
from services.merge.same_format_merger import merge_files
from services.merge.image_merger import merge_gif_animated
from services.merge import bridge

IMAGE_FORMATS = set(FMT.IMAGE_FORMATS)


def _norm_fmt(fmt):
    fmt = str(fmt).lstrip(".").lower()
    return "pptx" if fmt == "pptx-img" else fmt


def list_files(folder, src_fmt):
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
        if (os.path.splitext(p)[1].lstrip(".").lower() == fmt
                or fmt in ("txt", "txt-ocr") and os.path.splitext(p)[1].lstrip(".").lower() == "txt"):
            files.append(p)
    return files


def _convert_via_bridge(src, dst, dst_fmt, config, log, timeout_ms=120000):
    """经 conversion.convert 把单个文件转换到目标格式。返回 True/False。"""
    res = bridge.call_conversion("conversion.convert", {
        "input": src,
        "output": dst,
        "target": dst_fmt,
        "config": config or {},
    }, timeout_ms=timeout_ms)
    if isinstance(res, dict):
        if res.get("error"):
            log(f"⚠ conversion 调用失败: {res['error']}")
            return False
        # conversion.convert 返回 {ok, log, done}
        ok = bool(res.get("ok"))
        if not ok:
            for line in res.get("log") or []:
                log(f"   {line}")
        return ok
    log(f"⚠ conversion.convert 未返回预期结果: {res}")
    return False


def concat(folder, src_fmt, dst_fmt, output, log=lambda m: print(m), config=None) -> bool:
    """把文件夹内所有 src_fmt 文件合并为单个 dst_fmt 文件。"""
    files = list_files(folder, src_fmt)
    if not files:
        log(f"❌ 文件夹中没有 .{_norm_fmt(src_fmt)} 文件: {folder}")
        return False
    if len(files) == 1:
        # 单文件「拼接」= 普通转换（委托 conversion）
        log(f"🧩 单个文件：直接转换为 .{_norm_fmt(dst_fmt)}")
        return _convert_via_bridge(files[0], output, _norm_fmt(dst_fmt),
                                   config, log)

    src = _norm_fmt(src_fmt)
    dst = _norm_fmt(dst_fmt)
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    log(f"🧩 拼接 {len(files)} 个 .{src} 文件 → 单个 .{dst} 文件")

    # ── 1) 图片 → 文档类：逐张转目标格式再同格式合并（每文件一页/一张）──
    if src in IMAGE_FORMATS and FMT.family_of(dst) == "doc":
        log(f"🔄 图片 → {dst.upper()}（{len(files)} 张合并为单文件）")
        return _images_to_doc(files, dst, output, config, log)

    # ── 2) 图片 → 动态 GIF（动画拼接）──
    if src in IMAGE_FORMATS and dst == "gif":
        return merge_gif_animated(files, output, log)

    # ── 3) 自我拼接：src == dst ──
    if src == dst:
        return merge_files(files, dst, output, log, src_fmt=src)

    # ── 4) 格式转换拼接：先逐个转换，再合并 ──
    if dst not in set(FMT.MERGEABLE_FORMATS):
        log(f"❌ 目标格式 {dst.upper()} 不支持拼接合并")
        return False
    tmpdir = tempfile.mkdtemp(prefix="concat_")
    converted = []
    try:
        for i, f in enumerate(files):
            tmp = os.path.join(tmpdir, f"{i:04d}.{dst}")
            log(f"── 转换 {os.path.basename(f)} → 临时 {dst.upper()}")
            try:
                if _convert_via_bridge(f, tmp, dst, config, log):
                    converted.append(tmp)
                else:
                    log(f"⚠ 跳过 {os.path.basename(f)}（转换失败）")
            except Exception as e:
                log(f"⚠ 跳过 {os.path.basename(f)}: {e}")
        if not converted:
            log("❌ 没有任何文件转换成功，无法拼接")
            return False
        log(f"🧩 合并 {len(converted)} 个 {dst.upper()} 文件 → 单文件")
        return merge_files(converted, dst, output, log, src_fmt=dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _images_to_doc(files, dst, output, config, log):
    """图片 → 文档：逐张经 conversion 转目标格式，再同格式合并为单文件。"""
    tmpdir = tempfile.mkdtemp(prefix="img2doc_")
    converted = []
    try:
        for i, f in enumerate(files):
            tmp = os.path.join(tmpdir, f"img{i:04d}.{dst}")
            log(f"── 转换 {os.path.basename(f)} → 临时 {dst.upper()}")
            if _convert_via_bridge(f, tmp, dst, config, log):
                converted.append(tmp)
            else:
                log(f"⚠ 跳过 {os.path.basename(f)}（转换失败）")
        if not converted:
            log("❌ 没有任何图片转换成功，无法合并")
            return False
        log(f"🧩 合并 {len(converted)} 张图片 → 单个 {dst.upper()}")
        return merge_files(converted, dst, output, log, src_fmt=dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)