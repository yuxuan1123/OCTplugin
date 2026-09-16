"""
conversion/services/conversion/star/hubs.py
───────────────────────────────────────────────
星型枢纽声明，1:1 迁移自 OCTools/services/conversion/star/hubs.py。
"""

from dataclasses import dataclass
from typing import Tuple

from core import formats as FMT


@dataclass(frozen=True)
class Hub:
    """一个星型枢纽"""
    name: str                       # 枢纽名（可读）
    hub: str                        # 枢纽格式 id
    members: Tuple[str, ...]        # 族内成员（含枢纽本身；经枢纽两两连通）
    note: str = ""                  # 说明


def _members(*fmt_ids: str) -> Tuple[str, ...]:
    return tuple(fmt_ids)


HUBS: Tuple[Hub, ...] = (
    Hub("向量枢纽", "svg", _members(*FMT.vector_ids()),
        note="向量图以 svg 为转换点（svg ↔ 位图，经 jpg 位图枢纽可到任意位图）"),
    Hub("位图枢纽", "jpg", _members(*FMT.bitmap_ids()),
        note="位图以 jpg 为转换点（png/bmp/webp/tiff/heic/ppm/pgm ↔ jpg）"),
    Hub("文档枢纽", "md", _members(*FMT.doc_ids(), "html", "pptx"),
        note="文档以 md 为转换点（docx/pdf/txt/html/pptx ↔ md；OCR 产物 txt-ocr 也归入文档）"),
    Hub("表格枢纽", "json", _members(*[f.id for f in FMT.FORMATS if f.family == "table"]),
        note="表格以 json 为转换点（xlsx ↔ json ↔ csv）"),
    Hub("视频枢纽", "mp4", _members(*[f.id for f in FMT.FORMATS if f.family == "video"]),
        note="视频以 mp4 为转换点"),
    Hub("音频枢纽", "wav", _members(*[f.id for f in FMT.FORMATS if f.family == "audio"]),
        note="音频以 wav 为转换点"),
)


def hub_of(fmt: str) -> Hub:
    fmt = FMT.resolve(fmt)
    for h in HUBS:
        if fmt in h.members:
            return h
    return None


def hub_star_edges() -> Tuple[Tuple[str, str], ...]:
    """星型边：族内 成员 ⇄ 枢纽（保证族内连通；可执行性由 router 再过滤）"""
    edges = []
    for h in HUBS:
        for m in h.members:
            if m == h.hub:
                continue
            edges.append((m, h.hub))
            edges.append((h.hub, m))
    return tuple(edges)