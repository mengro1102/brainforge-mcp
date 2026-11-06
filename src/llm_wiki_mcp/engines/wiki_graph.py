#!/usr/bin/env python3
"""wiki_graph.py: build a link graph from a markdown wiki (Obsidian vault).

Nodes are markdown pages. Edges come from two places:
  * `related: [slug-a, slug-b]` in the frontmatter  (curated, type "related")
  * `[[wikilinks]]` in the body                     (type "wikilink")
  * `sources:` entries that are .md paths in the wiki  (provenance, type "source")

Standard library only. If PyYAML is installed it is used to parse frontmatter;
otherwise a small parser handles the scalar / inline-list / block-list forms.

This file is shared by several skills (graph search, link review). The copy in
each skill's scripts/ directory is identical. Edit shared/wiki_graph.py and run
`python scripts/sync_shared.py` instead of editing the copies.

Scope: an optional `wiki-graph.json` in the wiki root,
    {"include": ["concepts", "entities", "references"], "exclude": ["raw/projects"]}
limits which top-level folders / path prefixes are scanned. Default: everything
except SKIP_DIRS.

CLI:
    python wiki_graph.py WIKI_DIR [--out graph.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "_archive", "reports", "templates"}
# On a file-name collision (raw/ppo.md vs concepts/ppo.md) the curated page keeps
# the plain slug; the other is keyed by its path. Lower rank wins.
CURATED_TOPS = {"concepts", "comparisons", "entities", "references", "queries",
                "syntheses", "sources", "brainstorm", "notes"}
SKIP_FILES = {"index.md", "log.md", "readme.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
DIR_TYPES = {
    "concepts": "concept", "comparisons": "comparison", "entities": "entity",
    "references": "reference", "queries": "query", "syntheses": "synthesis",
    "sources": "source", "papers": "paper", "articles": "article",
}


def split_frontmatter(text: str) -> tuple[str, str]:
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    nl = text.find("\n", end + 1)
    return text[3:end].strip("\n"), (text[nl + 1:] if nl != -1 else "")


def _parse_scalar(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
    return v.strip("'\"")


def parse_frontmatter(fm: str) -> dict:
    if not fm:
        return {}
    if yaml is not None:
        try:
            data = yaml.safe_load(fm)
            return data if isinstance(data, dict) else {}
        except yaml.YAMLError:
            pass  # fall back to the tolerant parser below
    out: dict = {}
    key = None
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2)
            out[key] = _parse_scalar(val) if val.strip() else []
        elif key and re.match(r"^\s+-\s+", line) and isinstance(out.get(key), list):
            item = re.sub(r"^\s+-\s+", "", line).strip().strip("'\"")
            pm = re.match(r"^path:\s*(.+)$", item)
            if pm:  # `- path: x.md` inside a list of mappings, as PyYAML would give
                out[key].append({"path": pm.group(1).strip().strip("'\"")})
            elif ":" not in item:
                out[key].append(item)
    return out


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict):
                x = x.get("path") or x.get("title") or ""
            out.append(str(x).strip())
        return [x for x in out if x]
    return [str(v)]


def _slug_of(target: str) -> str:
    t = target.strip().split("/")[-1]
    return t[:-3] if t.endswith(".md") else t


def load_scope(root: Path) -> tuple[list[str], list[str]]:
    cfg = root / "wiki-graph.json"
    if not cfg.exists():
        return [], []
    data = json.loads(cfg.read_text(encoding="utf-8"))
    norm = lambda xs: [x.strip("/").replace("\\", "/") for x in xs or []]  # noqa: E731
    return norm(data.get("include")), norm(data.get("exclude"))


def iter_pages(root: Path):
    include, exclude = load_scope(root)
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        posix = rel.as_posix()
        if any(part in SKIP_DIRS or part.startswith(".") or part.lower().lstrip("_").startswith("backup")
               for part in rel.parts[:-1]):
            continue  # hidden, tooling and backup folders (_backup/, _backup_pdf/, backup/)
        if p.name.lower() in SKIP_FILES:
            continue
        if include and not any(posix == i or posix.startswith(i + "/") for i in include):
            continue
        if any(posix == e or posix.startswith(e + "/") for e in exclude):
            continue
        yield p, rel


def _rank(rel: Path) -> int:
    return 0 if len(rel.parts) > 1 and rel.parts[0] in CURATED_TOPS else 1


def build(root: str | Path) -> dict:
    """Return {"nodes": {slug: meta}, "edges": [...], "dangling": [...]}."""
    root = Path(root).expanduser().resolve()
    nodes: dict[str, dict] = {}
    bodies: dict[str, str] = {}
    ranks: dict[str, int] = {}
    for p, rel in iter_pages(root):
        fm, body = split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        meta = parse_frontmatter(fm)
        slug = p.stem
        if slug in nodes:
            # Collision: the better-ranked page keeps the plain slug.
            if _rank(rel) < ranks[slug]:
                loser = nodes.pop(slug)
                lslug = loser["path"][:-3]
                nodes[lslug], bodies[lslug], ranks[lslug] = loser, bodies.pop(slug), ranks.pop(slug)
            else:
                slug = rel.as_posix()[:-3]
        parent = rel.parts[-2] if len(rel.parts) > 1 else ""
        ftype = str(meta.get("type") or DIR_TYPES.get(parent) or "page")
        nodes[slug] = {
            "path": rel.as_posix(),
            "title": str(meta.get("title") or slug),
            "type": ftype,
            "tags": _as_list(meta.get("tags")),
            "aliases": _as_list(meta.get("aliases")),
            "related": [_slug_of(x) for x in _as_list(meta.get("related"))],
            "sources": [x for x in _as_list(meta.get("sources")) if x.endswith((".md", ".pdf"))],
            "confidence": str(meta.get("confidence") or ""),
            "description": str(meta.get("description") or ""),
        }
        bodies[slug] = body
        ranks[slug] = _rank(rel)

    alias: dict[str, str] = {}
    for slug, m in nodes.items():
        for a in [m["title"], *m["aliases"]]:
            alias.setdefault(a.strip().lower(), slug)

    def resolve(target: str) -> str | None:
        s = _slug_of(target)
        if s in nodes:
            return s
        return alias.get(s.lower()) or alias.get(s.replace("-", " ").lower())

    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    dangling: set[str] = set()
    for slug, m in nodes.items():
        links = [(t, "related") for t in m["related"]]
        links += [(t, "source") for t in m["sources"] if t.endswith(".md")]
        links += [(t.group(1), "wikilink") for t in WIKILINK_RE.finditer(CODE_RE.sub("", bodies[slug]))]
        for target, kind in links:
            dst = resolve(target)
            if dst is None:
                dangling.add(_slug_of(target))
                continue
            if dst == slug or (slug, dst, kind) in seen:
                continue
            seen.add((slug, dst, kind))
            edges.append({"src": slug, "dst": dst, "type": kind})
    return {"root": str(root), "nodes": nodes, "edges": edges, "dangling": sorted(dangling)}


def adjacency(g: dict) -> dict[str, list[tuple[str, str]]]:
    """Undirected adjacency: related links are mutual, backlinks are useful."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for e in g["edges"]:
        adj.setdefault(e["src"], []).append((e["dst"], e["type"]))
        adj.setdefault(e["dst"], []).append((e["src"], e["type"]))
    return adj


def linked_to(g: dict, types: set[str]) -> set[str]:
    """Nodes with at least one edge (either direction) to a node of the given types."""
    nodes = g["nodes"]
    out: set[str] = set()
    for e in g["edges"]:
        if nodes[e["dst"]]["type"] in types:
            out.add(e["src"])
        if nodes[e["src"]]["type"] in types:
            out.add(e["dst"])
    return out


def degrees(g: dict) -> dict[str, int]:
    deg = {k: 0 for k in g["nodes"]}
    for e in g["edges"]:
        deg[e["src"]] = deg.get(e["src"], 0) + 1
        deg[e["dst"]] = deg.get(e["dst"], 0) + 1
    return deg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a link graph from a markdown wiki")
    ap.add_argument("wiki", nargs="?", default=".")
    ap.add_argument("--out", help="write graph JSON here (default: print stats)")
    a = ap.parse_args(argv)
    g = build(a.wiki)
    if a.out:
        Path(a.out).write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"nodes {len(g['nodes'])} · edges {len(g['edges'])} · dangling {len(g['dangling'])}",
          file=sys.stderr if not a.out else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
