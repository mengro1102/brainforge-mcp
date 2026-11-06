#!/usr/bin/env python3
"""graph_query.py: pick which wiki pages to read, by walking the link graph.

Plain keyword search has two blind spots:
  1. No shared words, no hit. Ask "what does PPO assume?" and you get ppo.md,
     but miss actor-critic, value-function and policy-gradient, which are the
     pages the answer actually needs.
  2. No relations. "How are A and B connected?" has no structure to answer from.

This script outputs a ranked *reading list* (paths + why each was picked),
never page bodies. The agent then reads only the top 2-4 files. In production
this cut prompt size that used to balloon to 14-16k tokens from dumping whole
search results, which matters for speed and for free-tier quotas.

Usage:
    python graph_query.py search  "what does PPO assume"   --wiki ./wiki
    python graph_query.py neighbors ppo --hops 2           --wiki ./wiki
    python graph_query.py path ppo transformer             --wiki ./wiki
    python graph_query.py hubs --top 15                    --wiki ./wiki
    python graph_query.py stats                            --wiki ./wiki
Add --json to any command for machine-readable output. `search` falls back to
page bodies when no slug/title/tag/alias matches (or use --fulltext).
The wiki path can also come from the WIKI_DIR environment variable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_graph  # noqa: E402

HOP_DECAY = 0.55
# Body wikilinks are slightly weaker evidence than curated `related:` entries;
# provenance (`sources:`) links are weaker still.
EDGE_WEIGHT = {"related": 1.0, "wikilink": 0.8, "source": 0.5}
WORD = r"0-9A-Za-z\uac00-\ud7a3"  # latin + hangul; extend for other scripts


def tokenize(text: str) -> list[str]:
    """Lower-cased tokens. Also splits glued chunks such as Korean particles
    ('ppo와' -> 'ppo'), otherwise the token never matches the slug 'ppo'."""
    raw = [t for t in re.split(rf"[^{WORD}]+", text.lower()) if len(t) > 1]
    out: list[str] = []
    for t in raw:
        out.append(t)
        for part in re.findall(r"[0-9a-z]{2,}", t) + re.findall(r"[\uac00-\ud7a3]{2,}", t):
            if part != t:
                out.append(part)
    return out


def seed_scores(g: dict, query: str, k: int = 8) -> dict[str, float]:
    """Match query tokens against slug / title / tags / aliases (not bodies).

    No raw substring matching: "ppo" once matched `a-su(ppo)rting-...`.
    Slugs are compared per hyphen part, titles on word boundaries.
    """
    toks = tokenize(query)
    out: dict[str, float] = {}
    for slug, m in g["nodes"].items():
        parts = slug.lower().split("-")
        title = m["title"].lower()
        tags = {t.lower() for t in m["tags"]}
        aliases = {a.lower() for a in m["aliases"]}
        score = 0.0
        for t in toks:
            if t == slug.lower():
                score += 4.0  # the page *named* the query beats pages that merely mention it
            elif t in parts:
                score += 2.0
            if re.search(rf"(?<![{WORD}]){re.escape(t)}(?![{WORD}])", title):
                score += 1.5
            if t in tags:
                score += 1.5
            if t in aliases:
                score += 2.0
        if score:
            out[slug] = score
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:k])


def fulltext_seeds(g: dict, query: str, k: int = 6) -> dict[str, float]:
    """Fallback when no slug/title/tag matches: score pages by how many distinct
    query terms their body contains (word boundaries). Weaker than a title hit."""
    terms = [t for t in dict.fromkeys(tokenize(query)) if len(t) > 2]
    if not terms:
        return {}
    root = Path(g["root"])
    out: dict[str, float] = {}
    for slug, m in g["nodes"].items():
        try:
            body = wiki_graph.split_frontmatter((root / m["path"]).read_text(encoding="utf-8", errors="replace"))[1].lower()
        except OSError:
            continue
        hits = sum(1 for t in terms if re.search(rf"(?<![{WORD}]){re.escape(t)}", body))
        if hits:
            out[slug] = round(hits / len(terms) * 1.5, 3)
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))[:k])


def neighbors(adj: dict, slug: str, hops: int) -> dict[str, tuple[int, str]]:
    seen: dict[str, tuple[int, str]] = {}
    q = deque([(slug, 0)])
    while q:
        cur, depth = q.popleft()
        if depth >= hops:
            continue
        for nb, kind in adj.get(cur, []):
            if nb == slug or nb in seen:
                continue
            seen[nb] = (depth + 1, kind)
            q.append((nb, depth + 1))
    return seen


def shortest_path(adj: dict, a: str, b: str) -> list[tuple[str, str]]:
    if a == b:
        return [(a, "")]
    prev: dict[str, tuple[str, str]] = {a: ("", "")}
    q = deque([a])
    while q:
        cur = q.popleft()
        for nb, kind in adj.get(cur, []):
            if nb in prev:
                continue
            prev[nb] = (cur, kind)
            if nb == b:
                path, node = [], b
                while node:
                    p, k = prev[node]
                    path.append((node, k))
                    node = p
                return path[::-1]
            q.append(nb)
    return []


def resolve(g: dict, name: str) -> str | None:
    """Agents pass "GRPO" or "Transformer"; slugs are lower-case. Accept case,
    spaces, titles and aliases, or the agent wrongly reports "no connection"."""
    nodes = g["nodes"]
    if name in nodes:
        return name
    low = name.strip().lower()
    if low.replace(" ", "-") in nodes:
        return low.replace(" ", "-")
    for slug, m in nodes.items():
        if m["title"].strip().lower() == low or low in (a.lower() for a in m["aliases"]):
            return slug
    return None


def suggest(g: dict, name: str, k: int = 5) -> list[str]:
    low = name.strip().lower()
    return [s for s in g["nodes"] if low in s or s in low][:k]


def not_found(g: dict, name: str) -> dict:
    return {"error": f"no page named '{name}'", "did_you_mean": suggest(g, name)}


# ── commands ─────────────────────────────────────────────────────────────

def cmd_search(g: dict, a) -> dict:
    adj = wiki_graph.adjacency(g)
    seeds = {} if a.fulltext else seed_scores(g, a.query)
    why0 = "direct match on slug/title/tag"
    if not seeds:
        seeds = fulltext_seeds(g, a.query)
        why0 = "full-text match in page body"
    if not seeds:
        return {"error": f"no page matched '{a.query}'",
                "hint": "nothing in slugs, titles, tags, aliases or bodies; try other words or a synonym"}
    scored = {s: {"score": v, "hop": 0, "why": why0} for s, v in seeds.items()}
    for s, base in seeds.items():
        title = g["nodes"][s]["title"]
        for nb, (depth, kind) in neighbors(adj, s, a.hops).items():
            gain = base * (HOP_DECAY ** depth) * EDGE_WEIGHT.get(kind, 0.8)
            if nb not in scored or gain > scored[nb]["score"]:
                scored[nb] = {"score": round(gain, 3), "hop": depth,
                              "why": f"{depth} hop(s) from '{title}' via {kind}"}
    ranked = sorted(scored.items(), key=lambda kv: -kv[1]["score"])[:a.limit]
    return {
        "query": a.query,
        "seeds": len(seeds), "expanded": len(scored),
        "results": [{"path": g["nodes"][s]["path"], "title": g["nodes"][s]["title"], **info}
                    for s, info in ranked],
        "next": "Read the top 2-4 paths. Do not read all of them.",
    }


def cmd_neighbors(g: dict, a) -> dict:
    slug = resolve(g, a.slug)
    if slug is None:
        return not_found(g, a.slug)
    nb = neighbors(wiki_graph.adjacency(g), slug, a.hops)
    return {"node": slug, "hops": a.hops, "neighbors": [
        {"hop": d, "via": k, "path": g["nodes"][s]["path"], "title": g["nodes"][s]["title"]}
        for s, (d, k) in sorted(nb.items(), key=lambda kv: (kv[1][0], kv[0]))]}


def cmd_path(g: dict, a) -> dict:
    src, dst = resolve(g, a.a), resolve(g, a.b)
    if src is None:
        return not_found(g, a.a)
    if dst is None:
        return not_found(g, a.b)
    p = shortest_path(wiki_graph.adjacency(g), src, dst)
    if not p:
        return {"error": f"no connection between {src} and {dst}",
                "hint": "one of them may be isolated; see `stats`"}
    return {"from": src, "to": dst, "hops": len(p) - 1, "path": [
        {"via": k, "path": g["nodes"][s]["path"], "title": g["nodes"][s]["title"]} for s, k in p]}


def cmd_hubs(g: dict, a) -> dict:
    deg = wiki_graph.degrees(g)
    top = sorted(deg.items(), key=lambda kv: -kv[1])[:a.top]
    return {"hubs": [{"degree": c, "path": g["nodes"][s]["path"], "title": g["nodes"][s]["title"]}
                     for s, c in top if c]}


def cmd_stats(g: dict, a) -> dict:
    deg = wiki_graph.degrees(g)
    kinds: dict[str, int] = {}
    types: dict[str, int] = {}
    for e in g["edges"]:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    for m in g["nodes"].values():
        types[m["type"]] = types.get(m["type"], 0) + 1
    iso = sorted(k for k, v in deg.items() if v == 0)
    n = max(len(g["nodes"]), 1)
    return {
        "nodes": len(g["nodes"]), "edges": len(g["edges"]), "dangling": len(g["dangling"]),
        "edge_types": kinds, "node_types": types,
        "isolated": len(iso), "isolated_pct": round(100 * len(iso) / n, 1),
        "isolated_sample": [g["nodes"][k]["path"] for k in iso[:10]],
        "hint": ("isolated pages are invisible to graph search; add `related:` to their frontmatter"
                 if iso else ""),
    }


def render(cmd: str, res: dict) -> str:
    if "error" in res:
        extra = res.get("did_you_mean") or res.get("hint") or ""
        return f"{res['error']}" + (f"\n  {extra}" if extra else "")
    if cmd == "search":
        lines = [f"query: {res['query']}  (seeds {res['seeds']} -> expanded {res['expanded']})", ""]
        for r in res["results"]:
            lines.append(f"{r['score']:>6.2f}  hop {r['hop']}  {r['path']:<45} {r['title']}")
            lines.append(f"{'':>16}└ {r['why']}")
        lines += ["", f"-> {res['next']}"]
        return "\n".join(lines)
    if cmd == "neighbors":
        lines = [f"{res['node']}: {len(res['neighbors'])} neighbor(s) within {res['hops']} hop(s)"]
        lines += [f"  hop {n['hop']} {n['via']:<9} {n['path']:<45} {n['title']}" for n in res["neighbors"]]
        return "\n".join(lines)
    if cmd == "path":
        lines = [f"{res['from']} -> {res['to']} ({res['hops']} hop(s))"]
        for i, n in enumerate(res["path"]):
            lines.append(("" if i == 0 else f"  --{n['via']}--> ") + f"{n['path']}  {n['title']}")
        return "\n".join(lines)
    if cmd == "hubs":
        return "\n".join(f"  {h['degree']:>3}  {h['path']:<45} {h['title']}" for h in res["hubs"])
    return json.dumps(res, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Graph-guided reading list for a markdown wiki")
    ap.add_argument("--wiki", default=os.environ.get("WIKI_DIR", "."), help="wiki root (or $WIKI_DIR)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--hops", type=int, default=1)
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--fulltext", action="store_true", help="seed from page bodies instead of titles")
    n = sub.add_parser("neighbors")
    n.add_argument("slug")
    n.add_argument("--hops", type=int, default=1)
    p = sub.add_parser("path")
    p.add_argument("a")
    p.add_argument("b")
    h = sub.add_parser("hubs")
    h.add_argument("--top", type=int, default=15)
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = Path(a.wiki).expanduser()
    if not root.is_dir():
        print(f"wiki directory not found: {root}", file=sys.stderr)
        return 2
    g = wiki_graph.build(root)
    res = {"search": cmd_search, "neighbors": cmd_neighbors, "path": cmd_path,
           "hubs": cmd_hubs, "stats": cmd_stats}[a.cmd](g, a)
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else render(a.cmd, res))
    return 1 if "error" in res else 0


if __name__ == "__main__":
    raise SystemExit(main())
