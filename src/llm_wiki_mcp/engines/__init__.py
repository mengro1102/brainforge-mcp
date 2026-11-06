"""Engines vendored from mengro-agent-skills (wiki-graph-search, wiki-lint, wiki-link-review).

Keep these files identical to the skill scripts; update by copying, not editing.
"""
import sys
from pathlib import Path

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)  # the scripts import each other as top-level modules
