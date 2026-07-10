---
name: authoritative-source-discovery
description: Discover and organize official or directly referenced web sources from a supplied seed URL while reducing irrelevant noise. Use when a user wants documentation, research, examples, cases, or other authoritative links collected from an official site for NotebookLM or another downstream tool.
---

# Authoritative Source Discovery

## Quick start

Input: one public seed URL, plus optional scope requirements (version, language, product, content to include/exclude).

Before running, explain and let the user choose:

| Mode | Speed | Token cost | Coverage |
|---|---|---|---|
| Fast | highest | lowest | basic indexes and static links |
| Standard | medium | medium | core recursion plus semantic review (default) |
| Deep | lowest | highest | dynamic fallback, repository and wider references |

Show expected page count, time, and relative cost. Do not silently upgrade modes.

## Workflow

1. Infer a provisional scope from the seed URL, title, path, breadcrumbs, and navigation. Confirm it when ambiguous; never assume the whole domain is in scope.
2. Run the bundled crawler:

   ```powershell
   python scripts/discover.py <SEED_URL> --mode <fast|standard|deep> --output output/<project>/<timestamp>
   ```

   It uses public HTTP first, normalizes URLs, removes duplicates, respects `robots.txt`, records failures, and stays within the requested scope where it can infer one.
3. Treat crawler output as candidates, not final truth. Keep all candidates traceable.
4. Apply deterministic cleanup only to clear junk (assets, mailto/javascript links, fragments, tracking variants, duplicates, and unretrievable records). Do not discard blogs, cases, research, or external links solely by path or domain.
5. Classify remaining candidates in batches using the page metadata and link context returned by the crawler. For each candidate choose exactly one: `include`, `exclude`, or `uncertain`; add a category and short reason. Do not send thousands of URLs or full pages in one prompt.
6. Retry malformed or failed classification once. If still unresolved, keep `uncertain`; never silently discard it. Ask the user to review uncertain items when they are material.
7. Save the LLM decisions as JSON, then export:

   ```powershell
   python scripts/discover.py --export-decisions decisions.json --output output/<project>/<timestamp>
   ```

   `urls.txt` contains only confirmed included URLs; `uncertain.txt`, `excluded.txt`, `results.csv`, and `report.md` preserve the audit trail. Deep/debug runs may also keep `all-candidates.json`.

## Source policy

- Core: official pages in the seed page's product/version/language scope.
- Expansion: pages directly referenced by core pages, including official cases, research, examples, and external primary sources.
- Noise: clearly unrelated pages such as recruitment, login, sales, social sharing, and static assets.
- External does not mean noise; direct reference and relevance matter.

Default recursion follows core sources until no new in-scope pages appear. Follow expansion sources one level only.

## Dynamic pages and limits

Use ordinary HTTP first. If content or links require JavaScript, detect available browser tooling and use it only in Standard/Deep. If unavailable, continue statically and report the coverage risk; install Playwright only after user approval.

Use a 500-page default limit. At 501–2,000 pages, show the estimate and ask before continuing; above 2,000, pause and suggest narrowing or batching. Use polite concurrency, retries, backoff on 429, caching, and resumable state.

## Failure contract

Discovery failure does not delete a candidate. Classification failure produces `uncertain`. The report must state discovery methods, counts, failures, and known blind spots. Never claim absolute completeness for an arbitrary site.
