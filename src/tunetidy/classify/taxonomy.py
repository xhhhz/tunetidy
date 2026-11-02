"""分类体系：官方曲风树上的祖先查找与按名称/路径查找。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from tunetidy.models import GenreTag, StyleNode


class TaxonomyLookupError(ValueError):
    """按名称或路径找不到曲风，或名称有歧义。"""


class Taxonomy:
    def __init__(self, nodes: Iterable[StyleNode]) -> None:
        self.nodes = {n.tag_id: n for n in nodes}
        for n in self.nodes.values():
            if n.parent_id is not None and n.parent_id not in self.nodes:
                raise ValueError(f"曲风 {n.tag_id} 的父节点 {n.parent_id} 不在树里")
        self.version = self._version()
        self._by_name: dict[str, list[StyleNode]] = {}
        self._by_path: dict[str, StyleNode] = {}
        for n in self.nodes.values():
            self._by_name.setdefault(n.name, []).append(n)
            self._by_path[self.path(n.tag_id)] = n

    def _version(self) -> str:
        rows = sorted((n.tag_id, n.name, n.parent_id or "") for n in self.nodes.values())
        digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
        return f"netease-l1-{digest[:12]}"

    def root_name(self, tag: GenreTag) -> tuple[str, bool]:
        """标签所属一级曲风的名称，以及 tag_id 是否在树里。

        一律按 tag_id 找祖先。树里没有的 id（平台新增曲风、本地树过期）
        退回按名称"一级-二级"的前缀，并标出来，便于在依据里展示。
        """
        if tag.tag_id not in self.nodes:
            return tag.name.split("-", 1)[0], False
        return self.chain(tag.tag_id)[0].name, True

    def chain(self, tag_id: str) -> list[StyleNode]:
        """从一级到该节点的路径。"""
        node = self.nodes[tag_id]
        out = [node]
        seen = {node.tag_id}
        while node.parent_id is not None:
            node = self.nodes[node.parent_id]
            if node.tag_id in seen:  # 防御：树里有环
                raise ValueError(f"曲风树在 {node.tag_id} 处有环")
            seen.add(node.tag_id)
            out.append(node)
        return list(reversed(out))

    def path(self, tag_id: str) -> str:
        """全路径名，如 "流行-华语流行"。

        名称本身可能含 "-"（如 "Jump-Up"），所以只能整串比较，不能按 "-" 反拆。
        """
        return "-".join(n.name for n in self.chain(tag_id))

    def is_at_or_below(self, tag_id: str, ancestor_id: str) -> bool:
        """tag_id 是否等于 ancestor_id 或是它的下级。"""
        return tag_id in self.nodes and any(n.tag_id == ancestor_id for n in self.chain(tag_id))

    def find(self, spec: str) -> StyleNode:
        """按名称（如 "华语流行"）或全路径（如 "流行-华语流行"）找曲风。

        名称在树里唯一时可以直接写名称；有重名时必须写全路径。
        """
        spec = spec.strip()
        if spec in self._by_path:
            return self._by_path[spec]
        named = self._by_name.get(spec, [])
        if len(named) == 1:
            return named[0]
        if named:
            paths = "、".join(sorted(self.path(n.tag_id) for n in named))
            raise TaxonomyLookupError(f"曲风「{spec}」有重名，请写全路径：{paths}")
        raise TaxonomyLookupError(f'曲风树里没有「{spec}」；可写一级名或"一级-二级"全路径')

    def level1_names(self) -> list[str]:
        return sorted(n.name for n in self.nodes.values() if n.parent_id is None)
