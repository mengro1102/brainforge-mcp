"""위키 마크다운에서 지식 그래프를 빌드하고 조회하는 핵심 엔진."""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import date
from pathlib import Path


# ── 정규식 ─────────────────────────────────────────────────

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

CAUSAL_STRICT_RE = re.compile(
    r"\[\[([^\]]+)\]\]\s*→\(([^)]+)\)→\s*\[\[([^\]]+)\]\]"
)
CAUSAL_FLEX_RE = re.compile(
    r"\[\[([^\]]+)\]\][^\n→]*→\(([^)]+)\)→\s*(.+?)(?:\n|$)"
)
CAUSAL_REVERSE_RE = re.compile(
    r"([^\n\[]+?)→\(([^)]+)\)→\s*\[\[([^\]]+)\]\]"
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
YAML_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)


# ── 유틸리티 ───────────────────────────────────────────────

def normalize_to_slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def parse_frontmatter(content: str) -> dict:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    fm = {}
    for m in YAML_FIELD_RE.finditer(match.group(1)):
        key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
        fm[key] = val
    return fm


def build_alias_map(nodes: dict) -> dict:
    alias = {}
    for nid, n in nodes.items():
        alias[nid] = nid
        alias[nid.lower()] = nid
        label = n.get("label", "")
        if label:
            alias[label.lower()] = nid
            alias[normalize_to_slug(label)] = nid
            paren = re.search(r"\(([^)]+)\)", label)
            if paren:
                alias[paren.group(1).lower()] = nid
                alias[normalize_to_slug(paren.group(1))] = nid
                korean = label[: label.index("(")].strip()
                alias[korean.lower()] = nid
    return alias


def resolve_target(target_text: str, alias_map: dict) -> str:
    t = target_text.strip()
    if t in alias_map:
        return alias_map[t]
    if t.lower() in alias_map:
        return alias_map[t.lower()]
    slug = normalize_to_slug(t)
    if slug in alias_map:
        return alias_map[slug]
    return t


# ── 그래프 빌드 ────────────────────────────────────────────

class WikiGraph:
    """마크다운 위키에서 빌드된 지식 그래프."""

    def __init__(self, wiki_dir: str | Path):
        self.wiki_dir = Path(wiki_dir)
        self.nodes: dict[str, dict] = {}
        self.links: list[dict] = []
        self._label_to_id: dict[str, str] = {}

    def build(self) -> None:
        """wiki_dir의 마크다운을 파싱하여 그래프를 빌드한다."""
        self.nodes, self.links = self._extract(self.wiki_dir)
        self.links = self._deduplicate(self.links)
        self._add_unresolved()
        self._build_label_map()

    def _build_label_map(self) -> None:
        self._label_to_id = {}
        for n in self.nodes.values():
            self._label_to_id[n.get("label", "").lower()] = n.get("id", "")
        for nid in self.nodes:
            self._label_to_id[nid.lower()] = nid

    def resolve(self, query: str) -> str | None:
        q = query.lower().strip()
        if q in self._label_to_id:
            return self._label_to_id[q]
        for label, nid in self._label_to_id.items():
            if q in label:
                return nid
        return None

    def degree_map(self) -> dict[str, int]:
        deg: dict[str, int] = {}
        for l in self.links:
            deg[l["source"]] = deg.get(l["source"], 0) + 1
            deg[l["target"]] = deg.get(l["target"], 0) + 1
        return deg

    def save(self, output_path: Path) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        graph = {
            "nodes": [{"id": k, **v} for k, v in self.nodes.items()],
            "links": self.links,
            "metadata": {
                "total_nodes": len(self.nodes),
                "total_edges": len(self.links),
                "causal_edges": sum(1 for l in self.links if l["type"] == "causal"),
                "wikilink_edges": sum(1 for l in self.links if l["type"] == "wikilink"),
            },
        }
        output_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        return graph["metadata"]

    def load(self, graph_path: Path) -> None:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        self.nodes = {n["id"]: n for n in data["nodes"]}
        self.links = data["links"]
        self._build_label_map()


    # ── 내부 메서드 ────────────────────────────────────────

    def _extract(self, wiki_dir: Path):
        nodes = {}
        edges = []
        md_files = [f for f in wiki_dir.rglob("*.md") if f.name != ".steering.md"]

        file_contents = {}
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            rel_path = md_file.relative_to(wiki_dir)
            slug = md_file.stem
            parts = rel_path.parts
            category = parts[0] if len(parts) > 1 else "root"
            nodes[slug] = {
                "label": fm.get("title", slug),
                "file": str(rel_path),
                "category": category,
                "tags": fm.get("tags", ""),
            }
            file_contents[slug] = content

        alias_map = build_alias_map(nodes)

        for slug, content in file_contents.items():
            # 위키링크
            for m in WIKILINK_RE.finditer(content):
                raw = m.group(1).strip()
                target = resolve_target(raw, alias_map)
                if target != slug:
                    edges.append({
                        "source": slug, "target": target,
                        "type": "wikilink",
                        "key": f"wikilink_{slug}_{target}",
                        "weight": 1.0,
                    })

            # 인과: 엄격
            for m in CAUSAL_STRICT_RE.finditer(content):
                cause = resolve_target(m.group(1).strip().split("|")[0], alias_map)
                effect = resolve_target(m.group(3).strip().split("|")[0], alias_map)
                edges.append({
                    "source": cause, "target": effect,
                    "type": "causal", "relation": m.group(2).strip(),
                    "key": f"causal_{cause}_{effect}", "weight": 2.0,
                })

            # 인과: 유연 (좌측 위키링크 + 우측 텍스트)
            for m in CAUSAL_FLEX_RE.finditer(content):
                cause = resolve_target(m.group(1).strip().split("|")[0], alias_map)
                relation = m.group(2).strip()
                right = m.group(3).strip()
                right_links = WIKILINK_RE.findall(right)
                if right_links:
                    for rl in right_links:
                        effect = resolve_target(rl.strip(), alias_map)
                        key = f"causal_{cause}_{effect}"
                        if not any(e["key"] == key for e in edges):
                            edges.append({
                                "source": cause, "target": effect,
                                "type": "causal", "relation": relation,
                                "key": key, "weight": 2.0,
                            })
                else:
                    clean = re.sub(r"\s*\([^)]*\)\s*$", "", right).strip()
                    if clean and cause:
                        effect_slug = normalize_to_slug(clean)[:60]
                        if effect_slug:
                            effect = resolve_target(effect_slug, alias_map)
                            key = f"causal_{cause}_{effect}"
                            if not any(e["key"] == key for e in edges):
                                edges.append({
                                    "source": cause, "target": effect,
                                    "type": "causal", "relation": relation,
                                    "key": key, "weight": 2.0,
                                })

            # 인과: 역방향 (좌측 텍스트 + 우측 위키링크)
            for m in CAUSAL_REVERSE_RE.finditer(content):
                left = m.group(1).strip()
                relation = m.group(2).strip()
                effect = resolve_target(m.group(3).strip().split("|")[0], alias_map)
                left_links = WIKILINK_RE.findall(left)
                if left_links:
                    for ll in left_links:
                        cause = resolve_target(ll.strip(), alias_map)
                        key = f"causal_{cause}_{effect}"
                        if not any(e["key"] == key for e in edges):
                            edges.append({
                                "source": cause, "target": effect,
                                "type": "causal", "relation": relation,
                                "key": key, "weight": 2.0,
                            })

        return nodes, edges

    def _deduplicate(self, edges):
        seen = set()
        unique = []
        for e in edges:
            key = (e["source"], e["target"], e["type"])
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique

    def _add_unresolved(self):
        all_ids = set(self.nodes.keys())
        for e in self.links:
            for key in ("source", "target"):
                if e[key] not in all_ids:
                    self.nodes[e[key]] = {
                        "label": e[key], "file": None,
                        "category": "unresolved", "tags": "",
                    }
                    all_ids.add(e[key])
