# Source Scout

**English** | [简体中文](README.zh-CN.md)

[![English](https://img.shields.io/badge/README-English-0969da)](README.md)
[![简体中文](https://img.shields.io/badge/README-简体中文-d73a49)](README.zh-CN.md)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-26%20passing-2ea44f)](plugins/source-scout/skills/source-scout/tests/test_discover.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Follow on X](https://img.shields.io/badge/X-@cclove057-000000?logo=x&logoColor=white)](https://x.com/cclove057)

Source Scout is an agent skill and standalone Python workflow for discovering public official documentation and the authoritative sources it cites. Starting from one official documentation URL, it discovers in-scope sources, removes deterministic noise, preserves uncertain candidates for review, and exports an auditable URL collection.

Use it when you need a traceable source list for NotebookLM, research preparation, documentation indexing, or another downstream tool. Source Scout is not a general-purpose crawler, website mirror, or unrestricted web-research agent.

## What it does

- Discovers documentation through the seed page, sitemaps, `llms.txt`, navigation, and in-scope links.
- Separates relevance from authority instead of treating every official-domain URL as useful.
- Supports Fast, Standard, and Deep collection modes.
- Resumes interrupted work from run-local checkpoints without refetching completed pages.
- Handles retries, robots restrictions, redirects, canonical URLs, dynamic-page evidence, and access-limited sources.
- Exports included, excluded, and uncertain candidates with reasons and decision history.
- Uses bounded classification batches and validated group rules for large ambiguous result sets.
- Runs as a skill-driven agent workflow or as a standalone deterministic CLI.

## Collection modes

| Mode | Default page limit | Speed | Relative token cost | Coverage |
| --- | ---: | --- | --- | --- |
| Fast | 100 | Fastest | Lowest | Seed page, published inventories, and direct seed references |
| Standard | 500 | Moderate | Moderate | Recursive in-scope documentation discovery and direct authoritative citations |
| Deep | 2,000 | Slowest | Highest | Cross-checks indexes, navigation, dynamic pages, and recoverable failures |

All modes use the same acceptance policy. A mode changes discovery intensity, not the meaning of authority or relevance. Reaching a page limit without queue convergence produces `decision_required`; it is never reported as complete.

## Requirements

- Python 3.10 or newer
- Public HTTP(S) access to the target documentation
- An agent capable of reading `SKILL.md`, running shell commands, and classifying ambiguous candidates for the full workflow
- Optional browser capability for dynamic pages in Standard or Deep mode

The deterministic CLI uses only the Python standard library. Browser dependencies are never installed without explicit user approval.

## Installation

### Standalone skill

For a first installation, clone the repository and copy the skill directory into your agent's skill location:

```powershell
git clone https://github.com/ccclucky/source-scout.git
New-Item -ItemType Directory -Force $HOME/.codex/skills | Out-Null
Copy-Item -Recurse source-scout/plugins/source-scout/skills/source-scout $HOME/.codex/skills/source-scout
```

Run these commands from the directory in which you want the repository cloned. Start a new Codex task or reload skills using the mechanism provided by your client, then invoke `$source-scout` in chat to verify discovery. For another agent, copy the same directory into that agent's supported skills directory.

To update an existing installation, pull the repository, remove only the existing `source-scout` skill directory, and copy the replacement cleanly so removed files cannot remain stale:

```powershell
git -C source-scout pull
$destination = Join-Path $HOME ".codex/skills/source-scout"
Remove-Item -Recurse -Force -LiteralPath $destination
Copy-Item -Recurse source-scout/plugins/source-scout/skills/source-scout $destination
```

The distributable skill contains [SKILL.md](plugins/source-scout/skills/source-scout/SKILL.md), [references](plugins/source-scout/skills/source-scout/references/), [scripts](plugins/source-scout/skills/source-scout/scripts/), and [tests](plugins/source-scout/skills/source-scout/tests/); it does not require the plugin wrapper.

### Plugin package

The repository includes [Codex](plugins/source-scout/.codex-plugin/plugin.json) and [Claude Code](plugins/source-scout/.claude-plugin/plugin.json) plugin metadata under `plugins/source-scout/`, plus marketplace metadata at the repository root. These files package the same standalone skill; this README recommends the copy-based installation above because plugin installation commands vary by client version.

## Agent usage

Invoke the skill and provide an official documentation entry URL. Version, language, product, and content constraints are optional but improve scope accuracy.

```text
$source-scout collect the LangGraph Python documentation starting from
https://docs.langchain.com/oss/python/langgraph/overview
```

If material scope details are missing, Source Scout proposes a Collection Scope for confirmation before costly discovery. It then explains the mode trade-offs and estimated page, time, and token costs before execution.

The normal workflow is:

1. Validate the Seed Page and confirm the Collection Scope.
2. Choose Fast, Standard, or Deep mode.
3. Start or resume deterministic discovery.
4. Classify ambiguous candidates in batches.
5. Review material uncertain candidates.
6. Export the final source set and audit files.

## CLI usage

The CLI is stateful: `start` creates a unique run directory, and later commands operate on that directory. From the repository root:

```powershell
$script = "plugins/source-scout/skills/source-scout/scripts/discover.py"
$seedUrl = "https://docs.example.com/product/overview"

$start = python $script start --url $seedUrl --mode standard --output-root output | ConvertFrom-Json
$runDir = $start.run_dir

python $script status --run-dir $runDir
python $script resume --run-dir $runDir --max-pages 1000
python $script resume --run-dir $runDir --retry-failed
python $script classify next --run-dir $runDir --limit 50
python $script classify submit --run-dir $runDir --input decisions.json
python $script export --run-dir $runDir
```

Use `--scope` for the primary URL-path boundary and repeat `--scope-root` for additional verified official roots:

```powershell
python $script start `
  --url https://docs.example.com/product/overview `
  --scope /product `
  --scope-root https://api.example.com/product `
  --mode deep `
  --output-root output
```

`start` prints JSON containing `run_dir`; the example stores it in `$runDir` for every later command. Classification batches default to 50 candidates and accept at most 100. There is no run-wide classification or token circuit breaker. Replace `start` below with any command name to inspect its help, for example `python $script start --help`.

After Ctrl+C, a process termination, or another recoverable interruption, rerun `python $script resume --run-dir $runDir`. The checkpoint retains completed pages and pending work, so the run does not start over.

`classify next` prints candidate evidence as JSON. An agent reviews that batch and writes a decision array such as:

```json
[
  {
    "id": "0123456789abcdef",
    "decision": "include",
    "category": "documentation",
    "confidence": "high",
    "reason": "Official in-scope product documentation."
  }
]
```

Submit every ID from the active batch exactly once. `include` and `exclude` require high confidence; medium- or low-confidence results must use `uncertain`. Normal export requires converged discovery and no pending classifications. Classified `uncertain` candidates are written to `uncertain.txt`; use `classify override` before export when the user resolves one. `export --partial` is reserved for an explicit user decision to stop early.

```powershell
python $script classify override `
  --run-dir $runDir `
  --id 0123456789abcdef `
  --decision include `
  --reason "User confirmed this source is in scope."
```

### Grouped review

Grouped review is available only when at least 50 classified candidates are uncertain and they represent at least 20% of classified candidates:

```powershell
python $script classify group-next `
  --run-dir $runDir `
  --field path `
  --operator prefix `
  --value /docs/api/

python $script classify group-submit --run-dir $runDir --input group-decision.json
```

A group rule requires three structurally diverse proposal samples and a disjoint validation sample. Every validation decision and category must agree before the rule can be applied. A failed rule leaves candidates uncertain and permits at most one automatically prioritized subgroup attempt. A minimal submission has this shape:

```json
{
  "rule_id": "0123456789abcdef",
  "decision": "include",
  "category": "reference",
  "reason": "This validated group contains in-scope API reference pages.",
  "sample_decisions": [
    {
      "id": "fedcba9876543210",
      "decision": "include",
      "category": "reference",
      "confidence": "high",
      "reason": "In-scope API reference sample."
    }
  ]
}
```

The real file must contain every proposal and validation sample returned by `group-next`, not only the single illustrative row above.

## Output

Each run writes to `output/<project>/<timestamp>/`. The timestamp directory is the run ID and contains its checkpoint, evidence, classification progress, and exports.

| File | Purpose |
| --- | --- |
| `urls.txt` | Primary deliverable containing confirmed included URLs |
| `uncertain.txt` | User-review entry point for unresolved candidates |
| `report.md` | Concise completion status, counts, discovery channels, and material gaps |
| `results.csv` | Auditable candidate-level results and decisions |
| `excluded.txt` | Excluded candidates and reasons |
| `checkpoint.json` | Resumable run state; treat as an internal workflow artifact |
| `all-candidates.json` | Optional detailed audit output for Deep or debugging workflows |

Completed runs are immutable. Changing the Collection Scope or Collection Mode starts a new run rather than modifying an existing result.

## Safety and boundaries

- The Seed Page must be a public official documentation entry point.
- Private, loopback, and local network targets are rejected. Proxy-provided synthetic DNS addresses are accepted only when the hostname is actually routed through the configured proxy.
- Robots restrictions, authentication, CAPTCHAs, and paywalls are recorded and never bypassed.
- Access-limited sources are not automatically treated as irrelevant.
- Malformed inventory links are ignored as invalid candidates instead of aborting a run.
- Fast mode never uses browser rendering.
- Browser tooling is used only when already available or after explicit approval for an isolated installation.
- Source repositories may be included when directly cited, but Source Scout does not recursively scan repository code.
- The tool reports known gaps and convergence state; it does not promise absolute completeness for arbitrary websites.

## Development

Run the complete test suite from the repository root:

```powershell
python -m unittest discover `
  -s plugins/source-scout/skills/source-scout/tests `
  -v
```

The suite uses local HTTP fixtures and mocked network boundaries. External sites are not deterministic regression tests. Before a release, run separate smoke checks against real documentation:

```powershell
$script = "plugins/source-scout/skills/source-scout/scripts/discover.py"

python $script start `
  --url https://docs.langchain.com/oss/python/langgraph/overview `
  --scope /oss/python/langgraph `
  --mode standard `
  --max-pages 150 `
  --output-root release-smoke

python $script start `
  --url https://docs.python.org/3/tutorial/index.html `
  --scope /3/tutorial `
  --mode standard `
  --max-pages 100 `
  --output-root release-smoke
```

Treat site redesigns and network failures as smoke-test findings, not deterministic unit-test regressions. A successful discovery smoke run reports `discovery_status: converged`; semantic classification is a separate agent step.

## Repository layout

```text
.
├── .agents/plugins/marketplace.json
├── .claude-plugin/marketplace.json
└── plugins/source-scout/
    ├── .claude-plugin/plugin.json
    ├── .codex-plugin/plugin.json
    └── skills/source-scout/
        ├── SKILL.md
        ├── references/
        │   └── classification-policy.md
        ├── scripts/
        │   └── discover.py
        └── tests/
            └── test_discover.py
```

[SKILL.md](plugins/source-scout/skills/source-scout/SKILL.md) defines the agent workflow. The [classification policy](plugins/source-scout/skills/source-scout/references/classification-policy.md) defines deterministic and semantic decision boundaries. `discover.py` provides deterministic discovery, checkpointing, classification exchange, and export. The same skill directory is used by both plugin wrappers to avoid implementation drift.

## Current limitations

- The first release exports URL collections; it does not mirror page content or import directly into NotebookLM.
- Semantic classification requires an agent or externally supplied decisions. Standalone CLI discovery leaves ambiguous candidates unresolved.
- Dynamic coverage depends on browser capability supplied by the execution environment.
- Version, language, and documentation boundaries still depend on the quality of the confirmed Collection Scope.

## Support

Report reproducible bugs, documentation problems, and feature requests through [GitHub Issues](https://github.com/ccclucky/source-scout/issues). Include the command used, run status, and relevant non-sensitive report or checkpoint details. Do not publish credentials, private URLs, or restricted content.

## License

Source Scout is released under the [MIT License](LICENSE). Copyright © 2026 ccclucky.
