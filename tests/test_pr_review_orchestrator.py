import importlib.util
import pathlib
import sys
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / ".github" / "scripts" / "pr_review_orchestrator.py"
spec = importlib.util.spec_from_file_location("pr_review_orchestrator", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["pr_review_orchestrator"] = mod
spec.loader.exec_module(mod)


class ParserTests(unittest.TestCase):
    def test_parse_agent_result_normalizes_findings(self):
        raw = {
            "summary": "Done",
            "findings": [
                {
                    "severity": "HIGH",
                    "title": "Unsafe API",
                    "description": "Potential unsafe call",
                    "suggestion": "Use bounds check",
                    "file": "Program.c",
                    "line": "12",
                }
            ],
        }

        parsed = mod.parse_agent_result(raw)

        self.assertEqual(parsed["summary"], "Done")
        self.assertEqual(parsed["severity_counts"]["high"], 1)
        self.assertEqual(parsed["findings"][0]["path"], "Program.c")
        self.assertEqual(parsed["findings"][0]["line"], 12)

    def test_map_inline_findings_filters_invalid(self):
        parsed = {
            "findings": [
                {"path": "a.c", "line": 5, "body": "x", "severity": "high", "title": "A", "details": "", "suggestion": ""},
                {"path": "b.c", "line": 0, "body": "y", "severity": "high", "title": "B", "details": "", "suggestion": ""},
                {"path": "z.c", "line": 1, "body": "z", "severity": "high", "title": "C", "details": "", "suggestion": ""},
            ]
        }
        changed_files = [{"path": "a.c"}, {"path": "b.c"}]

        inline, skipped = mod.map_inline_findings(parsed, changed_files, max_comments=10)

        self.assertEqual(len(inline), 1)
        self.assertEqual(inline[0]["path"], "a.c")
        self.assertEqual(len(skipped), 2)


class PolicyTests(unittest.TestCase):
    def test_policy_failure_when_threshold_exceeded(self):
        cfg = mod.Config(
            github_token="",
            github_repository="owner/repo",
            github_api_url="https://api.github.com",
            github_event_path="event.json",
            dry_run=True,
            fail_on_agent_error=False,
            fail_on_publish_error=False,
            include_file_contents=True,
            max_file_content_bytes=100,
            max_inline_comments=10,
            report_dir="docs/pr-reviews",
            commit_report=False,
            commit_report_branch="",
            max_critical=0,
            max_high=0,
            agent_timeout_sec=30,
            agent_retry_count=1,
            agent_retry_delay_sec=1,
            agent_cli_command="",
            agent_python_module="",
            agent_python_function="review_pull_request",
        )

        parsed = {
            "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
            "findings": [],
            "summary": "",
            "documentation_markdown": "",
        }

        ok, message = mod.evaluate_policy(parsed, cfg)
        self.assertFalse(ok)
        self.assertIn("critical findings 1 > allowed 0", message)


if __name__ == "__main__":
    unittest.main()

