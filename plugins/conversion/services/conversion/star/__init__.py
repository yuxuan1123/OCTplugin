"""
conversion/services/conversion/star/
───────────────────────────────────────────────
星型保底转换（hubs / router / runner），1:1 迁移自 OCTools/services/conversion/star/。
"""

from services.conversion.star.hubs import HUBS, hub_of, hub_star_edges  # noqa: F401
from services.conversion.star.router import StarRouter, router  # noqa: F401
from services.conversion.star.runner import run_path  # noqa: F401

__all__ = ["HUBS", "hub_of", "hub_star_edges", "StarRouter", "router", "run_path"]