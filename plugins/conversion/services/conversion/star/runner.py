"""
conversion/services/conversion/star/runner.py
───────────────────────────────────────────────
星型路径执行器，1:1 迁移自 OCTools/services/conversion/star/runner.py。
"""

import os
import shutil
import tempfile
from typing import Callable, List

from services.conversion.registry import Registry, ConversionSpec


def _call_spec(spec: ConversionSpec, input_path: str, output_path: str,
               log: Callable[[str], None], config) -> bool:
    if spec.config_type is None or config is None:
        return spec.func(input_path, output_path, log)
    kwargs = {spec.config_kwarg: config}
    return spec.func(input_path, output_path, log, **kwargs)


def run_path(input_path: str, output_path: str, path: List[str],
             registry: Registry, log: Callable[[str], None],
             config=None) -> bool:
    if not path or len(path) < 2:
        log(f"❌ 星型路径无效: {path}")
        return False

    tmpdir = tempfile.mkdtemp(prefix="star_route_")
    try:
        cur = input_path
        cur_fmt = path[0]
        for i in range(1, len(path)):
            nxt_fmt = path[i]
            is_last = (i == len(path) - 1)
            out = output_path if is_last else os.path.join(tmpdir, f"hop{i}.{nxt_fmt}")

            spec = registry.get(cur_fmt, nxt_fmt)
            if spec is None:
                log(f"❌ 星型路径断链: {cur_fmt} → {nxt_fmt} 没有直达转换")
                return False
            log(f"   ⭐ 星型跳转 {i}/{len(path) - 1}: {cur_fmt} → {nxt_fmt}")
            ok = _call_spec(spec, cur, out, log,
                            config if is_last else None)
            if not ok or not os.path.exists(out):
                log(f"❌ 星型跳转失败: {cur_fmt} → {nxt_fmt}")
                return False
            cur, cur_fmt = out, nxt_fmt
        log(f"✅ 星型转换完成 → {output_path}")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)