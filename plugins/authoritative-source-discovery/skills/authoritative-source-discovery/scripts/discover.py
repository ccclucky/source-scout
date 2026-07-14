"""Stateful, deterministic discovery helper for Authoritative Source Discovery."""

from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import math
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

USER_AGENT = "AuthoritativeSourceDiscovery/1.0"
TRACKING_KEYS = {"gclid", "fbclid", "ref", "source"}
TRACKING_PREFIXES = ("utm_", "ref_", "mc_")
NON_SOURCE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css",
    ".js", ".woff", ".woff2", ".ttf", ".zip", ".tar", ".gz", ".mp4",
}
HARD_NOISE = re.compile(
    r"(?:^|/)(?:login|logout|signin|signup|account|cart|privacy|terms|careers?|jobs?|sales)(?:/|$)",
    re.I,
)
ALLOWED_DECISIONS = {"include", "exclude", "uncertain"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_CATEGORIES = {
    "documentation", "reference", "research", "example", "case", "blog",
    "marketing", "recruitment", "navigation", "other",
}
MODE_PAGE_LIMITS = {"fast": 100, "standard": 500, "deep": 2_000}
MAX_CLASSIFICATION_BATCH = 100
GROUP_UNCERTAIN_MINIMUM = 50
GROUP_UNCERTAIN_RATIO = 0.20
GROUP_PROPOSAL_SIZE = 3
GROUP_VALIDATION_MINIMUM = 3
GROUP_VALIDATION_MAXIMUM = 20
GROUP_FIELDS = {"host", "path", "provenance", "page_region", "title"}
GROUP_OPERATORS = {"equals", "prefix", "contains"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(value: str, base: str | None = None) -> str | None:
    absolute = urllib.parse.urljoin(base or value, value.strip())
    parts = urllib.parse.urlsplit(absolute)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    if Path(path).suffix.lower() in NON_SOURCE_EXTENSIONS:
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [
        (key, val) for key, val in query
        if key.lower() not in TRACKING_KEYS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urllib.parse.urlencode(sorted(query)), "")
    )


def candidate_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def same_origin(a: str, b: str) -> bool:
    return origin(a).lower() == origin(b).lower()


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, initial_url: str) -> None:
        super().__init__()
        self.allowed_origin = origin(initial_url).lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = normalize_url(newurl, req.full_url)
        if not target or origin(target).lower() != self.allowed_origin:
            raise urllib.error.HTTPError(req.full_url, 403, "cross-origin redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, target)


def scope_path(seed: str) -> str:
    path = urllib.parse.urlsplit(seed).path
    parent = path.rsplit("/", 1)[0] + "/"
    return parent if parent.startswith("/") else "/"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description = ""
        self.canonical = ""
        self.links: list[tuple[str, str, str]] = []
        self._in_title = False
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self._regions: list[str] = []

    @property
    def title(self) -> str:
        return clean_text(" ".join(self.title_parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in {"main", "article", "nav", "footer", "header"}:
            self._regions.append(tag)
        if tag == "a" and data.get("href"):
            self._anchor_href = data["href"]
            self._anchor_parts = []
        if tag == "meta":
            key = (data.get("name") or data.get("property") or "").lower()
            if key in {"description", "og:description"} and not self.description:
                self.description = clean_text(data.get("content", ""))
        if tag == "link" and "canonical" in data.get("rel", "").lower():
            self.canonical = data.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._anchor_href is not None:
            region = self._regions[-1] if self._regions else "unknown"
            self.links.append((self._anchor_href, clean_text(" ".join(self._anchor_parts)), region))
            self._anchor_href = None
            self._anchor_parts = []
        if tag in {"main", "article", "nav", "footer", "header"} and self._regions:
            for index in range(len(self._regions) - 1, -1, -1):
                if self._regions[index] == tag:
                    del self._regions[index]
                    break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)


@dataclass
class Candidate:
    id: str
    url: str
    discovered_from: str = ""
    discovery_method: str = ""
    depth: int = 0
    provenance: str = "official_direct"
    link_text: str = ""
    page_region: str = "unknown"
    title: str = ""
    description: str = ""
    canonical_url: str = ""
    declared_canonical_url: str = ""
    final_url: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    http_status: int | None = None
    content_type: str = ""
    fetched_at: str = ""
    content_hash: str = ""
    fetch_status: str = "discovered"
    decision: str = "pending"
    category: str = ""
    confidence: str = ""
    decision_basis: str = ""
    reason: str = ""
    decision_history: list[dict] = field(default_factory=list)


def fetch(url: str, retries: int = 3, timeout: int = 20) -> tuple[int, str, bytes, str]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            opener = urllib.request.build_opener(SameOriginRedirectHandler(url))
            with opener.open(request, timeout=timeout) as response:
                return (
                    getattr(response, "status", 200),
                    response.headers.get("Content-Type", ""),
                    response.read(2_000_000),
                    response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.05 * (2**attempt)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise
            time.sleep(0.05 * (2**attempt))
    assert last_error is not None
    raise last_error


def load_robots(seed: str) -> tuple[urllib.robotparser.RobotFileParser, bool, str]:
    robots_url = origin(seed) + "/robots.txt"
    parser = urllib.robotparser.RobotFileParser(robots_url)
    try:
        status, _, body, _ = fetch(robots_url, retries=1)
        if status == 200:
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            return parser, True, "loaded"
    except Exception as exc:
        return parser, False, type(exc).__name__
    return parser, False, "unavailable"


def sitemap_locations(seed: str) -> Iterable[str]:
    pending = [origin(seed) + suffix for suffix in ("/sitemap.xml", "/sitemap_index.xml")]
    seen: set[str] = set()
    while pending and len(seen) < 50:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        try:
            status, content_type, body, _ = fetch(sitemap_url, retries=1)
            if status != 200 or "xml" not in content_type.lower():
                continue
            root = ET.fromstring(body)
            locations = [
                node.text.strip() for node in root.iter()
                if node.tag.endswith("loc") and node.text and node.text.strip()
            ]
            if root.tag.endswith("sitemapindex"):
                for location in locations:
                    normalized = normalize_url(location, sitemap_url)
                    if normalized and same_origin(normalized, seed) and normalized not in seen:
                        pending.append(normalized)
            else:
                yield from locations
        except Exception:
            continue


def llms_locations(seed: str) -> Iterable[str]:
    for suffix in ("/llms.txt", "/llms-full.txt"):
        try:
            status, _, body, _ = fetch(origin(seed) + suffix, retries=1)
            if status != 200:
                continue
            text = body.decode("utf-8", errors="replace")
            yield from re.findall(r"https?://[^\s)>\]]+", text)
        except Exception:
            continue


def save_checkpoint(run_dir: Path, state: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    temporary = run_dir / "checkpoint.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(run_dir / "checkpoint.json")


def load_checkpoint(run_dir: Path) -> dict:
    path = run_dir / "checkpoint.json"
    if not path.exists():
        raise SystemExit(f"checkpoint not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def project_slug(seed: str) -> str:
    host = urllib.parse.urlsplit(seed).hostname or "sources"
    parts = [part for part in host.split(".") if part not in {"www", "docs", "com", "org", "net", "io"}]
    return re.sub(r"[^a-z0-9-]+", "-", (parts[0] if parts else "sources").lower())


def make_run_dir(output_root: str, seed: str, project: str | None) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    slug = project or project_slug(seed)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", slug):
        raise SystemExit("project must be a single safe slug using letters, numbers, underscore, or hyphen")
    root = Path(output_root).resolve()
    run_dir = (root / slug / stamp).resolve()
    if root not in run_dir.parents:
        raise SystemExit("project must stay inside output root")
    return run_dir


def candidate_map(state: dict) -> dict[str, dict]:
    return {row["url"]: row for row in state["candidates"]}


def add_candidate(
    state: dict, raw_url: str, source: str, method: str, depth: int,
    link_text: str = "", page_region: str = "unknown",
) -> dict | None:
    url = normalize_url(raw_url, source or state["seed_url"])
    if not url:
        return None
    existing = candidate_map(state).get(url)
    if existing:
        return existing
    provenance = "official_direct" if in_core_scope(state, url) else "official_reference"
    item = asdict(Candidate(
        id=candidate_id(url), url=url, discovered_from=source, discovery_method=method,
        depth=depth, provenance=provenance, link_text=link_text, page_region=page_region,
    ))
    if HARD_NOISE.search(urllib.parse.urlsplit(url).path):
        item.update(decision="exclude", category="navigation", confidence="high", decision_basis="rule", reason="operational or non-documentation path")
        item["decision_history"].append({
            "basis": "rule", "decision": "exclude", "confidence": "high",
            "reason": item["reason"], "at": now_iso(),
        })
    state["candidates"].append(item)
    return item


def queue_candidate(state: dict, item: dict, depth: int, kind: str = "core") -> None:
    if item["fetch_status"] != "discovered" or item["decision"] == "exclude":
        return
    if any(entry["url"] == item["url"] for entry in state["queue"]):
        return
    state["queue"].append({"url": item["url"], "depth": depth, "kind": kind})


def initialize_state(seed: str, mode: str, scope: str, scope_roots: list[str], max_pages: int, run_dir: Path) -> dict:
    if scope and not scope.startswith("/"):
        raise SystemExit("--scope must be a URL path prefix beginning with /")
    primary_path = scope.rstrip("/") or scope_path(seed).rstrip("/") or "/"
    normalized_roots = [origin(seed) + primary_path]
    for raw_root in scope_roots:
        normalized = normalize_url(raw_root)
        if not normalized:
            raise SystemExit(f"invalid --scope-root: {raw_root}")
        normalized_roots.append(normalized)
    return {
        "version": 2,
        "run_dir": str(run_dir),
        "seed_url": seed,
        "scope": scope,
        "scope_path": primary_path,
        "scope_roots": sorted(set(normalized_roots)),
        "scope_explicit": bool(scope),
        "mode": mode,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": "running",
        "discovery_status": "running",
        "decision_reason": "",
        "max_pages": max_pages,
        "fetched_pages": 0,
        "queue": [],
        "dynamic_pending": [],
        "candidates": [],
        "failures": [],
        "channels": {"seed": True, "sitemap": False, "llms": False, "static_links": False, "dynamic": "not_used"},
        "robots": {},
        "classification": {
            "submitted": 0,
            "active_batch_ids": [],
            "active_batch_chars": 0,
        },
    }


def prepare_discovery(state: dict) -> None:
    seed_item = add_candidate(state, state["seed_url"], "", "seed", 0)
    if seed_item:
        queue_candidate(state, seed_item, 0)
    for url in sitemap_locations(state["seed_url"]):
        item = add_candidate(state, url, state["seed_url"], "sitemap", 0)
        state["channels"]["sitemap"] = True
        if item and state["mode"] != "fast" and in_core_scope(state, item["url"]):
            queue_candidate(state, item, 0)
    for url in llms_locations(state["seed_url"]):
        item = add_candidate(state, url, state["seed_url"], "llms", 0)
        state["channels"]["llms"] = True
        if item and state["mode"] != "fast" and in_core_scope(state, item["url"]):
            queue_candidate(state, item, 0)


def in_core_scope(state: dict, url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.rstrip("/") or "/"
    for root in state.get("scope_roots", [origin(state["seed_url"]) + state["scope_path"]]):
        root_parts = urllib.parse.urlsplit(root)
        root_origin = f"{root_parts.scheme}://{root_parts.netloc}".lower()
        if origin(url).lower() != root_origin:
            continue
        prefix = root_parts.path.rstrip("/") or "/"
        if prefix == "/" or path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def mark_fetch_failure(item: dict, exc: Exception, state: dict) -> None:
    if isinstance(exc, urllib.error.HTTPError):
        item["http_status"] = exc.code
        if exc.code in {401, 403}:
            item["fetch_status"] = "access_limited"
            item["decision"] = "uncertain"
            item["reason"] = f"access limited: HTTP {exc.code}"
        elif exc.code in {404, 410}:
            item.update(fetch_status="invalid", decision="exclude", category="other", confidence="high", decision_basis="rule", reason=f"unavailable: HTTP {exc.code}")
        else:
            item["fetch_status"] = "failed"
            item["decision"] = "uncertain"
            item["reason"] = f"fetch failed after retries: HTTP {exc.code}"
    else:
        item["fetch_status"] = "failed"
        item["decision"] = "uncertain"
        item["reason"] = f"fetch failed after retries: {type(exc).__name__}"
    state["failures"].append({"url": item["url"], "error": item["reason"], "at": now_iso()})


def needs_dynamic_evidence(mode: str, parser: PageParser, html: str) -> bool:
    if mode == "fast":
        return False
    lowered = html.lower()
    empty_shell = not parser.links and "<script" in lowered and any(
        marker in lowered for marker in ("id='__next'", 'id="__next"', "id='app'", 'id="app"', "data-reactroot")
    )
    deep_dynamic_navigation = mode == "deep" and any(
        marker in lowered for marker in ("data-dynamic-nav", "__next_data__", "dynamic navigation")
    )
    return empty_shell or deep_dynamic_navigation


def crawl(state: dict, run_dir: Path) -> None:
    robots_cache: dict[str, tuple[urllib.robotparser.RobotFileParser, bool]] = {}
    while state["queue"] and state["fetched_pages"] < state["max_pages"]:
        entry = state["queue"].pop(0)
        items = candidate_map(state)
        item = items[entry["url"]]
        if item["fetch_status"] != "discovered":
            continue
        item_origin = origin(item["url"])
        if item_origin not in robots_cache:
            robots, robots_available, robots_status = load_robots(item["url"])
            robots_cache[item_origin] = (robots, robots_available)
            state.setdefault("robots", {})[item_origin] = {"available": robots_available, "status": robots_status}
        robots, robots_available = robots_cache[item_origin]
        if robots_available and not robots.can_fetch(USER_AGENT, item["url"]):
            if item["discovery_method"] == "sitemap" and in_core_scope(state, item["url"]):
                item.update(
                    fetch_status="access_limited", decision="include", category="documentation",
                    confidence="high", decision_basis="rule",
                    reason="official in-scope sitemap entry; content unverified because robots.txt blocks fetching",
                )
                item.setdefault("decision_history", []).append({
                    "basis": "rule", "decision": "include", "confidence": "high",
                    "reason": item["reason"], "at": now_iso(),
                })
            else:
                item.update(fetch_status="access_limited", decision="uncertain", reason="blocked by robots.txt")
            state["updated_at"] = now_iso()
            save_checkpoint(run_dir, state)
            continue
        try:
            status, content_type, body, final_url = fetch(item["url"])
            state["fetched_pages"] += 1
            item.update(
                http_status=status, content_type=content_type, fetched_at=now_iso(),
                content_hash=hashlib.sha256(body).hexdigest(), fetch_status="fetched",
                final_url=normalize_url(final_url) or final_url,
            )
            if item["final_url"] and item["final_url"] != item["url"]:
                item["redirect_chain"] = [item["url"], item["final_url"]]
            if "html" in content_type.lower():
                html = body.decode("utf-8", errors="replace")
                parser = PageParser()
                parser.feed(html)
                item["title"] = parser.title
                item["description"] = parser.description
                if parser.canonical:
                    declared = normalize_url(parser.canonical, item["url"]) or ""
                    item["declared_canonical_url"] = declared
                    if declared and in_core_scope(state, declared):
                        item["canonical_url"] = declared
                state["channels"]["static_links"] = True
                if needs_dynamic_evidence(state["mode"], parser, html):
                    if item["url"] not in state["dynamic_pending"]:
                        state["dynamic_pending"].append(item["url"])
                    state["channels"]["dynamic"] = "required"
                for href, text, region in parser.links:
                    child = add_candidate(state, href, item["url"], "page_link", entry["depth"] + 1, text, region)
                    if not child or state["mode"] == "fast":
                        continue
                    if in_core_scope(state, child["url"]):
                        queue_candidate(state, child, entry["depth"] + 1, "core")
                    elif (
                        not state.get("scope_explicit")
                        and same_origin(child["url"], state["seed_url"])
                        and entry["depth"] == 0
                        and region in {"main", "article"}
                    ):
                        queue_candidate(state, child, entry["depth"] + 1, "direct_citation")
            else:
                item["reason"] = "source artifact; not a discovery page"
        except Exception as exc:
            mark_fetch_failure(item, exc, state)
        state["updated_at"] = now_iso()
        save_checkpoint(run_dir, state)

    if state["queue"]:
        state["status"] = "decision_required"
        state["discovery_status"] = "budget_limited"
        state["decision_reason"] = "budget_limited"
    elif state.get("dynamic_pending"):
        state["status"] = "decision_required"
        state["discovery_status"] = "capability_limited"
        state["decision_reason"] = "dynamic_capability_required"
    elif any(item["fetch_status"] == "failed" for item in state["candidates"]):
        state["status"] = "decision_required"
        state["discovery_status"] = "failure_limited"
        state["decision_reason"] = "fetch_failures"
    else:
        state["discovery_status"] = "converged"
        state["status"] = "classification_required" if any(
            item["decision"] == "pending" for item in state["candidates"]
        ) else "ready_to_export"
        state["decision_reason"] = ""
    state["updated_at"] = now_iso()
    save_checkpoint(run_dir, state)


def validate_public_url(url: str, allow_private: bool = False) -> None:
    """Reject local/private targets unless explicitly enabled for fixture testing."""
    if allow_private:
        return
    hostname = urllib.parse.urlsplit(url).hostname
    if not hostname:
        raise SystemExit("a public http(s) seed URL is required")
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as error:
        raise SystemExit(f"cannot resolve public URL host: {hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise SystemExit(f"public URL required; private or local address rejected: {hostname}")


def ensure_mutable(state: dict) -> None:
    if state["status"] in {"completed", "partial_completed"}:
        raise SystemExit("completed run is immutable; create a new run instead")


def start_command(args: argparse.Namespace) -> None:
    seed = normalize_url(args.url)
    if not seed:
        raise SystemExit("a public http(s) seed URL is required")
    validate_public_url(seed, args.allow_private)
    for scope_root in args.scope_root:
        normalized_root = normalize_url(scope_root)
        if not normalized_root:
            raise SystemExit(f"invalid --scope-root: {scope_root}")
        validate_public_url(normalized_root, args.allow_private)
    run_dir = make_run_dir(args.output_root, seed, args.project)
    max_pages = args.max_pages if args.max_pages is not None else MODE_PAGE_LIMITS[args.mode]
    state = initialize_state(seed, args.mode, args.scope, args.scope_root, max_pages, run_dir)
    save_checkpoint(run_dir, state)
    prepare_discovery(state)
    save_checkpoint(run_dir, state)
    crawl(state, run_dir)
    print(json.dumps(summary(state), ensure_ascii=False))


def resume_command(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    if args.rendered_links:
        ingest_rendered_links(state, Path(args.rendered_links))
        save_checkpoint(run_dir, state)
    if args.retry_failed:
        for item in state["candidates"]:
            if item["fetch_status"] != "failed":
                continue
            item.update(fetch_status="discovered", decision="pending", category="", confidence="", decision_basis="", reason="")
            queue_candidate(state, item, int(item.get("depth", 0)), "retry")
        save_checkpoint(run_dir, state)
    if args.max_pages is not None:
        if args.max_pages <= state["fetched_pages"]:
            raise SystemExit("--max-pages must exceed pages already fetched")
        state["max_pages"] = args.max_pages
    state["status"] = "running"
    state["decision_reason"] = ""
    crawl(state, run_dir)
    print(json.dumps(summary(state), ensure_ascii=False))


def ingest_rendered_links(state: dict, path: Path) -> None:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise SystemExit("rendered links input must be an object keyed by source URL")
    pending = set(state.get("dynamic_pending", []))
    for source, links in evidence.items():
        normalized_source = normalize_url(source)
        if not normalized_source or normalized_source not in pending:
            raise SystemExit(f"rendered evidence source is not pending: {source}")
        if not isinstance(links, list):
            raise SystemExit(f"rendered links must be an array for: {source}")
        source_item = candidate_map(state).get(normalized_source)
        depth = int(source_item.get("depth", 0)) if source_item else 0
        for raw_link in links:
            item = add_candidate(state, str(raw_link), normalized_source, "rendered_link", depth + 1, page_region="main")
            if item and in_core_scope(state, item["url"]):
                queue_candidate(state, item, depth + 1, "core")
        pending.remove(normalized_source)
    state["dynamic_pending"] = sorted(pending)
    state["channels"]["dynamic"] = "used"


def summary(state: dict) -> dict:
    counts: dict[str, int] = {}
    for item in state["candidates"]:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    return {
        "run_dir": state["run_dir"], "status": state["status"],
        "discovery_status": state.get("discovery_status", "unknown"),
        "decision_reason": state.get("decision_reason", ""),
        "discovered": len(state["candidates"]), "fetched": state["fetched_pages"],
        **counts,
    }


def status_command(args: argparse.Namespace) -> None:
    print(json.dumps(summary(load_checkpoint(Path(args.run_dir).resolve())), ensure_ascii=False, indent=2))


def classification_next(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    pending = [item for item in state["candidates"] if item["decision"] == "pending"]
    if not 1 <= args.limit <= MAX_CLASSIFICATION_BATCH:
        raise SystemExit(f"--limit must be between 1 and {MAX_CLASSIFICATION_BATCH}")
    classification = state.setdefault("classification", {
        "submitted": 0, "active_batch_ids": [], "active_batch_chars": 0,
    })
    active_ids = classification.get("active_batch_ids", [])
    if active_ids:
        active_set = set(active_ids)
        batch = [item for item in pending if item["id"] in active_set]
    else:
        batch = pending[:args.limit]
        batch_chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in batch)
        classification["active_batch_ids"] = [item["id"] for item in batch]
        classification["active_batch_chars"] = batch_chars
    save_checkpoint(run_dir, state)
    fields = (
        "id", "url", "title", "description", "link_text", "page_region",
        "discovered_from", "discovery_method", "depth", "provenance", "content_type",
    )
    print(json.dumps({
        "items": [{key: item.get(key) for key in fields} for item in batch],
        "returned": len(batch), "remaining": max(0, len(pending) - len(batch)),
        "evidence_chars_issued": classification.get("active_batch_chars", 0),
    }, ensure_ascii=False, indent=2))


def group_field_value(item: dict, field: str) -> str:
    if field == "host":
        return urllib.parse.urlsplit(item["url"]).netloc.lower()
    if field == "path":
        return urllib.parse.urlsplit(item["url"]).path
    return clean_text(str(item.get(field, "")))


def group_condition_matches(item: dict, condition: dict) -> bool:
    actual = group_field_value(item, condition["field"])
    expected = condition["value"]
    if condition["operator"] == "equals":
        return actual == expected
    if condition["operator"] == "prefix":
        return actual.startswith(expected)
    return expected in actual


def spread_sample(items: list[dict], count: int) -> list[dict]:
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[0]]
    indices = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    return [items[index] for index in indices]


def classification_group_next(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    if args.field not in GROUP_FIELDS:
        raise SystemExit(f"--field must be one of: {', '.join(sorted(GROUP_FIELDS))}")
    if args.operator not in GROUP_OPERATORS:
        raise SystemExit(f"--operator must be one of: {', '.join(sorted(GROUP_OPERATORS))}")
    value = clean_text(args.value)
    if not value:
        raise SystemExit("--value is required")
    classified = [item for item in state["candidates"] if item["decision"] != "pending"]
    uncertain = [item for item in classified if item["decision"] == "uncertain"]
    if len(uncertain) < GROUP_UNCERTAIN_MINIMUM or len(uncertain) / max(1, len(classified)) < GROUP_UNCERTAIN_RATIO:
        raise SystemExit("grouped review requires at least 50 uncertain candidates and a 20% uncertain rate")
    condition = {"field": args.field, "operator": args.operator, "value": value}
    classification = state.setdefault("classification", {})
    parent = None
    if args.parent_rule_id:
        parent = next((rule for rule in classification.get("group_rules", []) if rule["rule_id"] == args.parent_rule_id), None)
        if not parent or parent.get("status") != "validation_failed":
            raise SystemExit("parent rule must be a failed validated group rule")
        if int(parent.get("split_depth", 0)) >= 1:
            raise SystemExit("a failed group may be split only once")
    allowed_ids = set(parent["matched_ids"]) if parent else None
    matched = sorted((
        item for item in uncertain
        if (allowed_ids is None or item["id"] in allowed_ids) and group_condition_matches(item, condition)
    ), key=lambda item: item["url"])
    if parent and len(matched) >= len(parent["matched_ids"]):
        raise SystemExit("a split rule must select a smaller subgroup")
    if len(matched) < GROUP_PROPOSAL_SIZE + GROUP_VALIDATION_MINIMUM:
        raise SystemExit("grouped review requires at least 6 matching uncertain candidates")
    validation_count = min(GROUP_VALIDATION_MAXIMUM, max(GROUP_VALIDATION_MINIMUM, math.ceil(len(matched) * 0.10)))
    selected = spread_sample(matched, GROUP_PROPOSAL_SIZE + validation_count)
    proposal = selected[:GROUP_PROPOSAL_SIZE]
    validation = selected[GROUP_PROPOSAL_SIZE:]
    identity = {"condition": condition, "parent_rule_id": args.parent_rule_id or ""}
    rule_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    split_depth = int(parent.get("split_depth", 0)) + 1 if parent else 0
    classification["active_group"] = {
        "rule_id": rule_id, "condition": condition,
        "matched_ids": [item["id"] for item in matched],
        "proposal_ids": [item["id"] for item in proposal],
        "validation_ids": [item["id"] for item in validation],
        "split_depth": split_depth, "parent_rule_id": args.parent_rule_id or "",
    }
    save_checkpoint(run_dir, state)
    fields = ("id", "url", "title", "discovered_from", "provenance", "page_region")
    compact = lambda item: {key: item.get(key) for key in fields}
    print(json.dumps({
        "rule_id": rule_id, "condition": condition, "matched": len(matched), "split_depth": split_depth,
        "proposal_items": [compact(item) for item in proposal],
        "validation_items": [compact(item) for item in validation],
    }, ensure_ascii=False, indent=2))


def classification_group_submit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("group submission must be a JSON object")
    classification = state.setdefault("classification", {})
    active = classification.get("active_group")
    if not active or payload.get("rule_id") != active.get("rule_id"):
        raise SystemExit("group submission does not match the active rule")
    decision = payload.get("decision")
    category = payload.get("category")
    reason = clean_text(str(payload.get("reason", "")))
    if decision not in {"include", "exclude"}:
        raise SystemExit("group rule decision must be include or exclude")
    if category not in ALLOWED_CATEGORIES:
        raise SystemExit("invalid group rule category")
    if not reason:
        raise SystemExit("group rule reason is required")
    rows = payload.get("sample_decisions")
    if not isinstance(rows, list):
        raise SystemExit("sample_decisions must be a JSON array")
    required_ids = set(active["proposal_ids"] + active["validation_ids"])
    supplied_ids = {row.get("id") for row in rows if isinstance(row, dict)}
    if supplied_ids != required_ids or len(rows) != len(required_ids):
        raise SystemExit("all proposal and validation samples must be submitted exactly once")
    for row in rows:
        if row.get("decision") not in {"include", "exclude"} or row.get("confidence") != "high":
            raise SystemExit("every sample must have an include/exclude decision at high confidence")
        if not clean_text(str(row.get("reason", ""))):
            raise SystemExit("every sample decision requires a reason")
    if any(row["decision"] != decision for row in rows):
        timestamp = now_iso()
        classification.setdefault("group_rules", []).append({
            **active, "decision": decision, "category": category, "reason": reason,
            "sample_decisions": rows, "status": "validation_failed", "applied": 0, "at": timestamp,
        })
        classification.pop("active_group", None)
        state["updated_at"] = timestamp
        save_checkpoint(run_dir, state)
        print(json.dumps({"rule_id": active["rule_id"], "status": "validation_failed", "applied": 0}))
        return
    by_id = {item["id"]: item for item in state["candidates"]}
    applied = 0
    timestamp = now_iso()
    for item_id in active["matched_ids"]:
        item = by_id.get(item_id)
        if not item or item["decision"] != "uncertain" or not group_condition_matches(item, active["condition"]):
            continue
        item.setdefault("decision_history", []).append({
            "basis": "rule", "decision": decision, "confidence": "high",
            "reason": reason, "rule_id": active["rule_id"], "at": timestamp,
        })
        item.update(
            decision=decision, category=category, confidence="high",
            decision_basis="rule", reason=reason,
        )
        applied += 1
    classification.setdefault("group_rules", []).append({
        **active, "decision": decision, "category": category, "reason": reason,
        "sample_decisions": rows, "status": "applied", "applied": applied, "at": timestamp,
    })
    classification.pop("active_group", None)
    state["updated_at"] = timestamp
    save_checkpoint(run_dir, state)
    print(json.dumps({"rule_id": active["rule_id"], "applied": applied}))


def validate_decisions(rows: object, batch_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(rows, list):
        return ["classification input must be a JSON array"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append(f"decision is not an object: {row!r}")
            continue
        item_id = row.get("id")
        if item_id not in batch_ids:
            errors.append(f"unknown or non-pending id: {item_id}")
        if item_id in seen:
            errors.append(f"duplicate id: {item_id}")
        seen.add(item_id)
        decision = row.get("decision")
        confidence = row.get("confidence")
        if decision not in ALLOWED_DECISIONS:
            errors.append(f"invalid decision for {item_id}")
        if confidence not in ALLOWED_CONFIDENCE:
            errors.append(f"invalid confidence for {item_id}")
        if decision in {"include", "exclude"} and confidence in {"medium", "low"}:
            errors.append(f"medium/low confidence must be uncertain for {item_id}")
        if row.get("category") not in ALLOWED_CATEGORIES:
            errors.append(f"invalid category for {item_id}")
        if not clean_text(str(row.get("reason", ""))):
            errors.append(f"missing reason for {item_id}")
    return errors


def classification_submit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    pending_by_id = {item["id"]: item for item in state["candidates"] if item["decision"] == "pending"}
    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    classification = state.setdefault("classification", {
        "submitted": 0, "active_batch_ids": [], "active_batch_chars": 0,
    })
    active_ids = set(classification.get("active_batch_ids", []))
    supplied_ids = {row.get("id") for row in rows} if isinstance(rows, list) else set()
    errors = validate_decisions(rows, active_ids)
    if not active_ids or supplied_ids != active_ids:
        errors.append("batch ids must be submitted exactly once")
    if errors:
        raise SystemExit("classification validation failed:\n- " + "\n- ".join(errors))
    for row in rows:
        item = pending_by_id[row["id"]]
        item.update(
            decision=row["decision"], category=row["category"], confidence=row["confidence"],
            decision_basis="model", reason=clean_text(row["reason"]),
        )
        item.setdefault("decision_history", []).append({
            "basis": "model", "decision": row["decision"], "confidence": row["confidence"],
            "reason": clean_text(row["reason"]), "at": now_iso(),
        })
    classification["submitted"] += len(rows)
    classification["active_batch_ids"] = []
    classification["active_batch_chars"] = 0
    state["updated_at"] = now_iso()
    save_checkpoint(run_dir, state)
    remaining = sum(item["decision"] == "pending" for item in state["candidates"])
    if remaining == 0 and state.get("discovery_status") == "converged":
        state["status"] = "ready_to_export"
        state["decision_reason"] = ""
        save_checkpoint(run_dir, state)
    print(json.dumps({"accepted": len(rows), "remaining": remaining}))


def classification_override(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    item = next((row for row in state["candidates"] if row["id"] == args.id), None)
    if item is None:
        raise SystemExit(f"unknown candidate id: {args.id}")
    reason = clean_text(args.reason)
    if not reason:
        raise SystemExit("override reason is required")
    item.setdefault("decision_history", []).append({
        "basis": "user", "decision": args.decision, "confidence": "high",
        "reason": reason, "at": now_iso(),
    })
    item.update(decision=args.decision, confidence="high", decision_basis="user", reason=reason)
    state["updated_at"] = now_iso()
    save_checkpoint(run_dir, state)
    print(json.dumps({"id": args.id, "decision": args.decision, "decision_basis": "user"}))


def export_command(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    state = load_checkpoint(run_dir)
    ensure_mutable(state)
    pending_count = sum(item["decision"] == "pending" for item in state["candidates"])
    if pending_count and not args.partial:
        raise SystemExit("pending classifications remain; resolve them or use --partial after the user chooses to stop")
    if state.get("discovery_status") != "converged" and not args.partial:
        raise SystemExit("discovery has not converged; resume it or use --partial after the user chooses to stop")
    groups: dict[str, list[dict]] = {"include": [], "exclude": [], "uncertain": []}
    for item in state["candidates"]:
        decision = item["decision"] if item["decision"] != "pending" else "uncertain"
        groups[decision].append(item)
    included_urls = sorted({
        item["canonical_url"] or item["final_url"] or item["url"]
        for item in groups["include"]
    })
    (run_dir / "urls.txt").write_text("".join(f"{url}\n" for url in included_urls), encoding="utf-8")
    (run_dir / "uncertain.txt").write_text("".join(f"{item['url']}\t{item.get('reason') or 'needs review'}\n" for item in sorted(groups["uncertain"], key=lambda row: row["url"])), encoding="utf-8")
    (run_dir / "excluded.txt").write_text("".join(f"{item['url']}\t{item.get('reason', '')}\n" for item in sorted(groups["exclude"], key=lambda row: row["url"])), encoding="utf-8")
    fields = list(Candidate.__dataclass_fields__)
    with (run_dir / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({
            key: json.dumps(item.get(key), ensure_ascii=False) if key in {"decision_history", "redirect_chain"} else item.get(key, "")
            for key in fields
        } for item in state["candidates"])
    report = [
        "# Source discovery report", "", f"- Seed: {state['seed_url']}",
        f"- Mode: {state['mode']}", f"- Status: {'partial_completed' if args.partial else 'completed'}",
        f"- Discovery status: {state.get('discovery_status', 'unknown')}",
        f"- Decision reason: {state.get('decision_reason') or '(none)'}", "",
        "## Results", "", f"- Included URLs: {len(included_urls)}",
        f"- Included candidate records: {len(groups['include'])}",
        f"- Needs review: {len(groups['uncertain'])}", f"- Excluded: {len(groups['exclude'])}", "",
        "## Coverage", "", f"- Discovery channels: {json.dumps(state['channels'], ensure_ascii=False)}",
        f"- Pages fetched: {state['fetched_pages']} / {state['max_pages']}",
        f"- Fetch failures: {len(state['failures'])}",
    ]
    if args.partial:
        report.extend(["", "Further user decision is required before this run can be called complete."])
    (run_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if state["mode"] == "deep":
        (run_dir / "all-candidates.json").write_text(json.dumps(state["candidates"], ensure_ascii=False, indent=2), encoding="utf-8")
    state["status"] = "partial_completed" if args.partial else "completed"
    state["decision_reason"] = "user_stopped" if args.partial else ""
    state["updated_at"] = now_iso()
    save_checkpoint(run_dir, state)
    print(json.dumps({
        "run_dir": str(run_dir), "status": state["status"],
        "include": len(included_urls), "include_candidates": len(groups["include"]),
        "exclude": len(groups["exclude"]), "uncertain": len(groups["uncertain"]),
    }, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover authoritative documentation sources")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--url", required=True)
    start.add_argument("--mode", choices=("fast", "standard", "deep"), default="standard")
    start.add_argument("--scope", default="")
    start.add_argument("--scope-root", action="append", default=[], help="Additional verified official URL root; repeatable")
    start.add_argument("--output-root", default="output")
    start.add_argument("--project")
    start.add_argument("--max-pages", type=int)
    start.add_argument("--allow-private", action="store_true", help=argparse.SUPPRESS)
    start.set_defaults(func=start_command)
    resume = commands.add_parser("resume")
    resume.add_argument("--run-dir", required=True)
    resume.add_argument("--max-pages", type=int)
    resume.add_argument("--rendered-links", type=Path)
    resume.add_argument("--retry-failed", action="store_true")
    resume.set_defaults(func=resume_command)
    status = commands.add_parser("status")
    status.add_argument("--run-dir", required=True)
    status.set_defaults(func=status_command)
    classify = commands.add_parser("classify")
    classify_commands = classify.add_subparsers(dest="classify_command", required=True)
    next_batch = classify_commands.add_parser("next")
    next_batch.add_argument("--run-dir", required=True)
    next_batch.add_argument("--limit", type=int, default=50)
    next_batch.set_defaults(func=classification_next)
    submit = classify_commands.add_parser("submit")
    submit.add_argument("--run-dir", required=True)
    submit.add_argument("--input", required=True)
    submit.set_defaults(func=classification_submit)
    group_next = classify_commands.add_parser("group-next")
    group_next.add_argument("--run-dir", required=True)
    group_next.add_argument("--field", required=True)
    group_next.add_argument("--operator", required=True)
    group_next.add_argument("--value", required=True)
    group_next.add_argument("--parent-rule-id")
    group_next.set_defaults(func=classification_group_next)
    group_submit = classify_commands.add_parser("group-submit")
    group_submit.add_argument("--run-dir", required=True)
    group_submit.add_argument("--input", required=True)
    group_submit.set_defaults(func=classification_group_submit)
    override = classify_commands.add_parser("override")
    override.add_argument("--run-dir", required=True)
    override.add_argument("--id", required=True)
    override.add_argument("--decision", choices=tuple(sorted(ALLOWED_DECISIONS)), required=True)
    override.add_argument("--reason", required=True)
    override.set_defaults(func=classification_override)
    export = commands.add_parser("export")
    export.add_argument("--run-dir", required=True)
    export.add_argument("--partial", action="store_true", help="Export after the user explicitly chooses to stop before completion")
    export.set_defaults(func=export_command)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
