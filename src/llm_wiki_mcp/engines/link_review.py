#!/usr/bin/env python3
"""link_review.py: read isolated wiki pages and link them to concepts, with a human approval gate.

Why this does not auto-link
---------------------------
Linking 252 isolated papers mechanically, by "the abstract contains the word",
went badly: an object-detection paper got linked to `attention` because its
abstract said "has attracted lots of attention". Edges like that pull the wrong
pages into every grounded answer, and that does more harm than having no edge.

So the work is split in three, and a person stands between steps 2 and 3:

    1. queue    show what to read next: title, abstract, candidate concepts
    2. propose  the agent reads it and records "this page belongs to A, B because ..."
       skip     the agent read it and found nothing to link; that is recorded too
    3. apply    write `related:` into the page's frontmatter, ONLY after approval

Nothing is read twice: proposals, skips and drops live in one ledger, and
`queue` skips everything in it. Before the ledger existed the same paper was
read and rejected on three consecutive days.

Usage
-----
    python link_review.py queue   --n 3
    python link_review.py propose <slug> --related a,b --why "one line"
    python link_review.py skip    <slug> --why "one line"
    python link_review.py pending
    python link_review.py apply   <slug>          # only after the user approved
    python link_review.py drop    <slug>          # user said no; never re-proposed

Options: --wiki DIR (or $WIKI_DIR), --ledger FILE (default <wiki>/reports/link-proposals.json),
--types reference,paper (which isolated page types to queue), --targets concept,comparison
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_graph  # noqa: E402

FM_RE = re.compile(r"^(\ufeff?---\s*\n)(.*?)(\n---\s*\n)", re.S)
ABSTRACT_HEADINGS = ("Abstract", "Summary", "초록", "요약")
KEY_HEADINGS = ("Key points", "Kernel", "Core", "핵심")


class Review:
    def __init__(self, wiki: Path, ledger: Path, types: list[str], targets: list[str]):
        self.wiki = wiki
        self.ledger = ledger
        self.types = set(types)
        self.targets = set(targets)
        self.g = wiki_graph.build(wiki)
        self._patterns = self._compile_candidates()

    # ── ledger ───────────────────────────────────────────────────────
    def load(self) -> dict:
        try:
            return json.loads(self.ledger.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:
            sys.exit(f"{self.ledger} is not valid JSON. Stopping without touching it.")

    def save(self, d: dict) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── helpers ──────────────────────────────────────────────────────
    def concept_nodes(self) -> dict:
        return {s: m for s, m in self.g["nodes"].items() if m["type"] in self.targets}

    def _compile_candidates(self) -> list[tuple[str, list]]:
        out = []
        for slug, m in self.concept_nodes().items():
            pats = []
            for raw in (slug.split("/")[-1], m["title"], *m["aliases"]):
                term = raw.lower().replace("-", " ").strip()
                if not term or (term.isascii() and len(term) < 2):
                    continue
                if term.isascii():
                    pat = re.escape(term).replace(r"\ ", r"[\s\-]+")
                    pats.append(re.compile(rf"\b{pat}(?:e?s)?\b"))
                elif len(term) >= 6:
                    pats.append(term)
            out.append((slug, pats))
        return out

    def candidates(self, text: str) -> list[str]:
        """Concepts whose name appears in the text. Candidates only; the reader decides.

        Latin names match on word boundaries (so short names like `rl`, `llm`,
        `moe` still work without matching inside "world"), with optional plural
        ("LLM agents" -> `llm-agent`). Non-latin names need 6+ chars and match
        as substrings, since those scripts have no word boundaries.
        """
        low = text.lower()
        out = set()
        for slug, pats in self._patterns:
            if any((p in low) if isinstance(p, str) else p.search(low) for p in pats):
                out.add(slug)
        return sorted(out)

    @staticmethod
    def section(text: str, names: tuple[str, ...]) -> str:
        for name in names:
            m = re.search(rf"^##+\s*{re.escape(name)}\s*\n(.*?)(?=\n##+ |\Z)", text, re.S | re.M | re.I)
            if m:
                return " ".join(m.group(1).split())
        return ""

    def node(self, slug: str) -> dict | None:
        return self.g["nodes"].get(slug)

    # ── commands ─────────────────────────────────────────────────────
    def queue(self, n: int) -> int:
        # "Unlinked" = no edge to any concept-type page. A link to its own
        # full text or to another paper does not put a page into the graph.
        linked = wiki_graph.linked_to(self.g, self.targets)
        done = set(self.load())
        rows = []
        for slug, m in self.g["nodes"].items():
            if slug in linked or m["type"] not in self.types or slug in done:
                continue
            text = (self.wiki / m["path"]).read_text(encoding="utf-8", errors="replace")
            cands = self.candidates(wiki_graph.split_frontmatter(text)[1] + " " + m["title"])
            rows.append((len(cands), slug, m, text, cands))
        # Pages with candidates first, but pages without any are still queued:
        # filtering them out once left 35 of 52 isolated papers unread forever.
        rows.sort(key=lambda r: (-r[0], r[1]))
        if not rows:
            print("Nothing to read: no unlinked pages of type "
                  f"{sorted(self.types)}, or every one is already in the ledger.")
            return 0
        with_c = sum(1 for r in rows if r[0])
        print(f"{len(rows)} page(s) not linked to any concept; showing {min(n, len(rows))}. "
              f"({with_c} with candidate concepts, {len(rows) - with_c} without)\n")
        for _, slug, m, text, cands in rows[:n]:
            body = wiki_graph.split_frontmatter(text)[1]
            abstract = self.section(body, ABSTRACT_HEADINGS) or " ".join(
                ln for ln in body.splitlines() if not ln.lstrip().startswith("#")).strip()[:1400]
            abstract = " ".join(abstract.split())
            core = self.section(body, KEY_HEADINGS)
            print("=" * 66)
            print(f"slug  : {slug}\ntitle : {m['title']}\npath  : {m['path']}")
            print(f"\n[abstract]\n{abstract[:1400] or '(none)'}")
            if core:
                print(f"\n[key points]\n{core[:900]}")
            if cands:
                print(f"\n[candidate concepts] {', '.join(cands)}")
                print("  These names merely appear in the text. Pick only what the page is really about.")
            else:
                print("\n[candidate concepts] none: no concept name appears in the text.")
                print("  That does not mean nothing fits. Choose from the list below, or skip.")
                names = sorted(s for s, mm in self.concept_nodes().items() if mm["type"] != "entity")
                for i in range(0, len(names), 6):
                    print("    " + "  ".join(names[i:i + 6]))
            print(f"\n  propose: python link_review.py propose {slug} --related <a,b> --why \"<one line>\"")
            print(f"  skip   : python link_review.py skip {slug} --why \"<one line>\"\n")
        return 0

    def _entry(self, slug: str, related: list[str], why: str, status: str) -> dict:
        m = self.g["nodes"][slug]
        return {"title": m["title"], "path": m["path"], "related": related,
                "why": " ".join(why.split())[:400], "proposed_at": date.today().isoformat(),
                "status": status}

    def propose(self, slug: str, related: str, why: str) -> int:
        if not self.node(slug):
            print(f"No such page: {slug}")
            return 1
        picks = [x.strip() for x in related.split(",") if x.strip()]
        unknown = [p for p in picks if p not in self.g["nodes"]]
        if not picks or unknown:
            print(f"Unknown or empty --related: {unknown or '(empty)'}. Use exact page slugs.")
            return 1
        if not why.strip():
            print("--why is required: it is what the user will judge the proposal on.")
            return 1
        props = self.load()
        if props.get(slug, {}).get("status") in ("applied", "dropped"):
            print(f"Already {props[slug]['status']}; not re-proposing: {slug}")
            return 1
        props[slug] = self._entry(slug, picks, why, "pending")
        self.save(props)
        print(f"Proposed: {props[slug]['title']}\n  link to: {', '.join(picks)}\n  reason : {props[slug]['why']}")
        print("\n  NOT written to the wiki yet. Show this to the user and run `apply` only after they approve.")
        return 0

    def skip(self, slug: str, why: str) -> int:
        if not self.node(slug):
            print(f"No such page: {slug}")
            return 1
        if not why.strip():
            print("--why is required.")
            return 1
        props = self.load()
        if props.get(slug, {}).get("status") == "applied":
            print(f"Already applied; leaving it alone: {slug}")
            return 1
        props[slug] = self._entry(slug, [], why, "skipped")
        self.save(props)
        print(f"Recorded as skipped: {props[slug]['title']}\n  reason: {props[slug]['why']}")
        print("  It will not be queued again. The wiki was not modified.")
        return 0

    def pending(self) -> int:
        waiting = {k: v for k, v in self.load().items() if v.get("status") == "pending"}
        if not waiting:
            print("No proposals waiting for approval.")
            return 0
        print(f"{len(waiting)} proposal(s) waiting for approval\n")
        for slug, v in waiting.items():
            print(f"  - {v['title'][:70]}\n    slug    {slug}\n    link to {', '.join(v['related'])}"
                  f"\n    reason  {v['why']}\n")
        return 0

    def apply(self, slug: str) -> int:
        props = self.load()
        p = props.get(slug)
        if not p or p.get("status") != "pending":
            print(f"No pending proposal for: {slug}" + (f" (status={p.get('status')})" if p else ""))
            return 1
        path = self.wiki / p["path"]
        text = path.read_text(encoding="utf-8")
        m = FM_RE.match(text)
        if not m:
            print("Page has no frontmatter; not touching it.")
            return 1
        fm = m.group(2)
        mr = re.search(r"^related:\s*(.*)$", fm, re.M)
        cur: list[str] = []
        if mr:
            raw = mr.group(1).strip()
            if raw.startswith("["):
                cur = [x.strip().strip("'\"") for x in raw.strip("[]").split(",") if x.strip()]
            elif raw:
                cur = [x.strip() for x in raw.split(",") if x.strip()]
            else:  # block list below the key
                blk = re.search(r"^related:\s*\n((?:\s+-\s+.*\n?)+)", fm, re.M)
                if blk:
                    cur = [re.sub(r"^\s+-\s+", "", ln).strip().strip("'\"")
                           for ln in blk.group(1).splitlines() if ln.strip()]
                    fm = fm.replace(blk.group(0), "related:\n", 1)
        merged = sorted(set(cur) | set(p["related"]))
        line = "related: [" + ", ".join(merged) + "]"
        fm = re.sub(r"^related:\s*.*$", line, fm, count=1, flags=re.M) if mr else fm.rstrip() + "\n" + line
        # A person reviewed the page, so its confidence may go up (if the key exists).
        fm = re.sub(r"^confidence:\s*.*$", "confidence: high", fm, count=1, flags=re.M)
        path.write_text(text[:m.start(2)] + fm + text[m.end(2):], encoding="utf-8")
        p["status"], p["applied_at"] = "applied", date.today().isoformat()
        self.save(props)
        print(f"Applied: {p['title'][:70]}\n  related: {', '.join(merged)}")
        print("  The graph is rebuilt from the files on every query, so the new edges are live.")
        return 0

    def drop(self, slug: str) -> int:
        props = self.load()
        p = props.get(slug)
        if not p:
            print(f"No proposal for: {slug}")
            return 1
        # Keep the record. Deleting it put the page straight back into tomorrow's queue.
        p.update(status="dropped", dropped_at=date.today().isoformat(), related=[])
        self.save(props)
        print(f"Dropped: {slug}. It will not be proposed again (the record is kept).")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Human-approved linking of isolated wiki pages")
    ap.add_argument("--wiki", default=os.environ.get("WIKI_DIR", "."))
    ap.add_argument("--ledger", help="proposal ledger JSON (default <wiki>/reports/link-proposals.json)")
    ap.add_argument("--types", default="reference,article,source",
                    help="unlinked page types to queue (comma separated). Full-text `paper` pages are"
                         " left out by default: link the paper's reference page instead")
    ap.add_argument("--targets", default="concept,comparison,entity,synthesis",
                    help="page types that may be linked to")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--n", type=int, default=3)
    pr = sub.add_parser("propose")
    pr.add_argument("slug")
    pr.add_argument("--related", required=True)
    pr.add_argument("--why", required=True)
    sk = sub.add_parser("skip")
    sk.add_argument("slug")
    sk.add_argument("--why", required=True)
    sub.add_parser("pending")
    for name in ("apply", "drop"):
        sub.add_parser(name).add_argument("slug")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    wiki = Path(a.wiki).expanduser().resolve()
    if not wiki.is_dir():
        print(f"wiki directory not found: {wiki}", file=sys.stderr)
        return 2
    ledger = Path(a.ledger) if a.ledger else wiki / "reports" / "link-proposals.json"
    r = Review(wiki, ledger, a.types.split(","), a.targets.split(","))
    if a.cmd == "queue":
        return r.queue(a.n)
    if a.cmd == "propose":
        return r.propose(a.slug, a.related, a.why)
    if a.cmd == "skip":
        return r.skip(a.slug, a.why)
    if a.cmd == "pending":
        return r.pending()
    return r.apply(a.slug) if a.cmd == "apply" else r.drop(a.slug)


if __name__ == "__main__":
    raise SystemExit(main())
