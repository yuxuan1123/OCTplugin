"""
conversion/services/conversion/pipeline.py
───────────────────────────────────────────────
统一转换入口：直达 → 星型保底自动寻路，1:1 迁移自 OCTools/services/conversion/pipeline.py。
"""

import os
from typing import Callable, Optional

from core import formats as FMT
from core.engines.media_engine import is_media_format
from services.conversion.direct_table import _infer_source_format, _normalize_ext
from services.conversion.registry import REGISTRY, ConversionSpec
from services.conversion.star.router import router as STAR_ROUTER
from services.conversion.star.runner import run_path


def _call_spec(spec: ConversionSpec, input_path: str, output_path: str,
               log: Callable[[str], None], config) -> bool:
    if spec.config_type is None or config is None:
        return spec.func(input_path, output_path, log)
    return spec.func(input_path, output_path, log, **{spec.config_kwarg: config})


def convert(input_path: str, output_path: str,
            log: Callable[[str], None] = lambda m: print(m),
            config=None, target: Optional[str] = None) -> bool:
    """统一转换入口（直达 → 星型保底自动寻路）"""
    if not os.path.exists(input_path):
        log(f"❌ 文件不存在: {input_path}")
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
    src, dst = src_ext.lstrip("."), dst_ext.lstrip(".")

    spec = REGISTRY.get(src, dst)
    if spec is not None:
        return _call_spec(spec, input_path, output_path, log, config)

    path = STAR_ROUTER.find_path(src, dst)
    if path:
        log(f"⭐ 无直达路径 {src} → {dst}，星型自动寻路: {' → '.join(path)}")
        return run_path(input_path, output_path, path, REGISTRY, log, config)

    if is_media_format(src) and is_media_format(dst):
        log(f"❌ 不支持的媒体转换: {src} → {dst}")
        return False
    log(f"❌ 不支持的转换: {src} → {dst}（无直达路径，星型寻路也未找到）")
    return False