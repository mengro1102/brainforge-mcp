"""Tests for the v0.2 MCP tools against examples/wiki. Run: python -m unittest discover -s tests -v"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_wiki_mcp import server  # noqa: E402

EXAMPLE = ROOT / "examples" / "wiki"


def call(tool, **kw):
    """@mcp.tool may return the function or a tool object wrapping it."""
    fn = getattr(tool, "fn", tool)
    return fn(**kw)


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        shutil.copytree(EXAMPLE.parent, Path(self.tmp.name) / "ex")
        self.wiki = Path(self.tmp.name) / "ex" / "wiki"
        server._wiki_dir = self.wiki
        server._graph = None

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_wiki_returns_reading_list(self):
        out = call(server.search_wiki, query="LoRA")
        first = out.splitlines()[2]
        self.assertIn("concepts/lora.md", first)
        self.assertIn("Read the top 2-4 paths", out)

    def test_search_wiki_korean_and_fallback(self):
        out = call(server.search_wiki, query="저랭크 행렬")
        self.assertIn("full-text match", out)

    def test_lint_wiki_example_passes_with_growth_warnings(self):
        out = call(server.lint_wiki)
        self.assertIn("PASS", out, out)
        self.assertIn("[W] broken wikilink -> [[quantization]]", out)  # unwritten page = to-do, not error
        self.assertNotIn("missing required keys", out)  # brainforge pages have no `type:`
        self.assertNotIn("broken source path", out)    # raw/ next to wiki/ is resolved

    def test_lint_wiki_catches_real_errors(self):
        p = self.wiki / "concepts" / "lora.md"
        p.write_text(p.read_text(encoding="utf-8").replace("created: 2026-04-17", "created: 2026-04-17\ncreated: 17/04/2026"),
                     encoding="utf-8")
        out = call(server.lint_wiki)
        self.assertIn("FAIL", out)
        self.assertIn("duplicate frontmatter key: created", out)

    def test_suggest_links(self):
        out = call(server.suggest_links)
        self.assertIn("linked", out)
        (self.wiki / "sources" / "orphan.md").write_text(
            "---\ntitle: Orphan note on LoRA\ntags: [x]\nsources: []\n---\n\nAbout LoRA adapters.\n",
            encoding="utf-8")
        out = call(server.suggest_links, n=3)
        self.assertIn("sources/orphan.md", out)
        self.assertIn("lora", out)


if __name__ == "__main__":
    unittest.main()
