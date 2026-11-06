# Changelog

## 0.2.0 (unreleased)

- New tools: `search_wiki`, `lint_wiki`, `suggest_links` (English output). Engines are
  vendored from mengro-agent-skills (`src/llm_wiki_mcp/engines/`).
- `lint_wiki` defaults match the `init` vault layout: no `type:` required, `sources:`
  resolved from the vault root, unwritten `[[pages]]` reported as warnings.
- Example vault now ships `examples/raw/articles/` so its `sources:` resolve.
- Tests (`tests/`) and GitHub Actions CI.

## 0.1.0

- Initial release: `explain_node`, `find_path`, `causal_chain`, `graph_summary`, `rebuild_graph`, `init`.
