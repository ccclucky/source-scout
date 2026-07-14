import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "discover.py"


class FixtureSite:
    def __init__(self, extra_sitemap_count=0, dynamic_shell=False, sitemap_index=False, persistent_failure=False, unsafe_targets=False, blocked_sitemap=False, official_external_url=None):
        self.requests: list[str] = []
        self.fail_once = True
        self.extra_sitemap_count = extra_sitemap_count
        self.dynamic_shell = dynamic_shell
        self.sitemap_index = sitemap_index
        self.persistent_failure = persistent_failure
        self.unsafe_targets = unsafe_targets
        self.blocked_sitemap = blocked_sitemap
        self.official_external_url = official_external_url
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                outer.requests.append(path)
                if path == "/robots.txt":
                    blocked = "Disallow: /docs/blocked-doc\n" if outer.blocked_sitemap else ""
                    return self.reply("text/plain", "User-agent: *\nDisallow: /blocked\n" + blocked)
                if path == "/sitemap.xml":
                    if outer.sitemap_index:
                        self.send_response(404)
                        self.end_headers()
                        return
                    extra = "".join(
                        f"<url><loc>{outer.origin}/docs/api/group-{index % 3}/generated-{index}</loc></url>"
                        for index in range(outer.extra_sitemap_count)
                    )
                    if outer.blocked_sitemap:
                        extra += f"<url><loc>{outer.origin}/docs/blocked-doc</loc></url>"
                    return self.reply(
                        "application/xml",
                        f"<urlset><url><loc>{outer.origin}/docs/start</loc></url>"
                        f"<url><loc>{outer.origin}/docs/guide</loc></url>{extra}</urlset>",
                    )
                if path == "/sitemap_index.xml" and outer.sitemap_index:
                    return self.reply(
                        "application/xml",
                        f"<sitemapindex><sitemap><loc>{outer.origin}/docs-sitemap.xml</loc></sitemap></sitemapindex>",
                    )
                if path == "/docs-sitemap.xml":
                    return self.reply(
                        "application/xml",
                        f"<urlset><url><loc>{outer.origin}/docs/dynamic-guide</loc></url></urlset>",
                    )
                if path == "/docs/start":
                    if outer.dynamic_shell:
                        return self.reply(
                            "text/html",
                            "<html><head><title>Start</title></head><body>"
                            "<div id='__next' data-dynamic-nav='true'></div><script>renderDocs()</script>"
                            "</body></html>",
                        )
                    return self.reply(
                        "text/html",
                        "<html><head><title>Start</title></head><body><main>"
                        "<a href='/docs/guide'>Guide</a>"
                        "<a href='/docs/alias'>Alias</a>"
                        + ("<a href='/docs/external-redirect'>Redirect</a>" if outer.unsafe_targets else "") +
                        "<a href='/docs/retry'>Retry</a>"
                        + ("<a href='/docs/fail'>Fail</a>" if outer.persistent_failure else "") +
                        (f"<a href='{outer.official_external_url}'>Official API</a>" if outer.official_external_url else "") +
                        "<a href='/blocked'>Blocked</a>"
                        "<a href='https://example.org/paper.pdf'>Paper</a>"
                        "</main></body></html>",
                    )
                if path == "/docs/guide":
                    return self.reply("text/html", "<html><head><title>Guide</title></head><body>Guide</body></html>")
                if path == "/docs/alias":
                    canonical = "https://evil.example/x" if outer.unsafe_targets else "/docs/guide"
                    return self.reply("text/html", f"<html><head><title>Alias</title><link rel='canonical' href='{canonical}'></head><body>Alias</body></html>")
                if path == "/docs/external-redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example/redirected")
                    self.end_headers()
                    return
                if path == "/docs/dynamic-guide":
                    return self.reply("text/html", "<html><head><title>Dynamic Guide</title></head><body>Guide</body></html>")
                if path == "/docs/retry" and outer.fail_once:
                    outer.fail_once = False
                    self.send_response(503)
                    self.end_headers()
                    return
                if path == "/docs/retry":
                    return self.reply("text/html", "<html><head><title>Retry</title></head><body>Retry</body></html>")
                if path == "/docs/fail" and outer.persistent_failure:
                    self.send_response(503)
                    self.end_headers()
                    return
                if path == "/docs/fail":
                    return self.reply("text/html", "<html><head><title>Recovered</title></head><body>Recovered</body></html>")
                if "/generated-" in path:
                    index = int(path.rsplit("generated-", 1)[1])
                    title_kind = ("Alpha", "Beta", "Gamma")[(index // 3) % 3]
                    return self.reply("text/html", f"<html><head><title>Reference {title_kind}</title></head><body>Reference</body></html>")
                self.send_response(404)
                self.end_headers()

            def reply(self, content_type, body):
                encoded = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def run_cli(*args):
    values = list(map(str, args))
    if values and values[0] == "start":
        values.append("--allow-private")
    return subprocess.run([sys.executable, str(SCRIPT), *values], capture_output=True, text=True)


def classify_all_as_uncertain(run_dir):
    while True:
        batch = run_cli("classify", "next", "--run-dir", run_dir)
        payload = json.loads(batch.stdout)
        if not payload["items"]:
            return
        decisions = [{
            "id": item["id"], "decision": "uncertain", "category": "other",
            "confidence": "medium", "reason": "requires grouped review",
        } for item in payload["items"]]
        input_path = Path(run_dir) / "uncertain-batch.json"
        input_path.write_text(json.dumps(decisions), encoding="utf-8")
        submitted = run_cli("classify", "submit", "--run-dir", run_dir, "--input", input_path)
        if submitted.returncode != 0:
            raise AssertionError(submitted.stderr)


class DiscoverCliTests(unittest.TestCase):
    def test_group_next_returns_disjoint_proposal_and_scaled_validation_samples(self):
        with FixtureSite(extra_sitemap_count=60) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            self.assertEqual(started.returncode, 0, started.stderr)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            classify_all_as_uncertain(run_dir)

            result = run_cli(
                "classify", "group-next", "--run-dir", run_dir,
                "--field", "path", "--operator", "prefix", "--value", "/docs/api/",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["matched"], 60)
            self.assertEqual(len(payload["proposal_items"]), 3)
            self.assertEqual(len(payload["validation_items"]), 6)
            proposal_ids = {item["id"] for item in payload["proposal_items"]}
            validation_ids = {item["id"] for item in payload["validation_items"]}
            self.assertTrue(proposal_ids.isdisjoint(validation_ids))
            self.assertEqual({item["url"].split("/docs/api/", 1)[1].split("/", 1)[0] for item in payload["proposal_items"]}, {"group-0", "group-1", "group-2"})
            self.assertEqual({item["url"].split("/docs/api/", 1)[1].split("/", 1)[0] for item in payload["validation_items"]}, {"group-0", "group-1", "group-2"})
            self.assertEqual(payload["condition"], {
                "field": "path", "operator": "prefix", "value": "/docs/api/",
            })

    def test_group_submit_applies_unanimously_validated_rule_and_preserves_history(self):
        with FixtureSite(extra_sitemap_count=60) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            classify_all_as_uncertain(run_dir)
            planned = run_cli(
                "classify", "group-next", "--run-dir", run_dir,
                "--field", "path", "--operator", "prefix", "--value", "/docs/api/",
            )
            plan = json.loads(planned.stdout)
            samples = plan["proposal_items"] + plan["validation_items"]
            submission = {
                "rule_id": plan["rule_id"], "decision": "include", "category": "reference",
                "reason": "generated API reference pages are in scope",
                "sample_decisions": [{
                    "id": item["id"], "decision": "include", "category": "reference", "confidence": "high",
                    "reason": "in-scope generated API reference",
                } for item in samples],
            }
            input_path = run_dir / "group-decision.json"
            input_path.write_text(json.dumps(submission), encoding="utf-8")

            submitted = run_cli("classify", "group-submit", "--run-dir", run_dir, "--input", input_path)
            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            self.assertEqual(json.loads(submitted.stdout)["applied"], 60)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            generated = [item for item in state["candidates"] if "/generated-" in item["url"]]
            self.assertEqual({item["decision"] for item in generated}, {"include"})
            self.assertEqual({item["decision_basis"] for item in generated}, {"rule"})
            self.assertTrue(all([entry["basis"] for entry in item["decision_history"]] == ["model", "rule"] for item in generated))

    def test_group_next_rejects_unique_leaf_pages_without_structural_diversity(self):
        with FixtureSite(extra_sitemap_count=60) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            classify_all_as_uncertain(run_dir)
            result = run_cli(
                "classify", "group-next", "--run-dir", run_dir,
                "--field", "path", "--operator", "prefix", "--value", "/docs/api/group-0/",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("structurally different samples", result.stderr)

    def test_failed_group_rule_can_split_once_without_changing_candidates(self):
        with FixtureSite(extra_sitemap_count=60) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            classify_all_as_uncertain(run_dir)

            def plan(value=None, parent=None):
                args = ["classify", "group-next", "--run-dir", run_dir]
                if value:
                    args.extend(["--field", "path", "--operator", "prefix", "--value", value])
                if parent:
                    args.extend(["--parent-rule-id", parent])
                result = run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            def reject(group_plan):
                samples = group_plan["proposal_items"] + group_plan["validation_items"]
                rows = [{
                    "id": item["id"], "decision": "include", "category": "reference", "confidence": "high", "reason": "mostly in scope",
                } for item in samples]
                rows[-1]["decision"] = "exclude"
                rows[-1]["reason"] = "counterexample"
                input_path = run_dir / "rejected-group.json"
                input_path.write_text(json.dumps({
                    "rule_id": group_plan["rule_id"], "decision": "include", "category": "reference",
                    "reason": "proposed generated reference rule", "sample_decisions": rows,
                }), encoding="utf-8")
                result = run_cli("classify", "group-submit", "--run-dir", run_dir, "--input", input_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "validation_failed")

            broad = plan("/docs/api/")
            reject(broad)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            generated = [item for item in state["candidates"] if "/generated-" in item["url"]]
            self.assertEqual({item["decision"] for item in generated}, {"uncertain"})

            subgroup = plan(parent=broad["rule_id"])
            self.assertEqual(subgroup["split_depth"], 1)
            self.assertEqual(subgroup["condition"]["field"], "path")
            self.assertEqual(subgroup["condition"]["operator"], "prefix")
            reject(subgroup)
            blocked = run_cli(
                "classify", "group-next", "--run-dir", run_dir, "--parent-rule-id", broad["rule_id"],
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("already used its one split attempt", blocked.stderr)

    def test_start_fast_fetches_seed_but_does_not_recursively_fetch_links(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            result = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            state = json.loads((Path(payload["run_dir"]) / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertIn(site.origin + "/docs/guide", {item["url"] for item in state["candidates"]})
            self.assertEqual(site.requests.count("/docs/guide"), 0)
            self.assertEqual(state["discovery_status"], "converged")
            self.assertEqual(state["status"], "classification_required")

    def test_standard_recurses_retries_and_preserves_access_limited_candidates(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            result = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            state = json.loads((Path(payload["run_dir"]) / "checkpoint.json").read_text(encoding="utf-8"))
            by_url = {item["url"]: item for item in state["candidates"]}
            self.assertEqual(by_url[site.origin + "/docs/guide"]["fetch_status"], "fetched")
            self.assertEqual(by_url[site.origin + "/docs/retry"]["fetch_status"], "fetched")
            self.assertGreaterEqual(site.requests.count("/docs/retry"), 2)
            self.assertEqual(by_url[site.origin + "/blocked"]["fetch_status"], "access_limited")
            self.assertNotEqual(by_url[site.origin + "/blocked"]["decision"], "exclude")

    def test_budget_limit_enters_decision_required_and_resume_converges(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            first = run_cli(
                "start", "--url", site.origin + "/docs/start", "--mode", "standard",
                "--max-pages", "1", "--output-root", folder,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            run_dir = Path(json.loads(first.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "decision_required")
            self.assertEqual(state["decision_reason"], "budget_limited")

            resumed = run_cli("resume", "--run-dir", run_dir, "--max-pages", "20")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["discovery_status"], "converged")
            self.assertEqual(site.requests.count("/docs/start"), 1)

    def test_classification_export_keeps_audit_and_primary_output_separate(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            batch = run_cli("classify", "next", "--run-dir", run_dir, "--limit", "50")
            self.assertEqual(batch.returncode, 0, batch.stderr)
            items = json.loads(batch.stdout)["items"]
            decisions = []
            for item in items:
                decision = "include" if item["url"].endswith(("/start", "/guide")) else "uncertain"
                decisions.append({
                    "id": item["id"], "decision": decision,
                    "category": "documentation" if decision == "include" else "other",
                    "confidence": "high" if decision == "include" else "medium",
                    "reason": "in scope" if decision == "include" else "needs review",
                })
            decision_file = run_dir / "decisions.json"
            decision_file.write_text(json.dumps(decisions), encoding="utf-8")
            submitted = run_cli("classify", "submit", "--run-dir", run_dir, "--input", decision_file)
            self.assertEqual(submitted.returncode, 0, submitted.stderr)
            exported = run_cli("export", "--run-dir", run_dir)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertIn(site.origin + "/docs/start", (run_dir / "urls.txt").read_text(encoding="utf-8"))
            self.assertNotEqual((run_dir / "uncertain.txt").read_text(encoding="utf-8"), "")
            self.assertTrue((run_dir / "results.csv").exists())
            self.assertTrue((run_dir / "report.md").exists())

    def test_medium_confidence_include_is_rejected(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            item = json.loads(run_cli("classify", "next", "--run-dir", run_dir, "--limit", "1").stdout)["items"][0]
            decision_file = run_dir / "invalid.json"
            decision_file.write_text(json.dumps([{
                "id": item["id"], "decision": "include", "category": "documentation",
                "confidence": "medium", "reason": "maybe",
            }]), encoding="utf-8")
            result = run_cli("classify", "submit", "--run-dir", run_dir, "--input", decision_file)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("medium/low confidence must be uncertain", result.stderr)

    def test_classification_batches_have_no_run_wide_circuit_and_enforce_batch_limit(self):
        with FixtureSite(extra_sitemap_count=40) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            batch_result = run_cli("classify", "next", "--run-dir", run_dir, "--limit", "50")
            payload = json.loads(batch_result.stdout)
            self.assertGreater(payload["returned"], 25)
            self.assertLessEqual(payload["returned"], 50)
            self.assertNotIn("circuit_open", payload)

            oversized = run_cli("classify", "next", "--run-dir", run_dir, "--limit", "101")
            self.assertNotEqual(oversized.returncode, 0)
            self.assertIn("--limit must be between 1 and 100", oversized.stderr)

            one = payload["items"][0]
            incomplete = run_dir / "incomplete.json"
            incomplete.write_text(json.dumps([{
                "id": one["id"], "decision": "uncertain", "category": "other",
                "confidence": "medium", "reason": "needs review",
            }]), encoding="utf-8")
            submitted = run_cli("classify", "submit", "--run-dir", run_dir, "--input", incomplete)
            self.assertNotEqual(submitted.returncode, 0)
            self.assertIn("batch ids must be submitted exactly once", submitted.stderr)

    def test_mode_specific_default_page_limits(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            expected = {"fast": 100, "standard": 500, "deep": 2000}
            for mode, limit in expected.items():
                started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", mode, "--output-root", folder)
                self.assertEqual(started.returncode, 0, started.stderr)
                run_dir = Path(json.loads(started.stdout)["run_dir"])
                state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
                self.assertEqual(state["max_pages"], limit)

    def test_deep_requests_dynamic_evidence_and_resume_ingests_rendered_links(self):
        with FixtureSite(dynamic_shell=True) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "deep", "--output-root", folder)
            self.assertEqual(started.returncode, 0, started.stderr)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "decision_required")
            self.assertEqual(state["decision_reason"], "dynamic_capability_required")

            rendered = run_dir / "rendered-links.json"
            rendered.write_text(json.dumps({site.origin + "/docs/start": [site.origin + "/docs/dynamic-guide"]}), encoding="utf-8")
            resumed = run_cli("resume", "--run-dir", run_dir, "--rendered-links", rendered)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["discovery_status"], "converged")
            self.assertEqual(state["channels"]["dynamic"], "used")
            by_url = {item["url"]: item for item in state["candidates"]}
            self.assertEqual(by_url[site.origin + "/docs/dynamic-guide"]["discovery_method"], "rendered_link")

    def test_export_refuses_unclassified_run_without_explicit_partial_choice(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            result = run_cli("export", "--run-dir", run_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pending classifications remain", result.stderr)
            partial = run_cli("export", "--run-dir", run_dir, "--partial")
            self.assertEqual(partial.returncode, 0, partial.stderr)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial_completed")

    def test_user_override_preserves_model_decision_history(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            item = json.loads(run_cli("classify", "next", "--run-dir", run_dir, "--limit", "1").stdout)["items"][0]
            decision_file = run_dir / "model.json"
            decision_file.write_text(json.dumps([{
                "id": item["id"], "decision": "uncertain", "category": "other",
                "confidence": "medium", "reason": "authority unclear",
            }]), encoding="utf-8")
            self.assertEqual(run_cli("classify", "submit", "--run-dir", run_dir, "--input", decision_file).returncode, 0)
            override = run_cli(
                "classify", "override", "--run-dir", run_dir, "--id", item["id"],
                "--decision", "include", "--reason", "user approved source",
            )
            self.assertEqual(override.returncode, 0, override.stderr)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            updated = next(row for row in state["candidates"] if row["id"] == item["id"])
            self.assertEqual(updated["decision"], "include")
            self.assertEqual(updated["decision_basis"], "user")
            self.assertEqual([entry["basis"] for entry in updated["decision_history"]], ["model", "user"])

    def test_sitemap_index_recurses_into_child_sitemaps(self):
        with FixtureSite(sitemap_index=True) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            self.assertEqual(started.returncode, 0, started.stderr)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            by_url = {item["url"]: item for item in state["candidates"]}
            self.assertIn(site.origin + "/docs/dynamic-guide", by_url)
            self.assertNotIn(site.origin + "/docs-sitemap.xml", by_url)
            self.assertEqual(by_url[site.origin + "/docs/dynamic-guide"]["fetch_status"], "fetched")

    def test_exhausted_failure_requires_decision_and_can_be_retried_alone(self):
        with FixtureSite(persistent_failure=True) as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "decision_required")
            self.assertEqual(state["decision_reason"], "fetch_failures")
            guide_count = site.requests.count("/docs/guide")

            site.persistent_failure = False
            resumed = run_cli("resume", "--run-dir", run_dir, "--retry-failed")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            by_url = {item["url"]: item for item in state["candidates"]}
            self.assertEqual(by_url[site.origin + "/docs/fail"]["fetch_status"], "fetched")
            self.assertEqual(site.requests.count("/docs/guide"), guide_count)

    def test_export_deduplicates_urls_by_canonical_target(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            payload = json.loads(run_cli("classify", "next", "--run-dir", run_dir, "--limit", "50").stdout)
            decisions = []
            for item in payload["items"]:
                decisions.append({
                    "id": item["id"], "decision": "include", "category": "documentation",
                    "confidence": "high", "reason": "fixture documentation",
                })
            input_path = run_dir / "all-include.json"
            input_path.write_text(json.dumps(decisions), encoding="utf-8")
            self.assertEqual(run_cli("classify", "submit", "--run-dir", run_dir, "--input", input_path).returncode, 0)
            self.assertEqual(run_cli("export", "--run-dir", run_dir).returncode, 0)
            urls = (run_dir / "urls.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(urls.count(site.origin + "/docs/guide"), 1)

    def test_project_cannot_escape_output_root(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            escaped_name = "escape-" + Path(folder).name
            result = run_cli(
                "start", "--url", site.origin + "/docs/start", "--mode", "fast",
                "--output-root", folder, "--project", "../" + escaped_name,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("project must be a single safe slug", result.stderr)
            self.assertFalse((Path(folder).parent / escaped_name).exists())

    def test_explicit_scope_path_controls_recursive_fetches(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            result = run_cli(
                "start", "--url", site.origin + "/docs/start", "--mode", "standard",
                "--scope", "/docs/guide", "--output-root", folder,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(site.requests.count("/docs/guide"), 0)
            self.assertEqual(site.requests.count("/docs/retry"), 0)

    def test_completed_run_rejects_resume_without_modifying_checkpoint(self):
        with FixtureSite() as site, tempfile.TemporaryDirectory() as folder:
            started = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "fast", "--output-root", folder)
            run_dir = Path(json.loads(started.stdout)["run_dir"])
            self.assertEqual(run_cli("export", "--run-dir", run_dir, "--partial").returncode, 0)
            checkpoint = run_dir / "checkpoint.json"
            before = checkpoint.read_bytes()
            rendered = run_dir / "rendered.json"
            rendered.write_text("{}", encoding="utf-8")
            result = run_cli("resume", "--run-dir", run_dir, "--rendered-links", rendered)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("completed run is immutable", result.stderr)
            self.assertEqual(checkpoint.read_bytes(), before)
            for command in (
                ("classify", "next", "--run-dir", run_dir),
                ("export", "--run-dir", run_dir, "--partial"),
            ):
                result = run_cli(*command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("completed run is immutable", result.stderr)
                self.assertEqual(checkpoint.read_bytes(), before)

    def test_start_rejects_private_seed_without_explicit_test_override(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "start", "--url", "http://127.0.0.1/docs"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public URL required", result.stderr)

    def test_cross_origin_canonical_and_redirect_cannot_replace_official_urls(self):
        with FixtureSite(unsafe_targets=True) as site, tempfile.TemporaryDirectory() as folder:
            result = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            run_dir = Path(json.loads(result.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            by_url = {item["url"]: item for item in state["candidates"]}
            alias = by_url[site.origin + "/docs/alias"]
            self.assertEqual(alias["canonical_url"], "")
            self.assertEqual(alias["declared_canonical_url"], "https://evil.example/x")
            redirected = by_url[site.origin + "/docs/external-redirect"]
            self.assertEqual(redirected["fetch_status"], "access_limited")
            self.assertEqual(redirected["final_url"], "")

    def test_official_sitemap_candidate_blocked_by_robots_is_included_unverified(self):
        with FixtureSite(blocked_sitemap=True) as site, tempfile.TemporaryDirectory() as folder:
            result = run_cli("start", "--url", site.origin + "/docs/start", "--mode", "standard", "--output-root", folder)
            run_dir = Path(json.loads(result.stdout)["run_dir"])
            state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            item = next(row for row in state["candidates"] if row["url"] == site.origin + "/docs/blocked-doc")
            self.assertEqual(item["fetch_status"], "access_limited")
            self.assertEqual(item["decision"], "include")
            self.assertEqual(item["decision_basis"], "rule")
            self.assertIn("content unverified", item["reason"])

    def test_verified_cross_host_scope_root_is_fetched(self):
        with FixtureSite() as api_site:
            api_url = api_site.origin + "/docs/guide"
            with FixtureSite(official_external_url=api_url) as docs_site, tempfile.TemporaryDirectory() as folder:
                result = run_cli(
                    "start", "--url", docs_site.origin + "/docs/start", "--mode", "standard",
                    "--scope-root", api_site.origin + "/docs", "--output-root", folder,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                run_dir = Path(json.loads(result.stdout)["run_dir"])
                state = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
                item = next(row for row in state["candidates"] if row["url"] == api_url)
                self.assertEqual(item["fetch_status"], "fetched")
                self.assertEqual(item["provenance"], "official_direct")


if __name__ == "__main__":
    unittest.main()
