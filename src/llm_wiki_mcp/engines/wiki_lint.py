#!/usr/bin/env python3
"""wiki_lint.py: integrity linter for LLM-maintained markdown wikis (Obsidian vaults).

Extracted from a production wiki that an agent edits every day. Each check
below exists because it caught a real failure the older linter let through.

Checks (ERROR = exit 1, WARN = report only):
  [E] frontmatter missing, or missing the keys required for its page type
  [E] frontmatter is not valid YAML          (needs PyYAML; skipped otherwise)
  [E] duplicate top-level frontmatter key     (YAML silently keeps the last one)
  [E] unquoted numeric identifier             (arXiv 2103.00020 -> float 2103.0002)
  [E] `slug:` differs from the file name
  [E] created/updated/ingested not YYYY-MM-DD
  [E] `sources:` path missing on disk, or listed twice
  [E] broken relative .md link / broken [[wikilink]] in curated pages
  [E] index.md out of sync with the pages on disk   (if index.md exists)
  [E] abbreviation expansion contradicts glossary.md (if glossary.md exists)
  [W] foreign-script leakage in the body (e.g. CJK characters from Qwen-family models)
  [W] abbreviation expanded but not in glossary.md

Usage:
  python wiki_lint.py [WIKI_DIR] [--config wiki-lint.json] [--json] [--report DIR] [--fix]

--fix applies only mechanical, meaning-preserving repairs before linting:
  * quote unquoted numeric identifiers     identifier: 2103.00020 -> identifier: '2103.00020'
    (cannot restore a zero YAML already dropped; those still need checking by hand)
  * `related: [[a]], [[b]]` / `related: a, b` / `related: [concepts/a.md]` -> `related: [a, b]`
Everything else is left for a person or the agent to judge.

Standard library only. PyYAML is optional and enables the YAML-validity check.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised only without PyYAML
    yaml = None

DEFAULT_CONFIG: dict = {
    # page type -> directory (relative to the wiki root). Missing dirs are skipped.
    "curated": {
        "concept": "concepts",
        "comparison": "comparisons",
        "entity": "entities",
        "query": "queries",
        "synthesis": "syntheses",
        "reference": "references",
    },
    # source layer: only provenance is checked, links are not.
    "raw": {
        "paper": "raw/papers",
        "article": "raw/articles",
    },
    "required_keys": {
        "*": ["title", "type", "tags", "sources"],
        "article": [],          # articles may carry source_url instead (checked separately)
    },
    "index": "index.md",        # set to null to disable the index-sync check
    "index_sections": ["concepts", "comparisons", "entities", "queries", "syntheses"],
    "glossary": "glossary.md",  # set to null to disable the abbreviation check
    # where `sources:` paths are resolved from, relative to the wiki root
    # (use [".", ".."] when raw/ sits next to the wiki folder instead of inside it)
    "source_roots": ["."],
    # "error" (default) or "warn": wikis that use links to not-yet-written pages
    # as a to-do list can downgrade them to warnings
    "dangling_wikilinks": "error",
    # Unicode scripts that should never appear in body text.
    # han = CJK ideographs, thai, kana = Japanese kana. Use [] to disable.
    "leak_scripts": ["han", "thai"],
    "leak_allow_files": ["README.md", "glossary.md"],
    "wikilink_ignore": ["wikilinks", "link", "slug", "page-slug", "xxx", "..."],
}

SCRIPT_RANGES = {
    "han": "\u4e00-\u9fff\u3400-\u4dbf",
    "thai": "\u0e00-\u0e7f",
    "kana": "\u3040-\u30ff",
    "cyrillic": "\u0400-\u04ff",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_FENCE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
NUMERIC_ID_RE = re.compile(r"\s*(identifier|doi|arxiv|arxiv_id)\s*:\s*([0-9][0-9.]*)\s*$")

INLINE_EXPANSION_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9-]{1,14})\s*\(\s*([A-Z][A-Za-z0-9 ,'&/-]{5,80}?)\s*\)")
GLOSSARY_ROW_RE = re.compile(r"^\|\s*([A-Za-z0-9-]+)\s*\|\s*([^|]+?)\s*\|")
CITATION_RE = re.compile(r"\bet al\.|^\s*[A-Z][a-z]+\s+(&|and)\s+[A-Z][a-z]+|\b(19|20)\d{2}\b")
UNVERIFIED_MARKERS = ("unverified", "미확인")
_STOPWORDS = {"and", "of", "the", "with", "for", "from", "in", "on", "a", "an", "to"}


# ── config ───────────────────────────────────────────────────────────────

def load_config(path: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# ── frontmatter helpers ──────────────────────────────────────────────────

def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Frontmatter is '' when absent."""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    body_start = text.find("\n", end + 1)
    return text[3:end].strip("\n"), (text[body_start + 1:] if body_start != -1 else "")


def top_level_keys(fm: str) -> list[str]:
    return [m.group(1) for line in fm.splitlines() if (m := KEY_RE.match(line))]


def fm_value(fm: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", fm, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def source_paths(fm: str) -> list[str]:
    """Every repo-relative path listed under `sources:`.

    Accepts both `sources: [a.md, b.md]` and a list of mappings with `path:`.
    """
    out: list[str] = []
    inline = re.search(r"^sources:\s*\[(.*?)\]\s*$", fm, re.M)
    if inline:
        out += [s.strip().strip("'\"") for s in inline.group(1).split(",") if s.strip()]
    out += [m.group(1).strip().strip("'\"")
            for m in re.finditer(r"^\s*-?\s*path:\s*(.+?)\s*$", fm, re.M)]
    block = re.search(r"^sources:\s*\n((?:\s+-\s+[^\n]+\n?)+)", fm, re.M)
    if block:
        for line in block.group(1).splitlines():
            item = line.strip()[1:].strip().strip("'\"")
            if item and ":" not in item:
                out.append(item)
    return out


# ── individual checks ────────────────────────────────────────────────────

def check_yaml(fm: str) -> list[str]:
    if yaml is None:
        return []
    try:
        parsed = yaml.safe_load(fm)
    except yaml.YAMLError as exc:
        return [f"frontmatter is not valid YAML: {str(exc).splitlines()[0]}"]
    if parsed is not None and not isinstance(parsed, dict):
        return ["frontmatter is not a mapping"]
    return []


def check_duplicate_keys(fm: str) -> list[str]:
    counts: dict[str, int] = {}
    for k in top_level_keys(fm):
        counts[k] = counts.get(k, 0) + 1
    return [f"duplicate frontmatter key: {k} (YAML keeps only the last value)"
            for k, c in sorted(counts.items()) if c > 1]


def check_numeric_identifier(fm: str) -> list[str]:
    out = []
    for line in fm.splitlines():
        m = NUMERIC_ID_RE.match(line)
        if m:
            out.append(f"unquoted numeric identifier: {m.group(1)}: {m.group(2)} -> quote it as "
                       f"'{m.group(2)}' (YAML reads it as a float and drops trailing zeros)")
    return out


def check_slug(path: Path, fm: str) -> list[str]:
    v = fm_value(fm, "slug")
    return [f"slug mismatch: '{v}' != file name '{path.stem}'"] if v and v != path.stem else []


def check_dates(fm: str) -> list[str]:
    out = []
    for key in ("created", "updated", "ingested"):
        v = fm_value(fm, key)
        if v and not DATE_RE.match(v):
            out.append(f"{key} is not YYYY-MM-DD: {v!r}")
    return out


def check_leak(path: Path, body: str, leak_re: re.Pattern | None, allow: set[str]) -> list[str]:
    if leak_re is None or path.name in allow:
        return []
    hits = []
    for i, line in enumerate(CODE_FENCE_RE.sub("", body).splitlines(), 1):
        if leak_re.search(line):
            hits.append(f"foreign-script leakage (body line {i}): {line.strip()[:60]}")
    return hits


def load_glossary(path: Path | None) -> tuple[dict[str, str], set[str]]:
    if path is None or not path.exists():
        return {}, set()
    known: dict[str, str] = {}
    unverified: set[str] = set()
    in_unverified = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            in_unverified = any(m in line.lower() for m in UNVERIFIED_MARKERS)
            continue
        m = GLOSSARY_ROW_RE.match(line)
        if not m:
            continue
        abbr, full = m.group(1).strip(), m.group(2).strip()
        if set(abbr) <= {"-"} or abbr.lower() in {"abbr", "abbreviation", "약어"}:
            continue
        if in_unverified or any(mk in full.lower() for mk in UNVERIFIED_MARKERS):
            unverified.add(abbr)
        elif full and not full.startswith("("):
            known[abbr] = full
    return known, unverified


def looks_like_expansion(abbr: str, text: str) -> bool:
    """True when the parenthesised text spells out the abbreviation.

    `MLP (Multi-Layer Perceptron)` -> True; `GELU (GPT-2, ViT, ConvNeXt)` -> False.
    """
    letters = [c.lower() for c in abbr if c.isalpha()]
    words = [w for w in re.split(r"[^A-Za-z]+", text) if w and w.lower() not in _STOPWORDS]
    if not letters or not words:
        return False
    initials = [w[0].lower() for w in words]
    i = 0
    for ch in letters:
        while i < len(initials) and initials[i] != ch:
            i += 1
        if i == len(initials):
            return False
        i += 1
    return True


def check_abbreviations(body: str, known: dict[str, str], unverified: set[str]
                        ) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    if not known and not unverified:
        return errors, warns
    seen: dict[str, str] = {}
    for m in INLINE_EXPANSION_RE.finditer(body):
        abbr, full = m.group(1), m.group(2).strip()
        if CITATION_RE.search(full) or not looks_like_expansion(abbr, full):
            continue
        if abbr in unverified:
            errors.append(f"expansion given for an unverified abbreviation: {abbr} ({full})")
            continue
        ref = known.get(abbr)
        if ref and full.lower() != ref.lower():
            errors.append(f"expansion contradicts glossary: {abbr} ({full}) != '{ref}'")
        elif not ref:
            warns.append(f"abbreviation not in glossary: {abbr} ({full})")
        prev = seen.get(abbr)
        if prev and prev.lower() != full.lower():
            errors.append(f"inconsistent expansions in one page: {abbr} -> '{prev}' / '{full}'")
        seen[abbr] = full
    return errors, warns


# ── safe fixes ───────────────────────────────────────────────────────────

RELATED_LINE_RE = re.compile(r"^related:[ \t]*(.+?)\s*$", re.M)


def _norm_related(raw: str) -> str | None:
    """Return a normalized `[a, b]` list, or None when the line is already fine."""
    v = raw.strip()
    if re.fullmatch(r"\[[^\[\]]*\]", v) and "/" not in v and ".md" not in v:
        return None  # already a flat slug list
    items = re.findall(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]", v)
    if not items:
        items = [x for x in re.split(r"[,\[\]]", v) if x.strip()]
    slugs = []
    for it in items:
        it = it.strip().strip("'\"").split("/")[-1]
        it = it[:-3] if it.endswith(".md") else it
        if it and it not in slugs:
            slugs.append(it)
    return "[" + ", ".join(slugs) + "]"


def fix_text(text: str) -> tuple[str, list[str]]:
    fm, _ = split_frontmatter(text)
    if not fm:
        return text, []
    changes = []
    new = fm
    for line in fm.splitlines():
        m = NUMERIC_ID_RE.match(line)
        if m:
            fixed = line.replace(f"{m.group(2)}", f"'{m.group(2)}'", 1)
            new = new.replace(line, fixed, 1)
            changes.append(f"quoted {m.group(1)}: {m.group(2)}")
    m = RELATED_LINE_RE.search(new)
    if m:
        norm = _norm_related(m.group(1))
        if norm is not None and norm != m.group(1).strip():
            new = new[:m.start(1)] + norm + new[m.end(1):]
            changes.append(f"related: {m.group(1).strip()} -> {norm}")
    if not changes:
        return text, []
    start = text.find(fm)
    return text[:start] + new + text[start + len(fm):], changes


def apply_fixes(root: Path, linter: "Linter") -> dict[str, list[str]]:
    done = {}
    for path, _ftype, _raw in linter.targets():
        text = path.read_text(encoding="utf-8", errors="replace")
        new, changes = fix_text(text)
        if changes:
            path.write_text(new, encoding="utf-8")
            done[path.relative_to(root).as_posix()] = changes
    return done


# ── driver ───────────────────────────────────────────────────────────────

class Linter:
    def __init__(self, root: Path, cfg: dict):
        self.root = root
        self.cfg = cfg
        ranges = "".join(SCRIPT_RANGES[s] for s in cfg.get("leak_scripts") or [] if s in SCRIPT_RANGES)
        self.leak_re = re.compile(f"[{ranges}]") if ranges else None
        self.leak_allow = set(cfg.get("leak_allow_files") or [])
        gl = cfg.get("glossary")
        self.glossary_path = root / gl if gl else None
        self.glossary = load_glossary(self.glossary_path)
        self.ignore_links = set(cfg.get("wikilink_ignore") or [])

    def targets(self) -> list[tuple[Path, str, bool]]:
        out = []
        for layer, is_raw in (("curated", False), ("raw", True)):
            for ftype, rel in self.cfg[layer].items():
                d = self.root / rel
                if d.is_dir():
                    for p in sorted(d.glob("*.md")):
                        if p.name.lower() != "readme.md":
                            out.append((p, ftype, is_raw))
        return out

    def all_slugs(self) -> set[str]:
        slugs = {p.stem for p in self.root.rglob("*.md")
                 if not any(part.startswith(".") for part in p.relative_to(self.root).parts)}
        for p in self.root.rglob("*.md"):
            fm, _ = split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            for alias_line in re.findall(r"^aliases:\s*\[(.*?)\]", fm, re.M):
                slugs |= {a.strip().strip("'\"") for a in alias_line.split(",") if a.strip()}
        return slugs

    def required_keys(self, ftype: str) -> set[str]:
        rk = self.cfg["required_keys"]
        return set(rk.get(ftype, rk.get("*", [])))

    def lint_file(self, path: Path, ftype: str, is_raw: bool, slugs: set[str]
                  ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warns: list[str] = []
        fm, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if not fm:
            return ["frontmatter missing"], warns

        errors += check_yaml(fm)
        errors += check_duplicate_keys(fm)
        errors += check_numeric_identifier(fm)
        errors += check_slug(path, fm)
        errors += check_dates(fm)
        warns += check_leak(path, body, self.leak_re, self.leak_allow)

        keys = set(top_level_keys(fm))
        missing = self.required_keys(ftype) - keys
        if missing:
            errors.append(f"missing required keys: {sorted(missing)}")
        if ftype == "article" and not ({"source_url", "sources"} & keys):
            errors.append("no provenance: add source_url or sources")

        seen: set[str] = set()
        for rp in source_paths(fm):
            if rp in seen:
                errors.append(f"duplicate source path: {rp}")
            seen.add(rp)
            if rp.startswith(("http://", "https://")) or not rp.endswith((".md", ".pdf")):
                continue
            if not any((self.root / base / rp).exists() for base in self.cfg.get("source_roots") or ["."]):
                errors.append(f"broken source path: {rp}")

        if not is_raw:
            if path != self.glossary_path:
                e, w = check_abbreviations(body, *self.glossary)
                errors += e
                warns += w
            clean = CODE_FENCE_RE.sub("", body)
            for m in MD_LINK_RE.finditer(clean):
                target = m.group(1).split("#")[0].strip()
                if (not target or not target.endswith(".md")
                        or target.startswith(("http://", "https://", "mailto:"))):
                    continue
                if not (path.parent / target).resolve().exists():
                    errors.append(f"broken link -> {target}")
            for m in WIKILINK_RE.finditer(clean):
                slug = m.group(1).strip().split("/")[-1]
                if slug and slug not in self.ignore_links and slug not in slugs:
                    msg = f"broken wikilink -> [[{slug}]]"
                    target = warns if self.cfg.get("dangling_wikilinks") == "warn" else errors
                    if msg not in target:
                        target.append(msg)
        return errors, warns

    def lint_index(self) -> list[str]:
        rel = self.cfg.get("index")
        if not rel:
            return []
        index = self.root / rel
        if not index.exists():
            return []
        text = index.read_text(encoding="utf-8", errors="replace")
        linked = {m.group(1).strip().split("/")[-1] for m in WIKILINK_RE.finditer(text)}
        linked |= {Path(m.group(1)).stem for m in MD_LINK_RE.finditer(text) if m.group(1).endswith(".md")}
        errors = []
        for section in self.cfg.get("index_sections") or []:
            d = self.root / section
            if not d.is_dir():
                continue
            on_disk = {p.stem for p in d.glob("*.md") if p.name.lower() != "readme.md"}
            missing = sorted(on_disk - linked)
            if missing:
                errors.append(f"pages missing from {rel} [{section}]: {missing}")
        all_stems = {p.stem for p in self.root.rglob("*.md")}
        dangling = sorted(s for s in linked - all_stems if s not in self.ignore_links)
        if dangling:
            errors.append(f"{rel} links to pages that do not exist: {dangling}")
        return errors

    def run(self) -> dict:
        slugs = self.all_slugs()
        files: dict[str, dict] = {}
        targets = self.targets()
        n_ok = 0
        for path, ftype, is_raw in targets:
            errors, warns = self.lint_file(path, ftype, is_raw, slugs)
            rel = path.relative_to(self.root).as_posix()
            if errors or warns:
                files[rel] = {"errors": errors, "warns": warns}
            else:
                n_ok += 1
        idx_errors = self.lint_index()
        if idx_errors:
            files[self.cfg["index"]] = {"errors": idx_errors, "warns": []}
        total_e = sum(len(v["errors"]) for v in files.values())
        total_w = sum(len(v["warns"]) for v in files.values())
        return {
            "wiki": str(self.root),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": len(targets),
            "ok": n_ok,
            "errors": total_e,
            "warnings": total_w,
            "yaml_check": yaml is not None,
            "result": "FAIL" if total_e else "PASS",
            "details": files,
        }


def render_markdown(res: dict) -> str:
    lines = [
        "# Wiki Lint Report",
        f"- wiki: {res['wiki']}",
        f"- time: {res['time']}",
        f"- files: {res['files']} (ok {res['ok']})",
        f"- errors: {res['errors']}  warnings: {res['warnings']}",
        f"- result: **{res['result']}**",
    ]
    if not res["yaml_check"]:
        lines.append("- note: PyYAML not installed; YAML-validity check skipped (`pip install pyyaml`)")
    if res.get("fixed"):
        lines.append(f"- fixed: {sum(len(v) for v in res['fixed'].values())} change(s) in {len(res['fixed'])} file(s)")
        for rel, ch in res["fixed"].items():
            lines += [f"  - {rel}: {c}" for c in ch]
    lines.append("")
    for rel, d in res["details"].items():
        lines.append(f"## {rel}")
        lines += [f"- [E] {e}" for e in d["errors"]]
        lines += [f"- [W] {w}" for w in d["warns"]]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wiki", nargs="?", default=".", help="wiki root (default: current dir)")
    ap.add_argument("--config", help="JSON config (default: <wiki>/wiki-lint.json if present)")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("--report", help="also write lint-<stamp>.md and lint-latest.md into this dir")
    ap.add_argument("--fix", action="store_true", help="apply safe mechanical fixes first (see above)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = Path(args.wiki).expanduser().resolve()
    if not root.is_dir():
        print(f"wiki directory not found: {root}", file=sys.stderr)
        return 2
    cfg_path = Path(args.config) if args.config else root / "wiki-lint.json"
    cfg = load_config(cfg_path)
    fixed = apply_fixes(root, Linter(root, cfg)) if args.fix else {}
    res = Linter(root, cfg).run()
    res["fixed"] = fixed
    report = render_markdown(res)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(report)
    if args.report:
        out = Path(args.report)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (out / f"lint-{stamp}.md").write_text(report, encoding="utf-8")
        (out / "lint-latest.md").write_text(report, encoding="utf-8")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
