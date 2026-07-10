import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from scripts.discover import build_discovery_metadata, canonicalize_url, open_with_retries, robots_allows, write_outputs


class DiscoverTests(unittest.TestCase):
    def test_canonicalize_url_removes_fragment_and_tracking_parameters(self):
        value = canonicalize_url(
            "https://docs.example.com/guide#install?utm_source=newsletter&ref=home"
        )
        self.assertEqual(value, "https://docs.example.com/guide")

    def test_canonicalize_url_keeps_meaningful_query_parameters(self):
        value = canonicalize_url("https://example.com/search?q=langgraph&utm_medium=x")
        self.assertEqual(value, "https://example.com/search?q=langgraph")

    def test_write_outputs_creates_expected_files_and_keeps_uncertain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_outputs(
                Path(tmp),
                [
                    {"url": "https://example.com/a", "decision": "include", "reason": "core"},
                    {"url": "https://example.com/b", "decision": "uncertain", "reason": "review"},
                    {"url": "https://example.com/c", "decision": "exclude", "reason": "noise"},
                ],
                {"mode": "standard", "candidate_count": 3},
            )
            self.assertEqual(set(out), {"urls.txt", "uncertain.txt", "excluded.txt", "results.csv", "report.md"})
            self.assertEqual((Path(tmp) / "urls.txt").read_text(), "https://example.com/a\n")
            self.assertEqual((Path(tmp) / "uncertain.txt").read_text(), "https://example.com/b\n")
            self.assertIn("candidate_count: 3", (Path(tmp) / "report.md").read_text())

    def test_cli_exports_llm_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "decisions.json"
            decisions.write_text(json.dumps([{"url": "https://example.com/a", "decision": "include", "reason": "core"}]), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(Path(__file__).parents[1] / "scripts" / "discover.py"),
                "--export-decisions", str(decisions),
                "--output", str(root / "out"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "out" / "urls.txt").read_text(), "https://example.com/a\n")

    def test_metadata_warns_when_page_limit_is_reached(self):
        metadata = build_discovery_metadata("https://example.com/docs", "standard", [{"url":"a"}, {"url":"b"}], 2)
        self.assertTrue(metadata["page_limit_reached"])
        self.assertEqual(metadata["coverage_status"], "incomplete_limit_reached")

    def test_unavailable_robots_file_does_not_block_public_seed(self):
        self.assertTrue(robots_allows(None, False, "https://example.com/docs"))

    def test_fetch_retries_transient_failures(self):
        attempts = []
        def opener(request, timeout):
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("temporary")
            return "response"
        self.assertEqual(open_with_retries("request", opener=opener, retries=3, delay=0), "response")
        self.assertEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
