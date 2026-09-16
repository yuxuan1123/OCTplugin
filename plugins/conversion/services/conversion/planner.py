"""
conversion/services/conversion/planner.py
───────────────────────────────────────────────
统一可达性规划，1:1 迁移自 OCTools/services/conversion/planner.py。
"""

from typing import List, Set

from core import formats as FMT
from services.conversion.registry import REGISTRY
from services.conversion.star.router import router as STAR_ROUTER


def _norm(fmt: str) -> str:
    return FMT.resolve(fmt)


def is_reachable(src: str, dst: str) -> bool:
    src, dst = _norm(src), _norm(dst)
    if not src or not dst:
        return False
    if REGISTRY.has(src, dst):
        return True
    return STAR_ROUTER.is_reachable(src, dst)


def path_of(src: str, dst: str) -> List[str]:
    src, dst = _norm(src), _norm(dst)
    if not src or not dst:
        return []
    if REGISTRY.has(src, dst):
        return [src, dst]
    return STAR_ROUTER.find_path(src, dst) or []


def reachable_from(src: str) -> Set[str]:
    src = _norm(src)
    out = set(STAR_ROUTER.reachable_from(src))
    out.update(d for (s, d) in REGISTRY.edges() if s == src)
    out.discard(src)
    return out


def can_batch(src: str, dst: str) -> bool:
    src, dst = _norm(src), _norm(dst)
    if src in set(FMT.media_image_ids()) and dst in set(FMT.doc_ids()):
        return False
    return is_reachable(src, dst)


def can_concat(src: str, dst: str) -> bool:
    src_raw = str(src or "").strip().lower().lstrip(".")
    dst_raw = str(dst or "").strip().lower().lstrip(".")
    if dst_raw == "pptx-img":
        return False
    src, dst = _norm(src_raw), _norm(dst_raw)
    if src in set(FMT.media_image_ids()) and dst in set(FMT.doc_ids()):
        return True
    if src == dst:
        return dst in set(FMT.mergeable_ids())
    if dst not in set(FMT.mergeable_ids()):
        return False
    return is_reachable(src, dst)


def reachable_targets(src: str, mode: str = "convert") -> List[str]:
    """按模式返回 src 的全部可用目标格式"""
    src = _norm(src)
    check = can_concat if mode == "concat" else can_batch
    return [f for f in FMT.target_ids()
            if f != src and check(src, f)]