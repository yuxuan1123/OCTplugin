"""
conversion/services/conversion/star/router.py
───────────────────────────────────────────────
星型自动寻路器，1:1 迁移自 OCTools/services/conversion/star/router.py。
"""

from collections import defaultdict, deque
from typing import Dict, List, Optional, Set

from core import formats as FMT
from services.conversion.registry import Registry
from services.conversion.star.hubs import hub_star_edges


class StarRouter:
    """基于直达边 + 星型枢纽边的自动寻路器"""

    def __init__(self, registry: Registry):
        self._registry = registry
        self._graph: Dict[str, Set[str]] = self._build_graph()

    def _build_graph(self) -> Dict[str, Set[str]]:
        graph: Dict[str, Set[str]] = defaultdict(set)
        for (src, dst) in self._registry.edges():
            spec = self._registry.get(src, dst)
            if spec is not None and (spec.output_is_folder or not spec.transitive):
                continue
            graph[src].add(dst)
        for (m, h) in hub_star_edges():
            if self._registry.has(m, h):
                graph[m].add(h)
            if self._registry.has(h, m):
                graph[h].add(m)
        return {k: v for k, v in graph.items()}

    def find_path(self, src: str, dst: str) -> Optional[List[str]]:
        """BFS 最短路径；返回 [src, fmt2, ..., dst]，找不到返回 None"""
        src, dst = FMT.resolve(src), FMT.resolve(dst)
        if src == dst:
            return [src]
        if src not in self._graph or dst not in self._graph:
            return None
        prev: Dict[str, str] = {src: ""}
        queue = deque([src])
        while queue:
            cur = queue.popleft()
            if cur == dst:
                break
            for nxt in sorted(self._graph.get(cur, ())):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        if dst not in prev:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def is_reachable(self, src: str, dst: str) -> bool:
        return self.find_path(src, dst) is not None

    def reachable_from(self, src: str) -> Set[str]:
        src = FMT.resolve(src)
        seen: Set[str] = set()
        queue = deque([src])
        while queue:
            cur = queue.popleft()
            for nxt in self._graph.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        seen.discard(src)
        return seen

    def path_hint(self, src: str, dst: str) -> str:
        path = self.find_path(src, dst)
        return " → ".join(path) if path else ""


def _default_router() -> StarRouter:
    from services.conversion.registry import REGISTRY
    return StarRouter(REGISTRY)


router = _default_router()