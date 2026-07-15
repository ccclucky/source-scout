# Source Scout

Cross-agent skill and Python helper for discovering, classifying, and exporting official or directly referenced web sources from a seed URL.

The repository packages the same skill for Codex and Claude Code. It supports Fast, Standard, and Deep workflows, keeps uncertain candidates traceable, and exports `urls.txt`, `uncertain.txt`, `excluded.txt`, `results.csv`, and `report.md`.

## CLI

```powershell
python scripts/discover.py start --url <SEED_URL> --mode standard --output-root output
python scripts/discover.py start --url <SEED_URL> --scope /docs/product --scope-root https://api.example.com/product --mode deep --output-root output
python scripts/discover.py status --run-dir <RUN_DIR>
python scripts/discover.py resume --run-dir <RUN_DIR> --max-pages 1000
python scripts/discover.py resume --run-dir <RUN_DIR> --retry-failed
python scripts/discover.py classify next --run-dir <RUN_DIR> --limit 50
python scripts/discover.py classify submit --run-dir <RUN_DIR> --input decisions.json
python scripts/discover.py export --run-dir <RUN_DIR>
```

## Status

- LangGraph Python smoke run: discovery converged with 36 fetched pages.
- Python Tutorial smoke run: discovery converged with 17 fetched pages.
- Python tests: 26 passing.
