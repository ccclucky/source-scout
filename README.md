# Authoritative Source Discovery

Cross-agent skill and Python helper for discovering, classifying, and exporting official or directly referenced web sources from a seed URL.

The repository packages the same skill for Codex and Claude Code. It supports Fast, Standard, and Deep workflows, keeps uncertain candidates traceable, and exports `urls.txt`, `uncertain.txt`, `excluded.txt`, `results.csv`, and `report.md`.

## Status

- LangGraph Python smoke run: 33 URLs discovered and classified.
- Python Tutorial smoke run: 17 URLs discovered; robots and retry behavior verified.
- Python tests: 7 passing.
