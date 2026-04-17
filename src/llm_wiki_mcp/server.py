"""llm-wiki-mcp MCP 서버 — 지식 그래프 기반 위키 탐색 도구."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from fastmcp import FastMCP

import sys as _sys
from pathlib import Path as _Path
# 패키지 미설치 시 src/ 경로를 sys.path에 추가
_src = str(_Path(__file__).resolve().parent.parent)
if _src not in _sys.path:
    _sys.path.insert(0, _src)

try:
    from .graph import WikiGraph
except ImportError:
    from llm_wiki_mcp.graph import WikiGraph

# ── 전역 상태 ──────────────────────────────────────────────

_graph: WikiGraph | None = None
_wiki_dir: Path | None = None
_graph_path: Path | None = None

mcp = FastMCP(name="LLM Wiki MCP")


def _get_graph() -> WikiGraph:
    global _graph
    if _graph is None:
        _graph = WikiGraph(_wiki_dir)
        if _graph_path and _graph_path.exists():
            _graph.load(_graph_path)
        else:
            _graph.build()
            if _graph_path:
                _graph.save(_graph_path)
    return _graph


def _percentile_rank(value: int, all_values: list[int]) -> int:
    if not all_values:
        return 0
    count_below = sum(1 for v in all_values if v < value)
    return round(count_below / len(all_values) * 100)


def _relation_meaning(relation: str) -> str:
    meanings = {
        "가능하게 함": "존재/작동을 가능하게 하는 핵심 기반",
        "성능 향상": "성능을 높이는 보강 요인",
        "성능 저하": "성능을 낮추는 제약 요인",
        "기반이 됨": "이론적/기술적 토대",
        "발전시킴": "확장·발전의 원동력",
        "대체함": "기존 방식을 대체하는 새로운 접근",
        "포함함": "상위 개념으로서 포함하는 관계",
        "적용됨": "실제 적용되는 대상",
    }
    return meanings.get(relation, f"'{relation}' 관계")


# ── 도구: explain_node ─────────────────────────────────────

@mcp.tool
def explain_node(query: str) -> str:
    """노드의 상세 정보와 연결 관계를 조회한다.
    노드 이름이나 ID의 일부를 입력하면 해당 노드의 카테고리, 파일 경로,
    태그, 나가는/들어오는 엣지 목록을 반환한다.

    Args:
        query: 검색할 노드 이름 또는 ID (부분 매칭 지원)
    """
    g = _get_graph()
    nid = g.resolve(query)
    if not nid:
        return f"노드를 찾을 수 없습니다: {query}"

    node = g.nodes[nid]
    degree = g.degree_map()
    all_degrees = list(degree.values())
    node_degree = degree.get(nid, 0)
    pct = _percentile_rank(node_degree, all_degrees)

    outgoing = [l for l in g.links if l["source"] == nid]
    incoming = [l for l in g.links if l["target"] == nid]
    causal_out = [l for l in outgoing if l["type"] == "causal"]
    causal_in = [l for l in incoming if l["type"] == "causal"]

    cat_dist: dict[str, int] = {}
    for l in outgoing + incoming:
        other = l["target"] if l["source"] == nid else l["source"]
        cat = g.nodes.get(other, {}).get("category", "unresolved")
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
    top_cat = sorted(cat_dist.items(), key=lambda x: x[1], reverse=True)

    if node_degree == 0:
        role = "고립 노드 — 다른 개념과 연결이 없음"
    elif pct >= 80:
        role = f"핵심 허브 — 연결도 상위 {100 - pct}%, 위키의 중심 개념"
    elif pct >= 50:
        role = f"중간 연결자 — 연결도 상위 {100 - pct}%, 여러 개념을 잇는 역할"
    else:
        role = f"말단 노드 — 연결도 하위 {100 - pct}%, 특정 소스에 종속"

    if causal_out and causal_in:
        causal_role = f"매개자 — 원인 역할 {len(causal_out)}개, 결과 역할 {len(causal_in)}개"
    elif causal_out:
        causal_role = f"기반 기술 — 다른 {len(causal_out)}개 개념을 가능하게 함"
    elif causal_in:
        causal_role = f"파생 결과 — {len(causal_in)}개 개념으로부터 영향받음"
    else:
        causal_role = "인과 관계 미등록 — 인과 callout 추가 권장"

    lines = [
        f"## {node['label']}",
        f"카테고리: {node.get('category', '')} | 태그: {node.get('tags', '')}",
        f"파일: {node.get('file', 'N/A')}",
        "", "### 위치 분석",
        f"- 연결도: {node_degree} (상위 {100 - pct}%) → {role}",
        f"- 인과 역할: {causal_role}",
    ]
    if top_cat:
        lines.append(f"- 최다 연결 카테고리: {', '.join(f'{c}({n})' for c, n in top_cat[:3])}")

    if causal_out or causal_in:
        lines.append("\n### 인과 요약")
        for l in causal_out:
            tgt = g.nodes.get(l["target"], {}).get("label", l["target"])
            lines.append(f"- {node['label']} →({l.get('relation', '?')})→ {tgt}")
        for l in causal_in:
            src = g.nodes.get(l["source"], {}).get("label", l["source"])
            lines.append(f"- {src} →({l.get('relation', '?')})→ {node['label']}")

    lines.append("\n### 연결 맥락")
    for cat_name in ["concepts", "sources", "entities", "syntheses", "unresolved"]:
        cat_links = []
        for l in outgoing + incoming:
            other = l["target"] if l["source"] == nid else l["source"]
            other_node = g.nodes.get(other, {})
            if other_node.get("category", "unresolved") == cat_name:
                cat_links.append(other_node.get("label", other))
        if cat_links:
            unique = list(dict.fromkeys(cat_links))
            lines.append(f"- {cat_name}: {', '.join(unique[:8])}")

    suggestions = []
    unresolved_neighbors = [
        g.nodes.get(l["target"], {}).get("label", l["target"])
        for l in outgoing if g.nodes.get(l["target"], {}).get("category") == "unresolved"
    ]
    if unresolved_neighbors:
        suggestions.append(f"미해결 연결 {len(unresolved_neighbors)}개 → 페이지 생성 권장: {', '.join(unresolved_neighbors[:5])}")
    if not causal_out and not causal_in:
        suggestions.append("인과 관계 callout이 없음 → 관련 소스에서 인과 관계 추출 권장")
    if node_degree <= 2:
        suggestions.append("연결이 적음 → 관련 concepts/entities 페이지와 위키링크 추가 권장")
    if suggestions:
        lines.append("\n### 성장 제안")
        for s in suggestions:
            lines.append(f"- {s}")

    return "\n".join(lines)


# ── 도구: find_path ────────────────────────────────────────

@mcp.tool
def find_path(start: str, end: str) -> str:
    """두 노드 사이의 최단 경로를 BFS로 탐색한다.

    Args:
        start: 시작 노드 이름 또는 ID
        end: 도착 노드 이름 또는 ID
    """
    g = _get_graph()
    start_id = g.resolve(start)
    end_id = g.resolve(end)
    if not start_id:
        return f"시작 노드를 찾을 수 없습니다: {start}"
    if not end_id:
        return f"도착 노드를 찾을 수 없습니다: {end}"
    if start_id == end_id:
        return f"동일한 노드입니다: {g.nodes.get(start_id, {}).get('label', start_id)}"

    adj: dict[str, list[dict]] = {}
    for l in g.links:
        adj.setdefault(l["source"], []).append(l)
        adj.setdefault(l["target"], []).append(
            {**l, "source": l["target"], "target": l["source"], "_reverse": True}
        )

    visited = {start_id}
    queue = deque([(start_id, [(start_id, None)])])
    while queue:
        current, path = queue.popleft()
        if current == end_id:
            sl = g.nodes.get(start_id, {}).get("label", start_id)
            el = g.nodes.get(end_id, {}).get("label", end_id)
            hops = len(path) - 1
            if hops == 1: strength = "직접 연결 — 강한 관계"
            elif hops == 2: strength = "1단계 매개 — 중간 개념을 통해 연결"
            elif hops <= 4: strength = f"{hops-1}단계 매개 — 간접적 관계"
            else: strength = f"{hops-1}단계 매개 — 매우 먼 관계"
            has_causal = any(e and e.get("type") == "causal" for _, e in path)
            lines = [f"## {sl} ↔ {el}", f"거리: {hops}홉 | {strength}"]
            if has_causal:
                lines.append("⚡ 경로에 인과 관계 포함 — 논리적 연결이 존재")
            lines.append("\n### 경로")
            for nid, edge in path:
                n = g.nodes.get(nid, {})
                label = n.get("label", nid)
                cat = n.get("category", "")
                if edge:
                    rel = edge.get("relation", edge["type"])
                    d = "←" if edge.get("_reverse") else "→"
                    icon = "🔗" if edge["type"] == "wikilink" else "⚡"
                    lines.append(f"  {d} {icon} [{rel}] {label} ({cat})")
                else:
                    lines.append(f"  ● {label} ({cat})")
            intermediates = [g.nodes.get(nid, {}).get("label", nid) for nid, _ in path[1:-1]]
            if intermediates:
                lines.append(f"\n### 매개 노드 해석")
                lines.append(f"- {sl}과 {el}은 {'→'.join(intermediates)}을(를) 통해 연결됨")
            return "\n".join(lines)
        for link in adj.get(current, []):
            neighbor = link["target"]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [(neighbor, link)]))

    sl = g.nodes.get(start_id, {}).get("label", start_id)
    el = g.nodes.get(end_id, {}).get("label", end_id)
    return f"## {sl} ↔ {el}\n경로 없음 — 두 개념이 현재 위키에서 연결되지 않음"


# ── 도구: causal_chain ─────────────────────────────────────

@mcp.tool
def causal_chain(query: str) -> str:
    """특정 노드와 관련된 모든 인과 관계를 조회한다.

    Args:
        query: 검색할 노드 이름 또는 ID
    """
    g = _get_graph()
    nid = g.resolve(query)
    if not nid:
        return f"노드를 찾을 수 없습니다: {query}"

    causal = [l for l in g.links if l["type"] == "causal"]
    upstream = [l for l in causal if l["target"] == nid]
    downstream = [l for l in causal if l["source"] == nid]
    label = g.nodes.get(nid, {}).get("label", nid)

    if not upstream and not downstream:
        return (f"## {label}의 인과 관계\n\n인과 관계가 등록되지 않음.\n"
                f"→ 관련 소스에서 `> [!causal]` callout 추가 권장")

    lines = [f"## {label}의 인과 네트워크\n"]
    if upstream:
        lines.append("### 상류 (이 개념을 가능하게 한 것)")
        for l in upstream:
            src = g.nodes.get(l["source"], {}).get("label", l["source"])
            rel = l.get("relation", "?")
            lines.append(f"- {src} →({rel})→ {label}")
            lines.append(f"  해석: {src}이(가) {label}의 {_relation_meaning(rel)}")
    else:
        lines.append("### 상류\n- 없음 → 기반 개념 추적으로 인과 체인 확장 가능")

    lines.append("")
    if downstream:
        lines.append("### 하류 (이 개념이 영향을 준 것)")
        for l in downstream:
            tgt = g.nodes.get(l["target"], {}).get("label", l["target"])
            rel = l.get("relation", "?")
            lines.append(f"- {label} →({rel})→ {tgt}")
            lines.append(f"  해석: {label}이(가) {tgt}의 {_relation_meaning(rel)}")
    else:
        lines.append("### 하류\n- 없음 → 후속 개념/기술 추적으로 확장 가능")

    total = len(upstream) + len(downstream)
    lines.append(f"\n### 인과 깊이: {total}단계")
    if total <= 2: lines.append("- 얕은 인과 체인 → 상류/하류 확장 권장")
    elif total <= 5: lines.append("- 적절한 인과 체인 → 핵심 논리 흐름 형성됨")
    else: lines.append("- 풍부한 인과 체인 → 논리 추론 가능")
    return "\n".join(lines)


# ── 도구: graph_summary ────────────────────────────────────

@mcp.tool
def graph_summary() -> str:
    """현재 위키 그래프의 전체 요약 통계를 반환한다."""
    g = _get_graph()
    degree = g.degree_map()
    categories: dict[str, int] = {}
    for n in g.nodes.values():
        cat = n.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    hubs = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:5]
    unresolved = sorted(
        [(nid, degree.get(nid, 0)) for nid, n in g.nodes.items() if n.get("category") == "unresolved"],
        key=lambda x: x[1], reverse=True,
    )
    causal_count = sum(1 for l in g.links if l["type"] == "causal")
    total_nodes = len(g.nodes)
    total_edges = len(g.links)
    resolved = total_nodes - len(unresolved)
    density = round(total_edges / total_nodes, 2) if total_nodes else 0
    causal_ratio = round(causal_count / total_edges * 100, 1) if total_edges else 0
    unresolved_ratio = round(len(unresolved) / total_nodes * 100, 1) if total_nodes else 0

    if resolved < 20: scale = "초기 단계"
    elif resolved < 50: scale = "성장 단계"
    elif resolved < 100: scale = "확장 단계"
    else: scale = "성숙 단계"

    lines = [
        "## 위키 건강 리포트\n",
        f"### 규모: {scale}",
        f"- 실제 페이지: {resolved}개 (concepts {categories.get('concepts',0)}, sources {categories.get('sources',0)}, entities {categories.get('entities',0)}, syntheses {categories.get('syntheses',0)})",
        f"- 총 엣지: {total_edges} (밀도: {density} 엣지/노드)",
    ]
    if density >= 4.0: lines.append("  → 높은 밀도")
    elif density >= 2.5: lines.append("  → 양호한 밀도")
    else: lines.append("  → 낮은 밀도 — 위키링크 추가 권장")

    lines.append("\n### 강점")
    if hubs:
        hub_strs = [f"{g.nodes[nid].get('label', nid)}({deg})" for nid, deg in hubs[:3]]
        lines.append(f"- 허브: {', '.join(hub_strs)}")
    if causal_count:
        lines.append(f"- 인과 관계 {causal_count}개 존재")

    lines.append("\n### 약점")
    if unresolved_ratio > 25:
        lines.append(f"- 미해결 노드 {len(unresolved)}개 ({unresolved_ratio}%)")
    if causal_ratio < 5:
        lines.append(f"- 인과 비율 {causal_ratio}% — callout 추가 권장")
    if not categories.get("syntheses"):
        lines.append("- syntheses/ 0개 — 질의 결과 저장 시작 권장")

    lines.append("\n### 다음 행동")
    if unresolved:
        lines.append(f"1. 미해결 페이지 생성: {', '.join(f'{nid}({d})' for nid,d in unresolved[:5])}")
    return "\n".join(lines)


# ── 도구: rebuild_graph ────────────────────────────────────

@mcp.tool
def rebuild_graph() -> str:
    """위키 마크다운을 파싱하여 그래프를 재빌드한다."""
    global _graph
    _graph = WikiGraph(_wiki_dir)
    _graph.build()
    if _graph_path:
        meta = _graph.save(_graph_path)
        return (f"그래프 재빌드 완료: {meta['total_nodes']}노드, "
                f"{meta['total_edges']}엣지 (위키링크 {meta['wikilink_edges']}, "
                f"인과 {meta['causal_edges']})")
    return "그래프 재빌드 완료 (메모리만, 파일 저장 안 함)"


# ── init 명령 ──────────────────────────────────────────────

INIT_STRUCTURE = {
    "raw/papers/.gitkeep": "",
    "raw/articles/.gitkeep": "",
    "raw/transcripts/.gitkeep": "",
    "raw/notes/.gitkeep": "",
    "raw/code/.gitkeep": "",
    "raw/assets/.gitkeep": "",
    "wiki/sources/.gitkeep": "",
    "wiki/concepts/.gitkeep": "",
    "wiki/entities/.gitkeep": "",
    "wiki/syntheses/.gitkeep": "",
    "wiki/index.md": """---
title: 위키 인덱스
created: {date}
updated: {date}
---

# 위키 인덱스

AI가 생성·유지하는 세컨드 브레인 위키.

## Sources (원본 요약)

## Concepts (개념)

## Entities (인물·조직·모델·도구)

## Syntheses (종합·분석)
""",
    "wiki/log.md": """---
title: 위키 로그
created: {date}
updated: {date}
---

# 위키 로그

시간순 작업 기록. Append-only.

## [{date}] init | 위키 초기화

- llm-wiki-mcp init으로 생성됨
""",
    "output/.gitkeep": "",
}


def init_vault(vault_path: str) -> None:
    """새 LLM Wiki 볼트를 초기화한다."""
    from datetime import date as d
    vault = Path(vault_path).resolve()
    today = d.today().isoformat()

    if vault.exists() and any(vault.iterdir()):
        print(f"⚠ {vault} 가 비어있지 않습니다. 기존 파일은 유지됩니다.")

    created = 0
    for rel_path, content in INIT_STRUCTURE.items():
        full = vault / rel_path
        if not full.exists():
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content.format(date=today), encoding="utf-8")
            created += 1

    print(f"✅ LLM Wiki 볼트 초기화 완료: {vault}")
    print(f"   생성된 파일: {created}개")
    print(f"\n다음 단계:")
    print(f"  1. raw/ 폴더에 논문, 기사, 메모를 넣으세요")
    print(f"  2. MCP 클라이언트에 등록:")
    print(f'     "wiki": {{"command": "uvx", "args": ["llm-wiki-mcp", "--vault", "{vault}"]}}')


# ── 엔트리포인트 ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM Wiki MCP Server")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "init"])
    parser.add_argument("--vault", type=str, help="위키 볼트 경로")
    parser.add_argument("--graph", type=str, help="graph.json 저장 경로")
    parser.add_argument("path", nargs="?", help="init 시 볼트 경로")
    args = parser.parse_args()

    if args.command == "init":
        vault = args.path or args.vault or "."
        init_vault(vault)
        return

    global _wiki_dir, _graph_path
    _wiki_dir = Path(args.vault) if args.vault else Path("wiki")
    _graph_path = Path(args.graph) if args.graph else _wiki_dir.parent / "graph.json"
    mcp.run()


if __name__ == "__main__":
    main()
