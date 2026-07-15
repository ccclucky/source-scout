---
name: source-scout
description: Discover public official documentation and its directly cited authoritative sources from a supplied seed URL while reducing irrelevant noise. Use when a user wants a traceable documentation source set for NotebookLM or another downstream tool.
---

# Source Scout

## Quick start

Input: one public official-documentation seed URL, plus optional scope requirements (version, language, product, content to include/exclude). If the supplied page lacks Authority Evidence as an official documentation root, explain the limitation and request an official entry point; reverse-discovery from unofficial leads is out of current scope.

Before running, explain and let the user choose:

| Mode | Speed | Token cost | Coverage |
|---|---|---|---|
| Fast | highest | lowest | basic indexes and static links |
| Standard | medium | medium | core recursion plus semantic review (default) |
| Deep | lowest | highest | cross-checked indexes, navigation, dynamic pages, and failure investigation |

Show expected page count, time, and relative cost. Do not silently upgrade modes.

All modes use the same cleanup and acceptance policy. Fast still applies deterministic cleanup and batched model review to its ambiguous candidates; its lower token cost comes from a smaller discovery set and lighter evidence, not from disabling semantic noise reduction.

## Workflow

1. Infer a provisional scope from the seed URL, title, path, breadcrumbs, and navigation. Confirm it when ambiguous; never assume the whole domain is in scope.
2. Run the bundled crawler:

   ```powershell
   python scripts/discover.py start --url <SEED_URL> --mode <fast|standard|deep> --output-root output
   ```

   `--scope <PATH_PREFIX>` constrains the Seed host. After the agent verifies another official host/root with Authority Evidence, add it explicitly with repeatable `--scope-root <URL_ROOT>`. Never add a host merely because a page links to it.

   It uses public HTTP first, normalizes URLs, removes duplicates, respects `robots.txt`, records failures, and stays within the requested scope where it can infer one.
3. Treat crawler output as candidates, not final truth. Keep all candidates traceable.
   Persist URLs, provenance, and decision evidence, not full page bodies. Titles, descriptions, and minimal summaries may be used transiently for classification; content mirroring and offline archival are out of scope.
   When run without model access, finish deterministic discovery and export evidence/checkpoint state, but leave semantic ambiguities uncertain and do not claim a final denoised result. Later model decisions must resume from that checkpoint without refetching completed pages.
4. Apply deterministic cleanup only to clear junk (assets, mailto/javascript links, fragments, tracking variants, duplicates, and unretrievable records). Do not discard blogs, cases, research, or external links solely by path or domain.
   Resolve redirects, aliases, and canonical metadata deterministically. Keep only the final preferred URL in `urls.txt`, while preserving original URLs, redirect chains, and duplicate relationships in audit records.
5. Classify remaining candidates in batches using the page metadata and link context returned by the crawler. For each candidate choose exactly one: `include`, `exclude`, or `uncertain`; add a category and short reason. Do not send thousands of URLs or full pages in one prompt.
   Read [classification-policy.md](references/classification-policy.md) before classifying.
   Extraction of fetch time, final URL, status, content type, title, description, canonical URL, source page, anchor text, page region, and optional content hash is deterministic script work and must not consume model tokens. Send only rule-ambiguous candidates with minimal evidence to the model.
   Use a second semantic pass with a short main-content excerpt only for a small residual set; never send full pages. Start grouped handling only when at least 50 classified candidates are uncertain and they represent at least 20% of classified candidates. Group by host, path, provenance, and page region. Use only auditable structured conditions over host, path prefix, provenance, page region, and title patterns with equality, prefix, or containment operators; never execute generated code or arbitrary regular expressions.

   A group needs at least six candidates. Use three diverse candidates to propose a rule, then validate it on a disjoint 10% sample, with a minimum of three and maximum of twenty validation items. Select both samples across differing subpaths, title patterns, source pages, and page regions. All validation items must agree. On failure, revoke the rule and split the group at most once, preferring subpath, then provenance and page region. If the subgroup still fails, leave it uncertain. Explain estimated token consumption when presenting the collection modes. After the user approves a mode, do not impose a runtime token circuit breaker or enter `decision_required` merely because an estimated token budget was reached.
   Apply model-derived group rules only to structurally homogeneous candidates and validate them against held-out pages. Revoke a rule when validation fails; leave unverifiable groups uncertain.
6. Retry malformed or failed classification once. If still unresolved, keep `uncertain`; never silently discard it. Ask the user to review uncertain items when they are material.
   If uncertain items are numerous, diagnose scope or classification policy and rerun before asking for review. If ambiguity remains, group items by a shared decision rule; do not offload dozens of item-level decisions to the user.
7. Request and submit complete classification batches until no pending items remain. Use 50 items by default, reduce the batch size when evidence is unusually long, and never exceed 100 items in one batch. There is no run-wide classification-batch limit:

   ```powershell
   python scripts/discover.py classify next --run-dir <RUN_DIR> --limit 50
   python scripts/discover.py classify submit --run-dir <RUN_DIR> --input decisions.json
   ```

   Every row requires `id`, `decision`, `category`, `confidence`, and `reason`. `include`/`exclude` require `high` confidence; `medium`/`low` must be `uncertain`. Submit every returned batch ID exactly once. User overrides use:

   After ordinary classification, use grouped review only when at least 50 candidates are uncertain and they represent at least 20% of classified candidates. Ask the script for disjoint proposal and validation samples using one allowed structured condition:

   ```powershell
   python scripts/discover.py classify group-next --run-dir <RUN_DIR> --field path --operator prefix --value /docs/api/
   ```

   `field` is one of `host`, `path`, `provenance`, `page_region`, or `title`; `operator` is `equals`, `prefix`, or `contains`. Review every returned sample, then submit the individual high-confidence sample decisions together with the proposed rule decision:

   ```powershell
   python scripts/discover.py classify group-submit --run-dir <RUN_DIR> --input group-decision.json
   ```

   The script applies the rule only when all proposal and validation samples agree on decision and category. A counterexample records `validation_failed` and leaves the candidates uncertain. To split a failed group, call `group-next` with only `--run-dir` and `--parent-rule-id <FAILED_RULE_ID>`; the script chooses one narrower subgroup by path first, then provenance, then page region. Each failed parent permits only one split attempt, and a failed subgroup cannot be split again.

   User overrides use:

   ```powershell
   python scripts/discover.py classify override --run-dir <RUN_DIR> --id <ID> --decision <include|exclude|uncertain> --reason <REASON>
   ```

8. Export:

   ```powershell
   python scripts/discover.py export --run-dir <RUN_DIR>
   ```

   Normal export refuses pending classifications or unconverged discovery. Use `--partial` only after the user explicitly chooses to stop.

   `urls.txt` contains only confirmed included URLs; `uncertain.txt`, `excluded.txt`, `results.csv`, and `report.md` preserve the audit trail. Deep/debug runs may also keep `all-candidates.json`.

   Present `urls.txt` as the primary deliverable, `uncertain.txt` as the review entry point, and `report.md` as the concise summary. Treat `results.csv`, `all-candidates.json`, and `excluded.txt` as audit/query artifacts that users need not read by default. In the final response, emphasize the primary path, counts, completion status, and material gaps.

## Source policy

- Core: official pages in the seed page's product/version/language scope.
- Core scope may span multiple official hosts. Admit a new host only when official navigation, canonical metadata, a Direct Citation, or other Authority Evidence links it to the same product/version/language scope; same-origin is not the governing boundary.
- Expansion: pages directly referenced by core pages, including official cases, research, examples, and external primary sources.
- Noise: clearly unrelated pages such as recruitment, login, sales, social sharing, and static assets.
- External does not mean noise; direct reference and relevance matter.
- Do not scan source repositories or reconstruct missing public documentation from code. Report the public-documentation gap instead. A repository explicitly cited in Core Source content may remain an ordinary Expansion Source candidate, without recursive code traversal.

Default recursion follows core sources until no new in-scope pages appear. Follow expansion sources one level only.

Deep does not widen the Collection Scope. It cross-checks discovery channels inside that scope and follows Direct Citations one level only. Report natural queue convergence separately from stopping at a budget limit.

Do not finish silently when discovery has not converged. Continue automatically when safe; otherwise enter `decision_required`, explain the user-visible coverage impact, and recommend an action. Export a partial result only after the user chooses to stop, or a constrained result when access rules make further progress impossible. Call a run complete only after natural convergence.

## Dynamic pages and limits

Use ordinary HTTP first. Fast never uses browser rendering. In Standard, use already available browser tooling when static content is clearly insufficient; when no browser capability exists, continue statically and report the coverage gap. In Deep, dynamic coverage is part of the mode promise: when the checkpoint reports `dynamic_capability_required`, ask the user to enable additional page-reading capability. After approval, prefer already available browser tooling, collect rendered links, save a JSON object keyed by source URL in the run directory, then resume from the existing checkpoint without refetching completed pages:

```powershell
python scripts/discover.py resume --run-dir <RUN_DIR> --rendered-links rendered-links.json
```

If no browser capability exists, explain the installation size, time, and location and request separate explicit approval to install it. After approval, install only into an isolated workspace environment; never modify the installed skill or global Python environment. Preserve the checkpoint if installation or rendering fails, then offer retry or a constrained export with the dynamic-coverage gap stated explicitly.

Use mode-specific default page limits: 100 for Fast, 500 for Standard, and 2,000 for Deep. These limits control crawl size and time, not model tokens. When the run reports `decision_required`, show the remaining queue, estimate the added time, explain the coverage impact, and recommend continuing or narrowing the scope. Resume with a larger approved ceiling via `python scripts/discover.py resume --run-dir <RUN_DIR> --max-pages <N>`. Never report completion while the queue remains unconverged. Use retries, backoff on 429, run-local evidence, and resumable state.

Checkpointing is required, not optional. Persist the pending queue, visited URLs, candidates and evidence, retry state, scope, mode, budget, classification progress, and user decisions after each crawl/classification batch, before `decision_required`, and before recoverable shutdown. Resume idempotently without refetching or reclassifying completed work; support retrying failed items separately.

Resume only incomplete runs in place. Treat completed runs as immutable records. A changed Collection Scope or Collection Mode starts a new run. User overrides append a final decision version without erasing the original rule/model judgment.

Keep the installed skill stateless: it contains reusable instructions, scripts, and static resources only. Create a unique run directory inside the current workspace for every execution; checkpoints, caches, candidates, classifications, and outputs belong to that run. Different agents/tasks remain isolated unless they explicitly resume the same run ID.

Use a single-writer run model. One invocation executes one run sequentially; any agent may later resume an interrupted run, but concurrent writes to the same run are unsupported. Concurrent invocations create separate run IDs. Do not add locking or distributed coordination in the current version.

## Failure contract

Discovery failure does not delete a candidate. Classification failure produces `uncertain`. The report must state discovery methods, counts, failures, and known blind spots. Never claim absolute completeness for an arbitrary site.

Retry timeouts, connection failures, 429 responses, and 5xx responses with backoff. Do not retry 404/410. Do not bypass 401/403, authentication, CAPTCHAs, or robots restrictions. Preserve exhausted failures with evidence, request a decision when they materially affect coverage, and allow failed items to be retried independently.

Retry only exhausted transient failures with `python scripts/discover.py resume --run-dir <RUN_DIR> --retry-failed`; completed pages are not fetched again.

Access limitation is not noise. Mark robots-blocked or otherwise inaccessible candidates as `access_limited`, not automatically excluded. An in-scope URL from an official sitemap may remain included with an unverified-content note; otherwise keep it uncertain.

## Completion checks

Do not call the first version complete until executable tests cover distinct Fast/Standard/Deep behavior and the pre-run token-consumption notice, checkpoint interruption/resume, robots/429/404 handling, dynamic fallback, grouped handling of broad ambiguity, at least two real documentation sites, and the output/completion-status contract. Prompt-only evals and basic unit tests are insufficient.

Keep tests lightweight: use unit tests and mocked HTTP responses for ordinary logic, a tiny Python-standard-library local page fixture for recursive discovery, and direct interruption/resume simulation for checkpoints. Do not deploy a test website. Use real documentation sites only as pre-release smoke tests so external redesigns are not mistaken for code regressions.
