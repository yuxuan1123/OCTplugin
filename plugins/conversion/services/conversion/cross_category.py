"""
conversion/services/conversion/cross_category.py
───────────────────────────────────────────────
跨类转换调度（业务逻辑层），1:1 迁移自 OCTools/services/conversion/cross_category.py。
"""

from typing import List

from core import formats as FMT
from services.conversion.registry import REGISTRY
from services.conversion.star.router import router as STAR_ROUTER


def is_cross_category(src: str, dst: str) -> bool:
    sf, df = FMT.family_of(src), FMT.family_of(dst)
    return bool(sf and df and sf != df)


def cross_path(src: str, dst: str) -> List[str]:
    src, dst = FMT.resolve(src), FMT.resolve(dst)
    if not is_cross_category(src, dst):
        return []
    if REGISTRY.has(src, dst):
        return [src, dst]
    return STAR_ROUTER.find_path(src, dst) or []


def cross_reachable(src: str, dst: str) -> bool:
    return bool(cross_path(src, dst))


def cross_targets_from(src: str) -> List[str]:
    src = FMT.resolve(src)
    out = []
    for f in FMT.target_ids():
        if f == src:
            continue
        if is_cross_category(src, f) and cross_reachable(src, f):
            out.append(f)
    return sorted(out)