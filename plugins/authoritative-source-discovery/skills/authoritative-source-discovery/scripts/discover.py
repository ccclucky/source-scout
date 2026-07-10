"""Deterministic candidate discovery and output helpers for the skill."""

from __future__ import annotations

import argparse
import csv
import html.parser
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from pathlib import Path

TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ref"}


def canonicalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, val) for key, val in query if key.lower() not in TRACKING_KEYS]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(query), ""))


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag.lower() == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"] or "")
        self._in_title = tag.lower() == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data.strip())


def robots_allows(robots: urllib.robotparser.RobotFileParser | None, available: bool, url: str) -> bool:
    return True if not available or robots is None else robots.can_fetch("*", url)


def open_with_retries(request, opener=urllib.request.urlopen, retries: int = 3, delay: float = 0.5):
    last_error = None
    for attempt in range(retries):
        try:
            return opener(request, timeout=20)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def discover(seed: str, limit: int = 500, delay: float = 0.2) -> list[dict]:
    seed = canonicalize_url(seed)
    parsed_seed = urllib.parse.urlsplit(seed)
    root = parsed_seed.path.rsplit("/", 1)[0] + "/"
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(f"{parsed_seed.scheme}://{parsed_seed.netloc}/robots.txt")
    robots_available = True
    try:
        robots.read()
    except Exception:
        robots_available = False
    queue = deque([seed])
    seen: set[str] = set()
    results: list[dict] = []
    while queue and len(results) < limit:
        url = canonicalize_url(queue.popleft())
        if url in seen or urllib.parse.urlsplit(url).netloc != parsed_seed.netloc:
            continue
        seen.add(url)
        if not robots_allows(robots, robots_available, url):
            results.append({"url": url, "decision": "uncertain", "reason": "blocked_by_robots", "discovery_method": "crawl"})
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "authoritative-source-discovery/0.1"})
            with open_with_retries(request) as response:
                content_type = response.headers.get("content-type", "")
                body = response.read(2_000_000).decode("utf-8", errors="replace")
                status = getattr(response, "status", 200)
            item = {"url": url, "decision": "uncertain", "reason": "needs_llm_classification", "discovery_method": "crawl", "status": status}
            if "text/html" in content_type:
                parser = LinkParser()
                parser.feed(body)
                item["title"] = " ".join(parser.title).strip()
                item["links_found"] = len(parser.links)
                for href in parser.links:
                    child = canonicalize_url(urllib.parse.urljoin(url, href))
                    child_parsed = urllib.parse.urlsplit(child)
                    if child_parsed.netloc == parsed_seed.netloc and child_parsed.path.startswith(root):
                        if child not in seen:
                            queue.append(child)
            results.append(item)
        except Exception as exc:
            results.append({"url": url, "decision": "uncertain", "reason": f"fetch_failed: {type(exc).__name__}", "discovery_method": "crawl"})
        time.sleep(delay)
    return results


def write_outputs(output_dir: Path, results: list[dict], metadata: dict) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    included = [r["url"] for r in results if r.get("decision") == "include"]
    uncertain = [r["url"] for r in results if r.get("decision") == "uncertain"]
    excluded = [f"{r['url']}\t{r.get('reason', '')}" for r in results if r.get("decision") == "exclude"]
    (output_dir / "urls.txt").write_text("".join(f"{url}\n" for url in included), encoding="utf-8")
    (output_dir / "uncertain.txt").write_text("".join(f"{url}\n" for url in uncertain), encoding="utf-8")
    (output_dir / "excluded.txt").write_text("".join(f"{line}\n" for line in excluded), encoding="utf-8")
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in results for key in row}) or ["url", "decision", "reason"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in results)
    report = "# Collection report\n\n" + "\n".join(f"{key}: {value}" for key, value in metadata.items()) + "\n"
    report += f"included: {len(included)}\nuncertain: {len(uncertain)}\nexcluded: {len(excluded)}\n"
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    return ["urls.txt", "uncertain.txt", "excluded.txt", "results.csv", "report.md"]


def build_discovery_metadata(seed: str, mode: str, results: list[dict], limit: int) -> dict:
    reached = len(results) >= limit
    return {
        "mode": mode,
        "candidate_count": len(results),
        "seed": seed,
        "page_limit": limit,
        "page_limit_reached": reached,
        "coverage_status": "incomplete_limit_reached" if reached else "queue_exhausted",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover candidate URLs from a public seed page.")
    parser.add_argument("seed", nargs="?")
    parser.add_argument("--mode", choices=("fast", "standard", "deep"), default="standard")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--export-decisions", type=Path, help="Export a JSON classification list produced by the LLM.")
    args = parser.parse_args()
    if args.export_decisions:
        results = json.loads(args.export_decisions.read_text(encoding="utf-8"))
        write_outputs(args.output, results, {"mode": args.mode, "candidate_count": len(results), "source": str(args.export_decisions)})
        return
    if not args.seed:
        parser.error("seed is required unless --export-decisions is provided")
    results = discover(args.seed, limit=args.limit, delay=0.0 if args.mode == "fast" else 0.2)
    write_outputs(args.output, results, build_discovery_metadata(args.seed, args.mode, results, args.limit))


if __name__ == "__main__":
    main()
