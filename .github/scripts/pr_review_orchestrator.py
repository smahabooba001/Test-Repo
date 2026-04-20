#!/usr/bin/env python3
"""PR review orchestration for integrating an external agent reviewer.

This script is intentionally focused on orchestration. Review intelligence remains external.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import importlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SUMMARY_MARKER = "<!-- ai-pr-review:summary -->"
DEFAULT_CONFIG_PATH = ".pr-review-config.json"


@dataclasses.dataclass
class Config:
    github_token: str
    github_repository: str
    github_api_url: str
    github_event_path: str
    dry_run: bool
    fail_on_agent_error: bool
    fail_on_publish_error: bool
    include_file_contents: bool
    max_file_content_bytes: int
    max_inline_comments: int
    report_dir: str
    commit_report: bool
    commit_report_branch: str
    max_critical: int
    max_high: int
    agent_timeout_sec: int
    agent_retry_count: int
    agent_retry_delay_sec: int
    agent_cli_command: str
    agent_python_module: str
    agent_python_function: str


class GitHubClient:
    def __init__(self, token: str, api_url: str, repo: str, dry_run: bool = False) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.repo = repo
        self.dry_run = dry_run

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_url}{path}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc

    def list_pull_files(self, pr_number: int) -> list[dict[str, Any]]:
        page = 1
        output: list[dict[str, Any]] = []
        while True:
            chunk = self.request(
                "GET",
                f"/repos/{self.repo}/pulls/{pr_number}/files?per_page=100&page={page}",
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        return output

    def get_file_content(self, path: str, ref: str) -> str | None:
        encoded_path = urllib.parse.quote(path, safe="/")
        payload = self.request("GET", f"/repos/{self.repo}/contents/{encoded_path}?ref={ref}")
        if not isinstance(payload, dict):
            return None
        if payload.get("encoding") != "base64" or "content" not in payload:
            return None
        raw = base64.b64decode(payload["content"].encode("utf-8"), validate=False)
        return raw.decode("utf-8", errors="replace")

    def upsert_summary_comment(self, pr_number: int, body: str) -> None:
        if self.dry_run:
            print("[dry-run] Would upsert summary PR comment")
            return

        comments = self.request("GET", f"/repos/{self.repo}/issues/{pr_number}/comments?per_page=100")
        existing = None
        for comment in comments:
            if SUMMARY_MARKER in comment.get("body", ""):
                existing = comment
                break

        payload = {"body": body}
        if existing:
            self.request("PATCH", f"/repos/{self.repo}/issues/comments/{existing['id']}", payload)
        else:
            self.request("POST", f"/repos/{self.repo}/issues/{pr_number}/comments", payload)

    def post_inline_comment(self, pr_number: int, commit_id: str, finding: dict[str, Any]) -> None:
        if self.dry_run:
            print(f"[dry-run] Would post inline comment for {finding.get('path')}:{finding.get('line')}")
            return

        payload = {
            "body": finding["body"],
            "commit_id": commit_id,
            "path": finding["path"],
            "line": finding["line"],
            "side": "RIGHT",
        }
        self.request("POST", f"/repos/{self.repo}/pulls/{pr_number}/comments", payload)


def str_to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    file_config: dict[str, Any] = {}
    config_path = os.getenv("PR_REVIEW_CONFIG", DEFAULT_CONFIG_PATH)
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as fh:
            file_config = json.load(fh)

    def value(key: str, default: Any) -> Any:
        env_key = f"PR_REVIEW_{key.upper()}"
        if env_key in os.environ:
            return os.environ[env_key]
        return file_config.get(key, default)

    return Config(
        github_token=os.getenv("GITHUB_TOKEN", ""),
        github_repository=os.getenv("GITHUB_REPOSITORY", ""),
        github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
        github_event_path=os.getenv("GITHUB_EVENT_PATH", ""),
        dry_run=str_to_bool(os.getenv("DRY_RUN") or str(value("dry_run", "false"))),
        fail_on_agent_error=str_to_bool(str(value("fail_on_agent_error", "false"))),
        fail_on_publish_error=str_to_bool(str(value("fail_on_publish_error", "false"))),
        include_file_contents=str_to_bool(str(value("include_file_contents", "true"))),
        max_file_content_bytes=int(value("max_file_content_bytes", 200000)),
        max_inline_comments=int(value("max_inline_comments", 25)),
        report_dir=str(value("report_dir", "docs/pr-reviews")),
        commit_report=str_to_bool(str(value("commit_report", "false"))),
        commit_report_branch=str(value("commit_report_branch", "")),
        max_critical=int(value("max_critical", 0)),
        max_high=int(value("max_high", 0)),
        agent_timeout_sec=int(value("agent_timeout_sec", 300)),
        agent_retry_count=int(value("agent_retry_count", 2)),
        agent_retry_delay_sec=int(value("agent_retry_delay_sec", 5)),
        agent_cli_command=str(value("agent_cli_command", "")),
        agent_python_module=str(value("agent_python_module", "")),
        agent_python_function=str(value("agent_python_function", "review_pull_request")),
    )


def read_event(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def collect_pr_context(cfg: Config, gh: GitHubClient, event: dict[str, Any]) -> dict[str, Any]:
    pull_request = event.get("pull_request")
    if not pull_request:
        raise RuntimeError("This event does not include pull_request payload")

    pr_number = pull_request["number"]
    head_sha = pull_request["head"]["sha"]
    files = gh.list_pull_files(pr_number)

    changed_files: list[dict[str, Any]] = []
    diff_chunks: list[str] = []

    for file in files:
        entry = {
            "path": file.get("filename"),
            "status": file.get("status"),
            "additions": file.get("additions"),
            "deletions": file.get("deletions"),
            "changes": file.get("changes"),
            "patch": file.get("patch"),
            "contents": None,
        }
        if file.get("patch"):
            diff_chunks.append(f"--- {file.get('filename')}\n{file.get('patch')}")

        should_fetch_content = (
            cfg.include_file_contents
            and file.get("status") != "removed"
            and isinstance(file.get("filename"), str)
            and int(file.get("changes", 0)) > 0
        )
        if should_fetch_content:
            try:
                content = gh.get_file_content(file["filename"], head_sha)
                if content is not None and len(content.encode("utf-8")) <= cfg.max_file_content_bytes:
                    entry["contents"] = content
            except Exception as exc:  # best effort fallback
                entry["contents_error"] = str(exc)

        changed_files.append(entry)

    return {
        "repository": cfg.github_repository,
        "pr": {
            "number": pr_number,
            "title": pull_request.get("title", ""),
            "description": pull_request.get("body", "") or "",
            "url": pull_request.get("html_url", ""),
            "base_sha": pull_request.get("base", {}).get("sha", ""),
            "head_sha": head_sha,
            "author": pull_request.get("user", {}).get("login", ""),
        },
        "changed_files": changed_files,
        "diff": "\n\n".join(diff_chunks),
    }


def invoke_agent_via_module(cfg: Config, context: dict[str, Any]) -> dict[str, Any]:
    module = importlib.import_module(cfg.agent_python_module)
    fn = getattr(module, cfg.agent_python_function)
    result = fn(context)
    if not isinstance(result, dict):
        raise RuntimeError("Agent module function must return a dict")
    return result


def invoke_agent_via_cli(cfg: Config, context: dict[str, Any]) -> dict[str, Any]:
    if not cfg.agent_cli_command:
        raise RuntimeError("No agent invocation configured. Set PR_REVIEW_AGENT_CLI_COMMAND or PR_REVIEW_AGENT_PYTHON_MODULE")

    tmp_dir = pathlib.Path(os.getenv("RUNNER_TEMP", "."))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_path = tmp_dir / "pr-review-agent-input.json"
    output_path = tmp_dir / "pr-review-agent-output.json"
    input_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    if output_path.exists():
        output_path.unlink()

    command = cfg.agent_cli_command.format(input=str(input_path), output=str(output_path))
    args = shlex.split(command)

    subprocess.run(args, check=True, timeout=cfg.agent_timeout_sec)

    if not output_path.exists():
        raise RuntimeError("Agent CLI completed but output JSON was not created")

    return json.loads(output_path.read_text(encoding="utf-8"))


def invoke_agent_with_retry(cfg: Config, context: dict[str, Any]) -> dict[str, Any]:
    attempts = max(1, cfg.agent_retry_count + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            if cfg.agent_python_module:
                return invoke_agent_via_module(cfg, context)
            return invoke_agent_via_cli(cfg, context)
        except Exception as exc:
            last_error = exc
            print(f"Agent invocation attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(cfg.agent_retry_delay_sec)

    raise RuntimeError(f"Agent invocation failed after {attempts} attempts: {last_error}")


def normalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    severity = str(raw.get("severity", "low")).lower()
    if severity not in {"critical", "high", "medium", "low", "info"}:
        severity = "low"

    title = raw.get("title") or raw.get("rule") or "Finding"
    details = raw.get("details") or raw.get("description") or ""
    suggestion = raw.get("suggestion") or ""

    body_parts = [f"**{severity.upper()}**: {title}"]
    if details:
        body_parts.append(details)
    if suggestion:
        body_parts.append(f"Suggested fix: {suggestion}")

    line_value = raw.get("line")
    line = int(line_value) if isinstance(line_value, int) or str(line_value).isdigit() else None

    return {
        "severity": severity,
        "title": str(title),
        "details": str(details),
        "suggestion": str(suggestion),
        "path": raw.get("path") or raw.get("file") or "",
        "line": line,
        "body": "\n\n".join(body_parts),
    }


def parse_agent_result(raw: dict[str, Any]) -> dict[str, Any]:
    findings = [normalize_finding(item) for item in raw.get("findings", []) if isinstance(item, dict)]
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    for finding in findings:
        severity_counts[finding["severity"]] += 1

    summary = (
        raw.get("summary")
        or raw.get("overall_summary")
        or raw.get("result")
        or "Automated review completed."
    )
    documentation_markdown = (
        raw.get("documentation_markdown")
        or raw.get("report_markdown")
        or raw.get("documentation")
        or ""
    )

    return {
        "summary": str(summary),
        "documentation_markdown": str(documentation_markdown),
        "findings": findings,
        "severity_counts": severity_counts,
    }


def evaluate_policy(parsed: dict[str, Any], cfg: Config) -> tuple[bool, str]:
    critical = parsed["severity_counts"]["critical"]
    high = parsed["severity_counts"]["high"]

    failed_reasons: list[str] = []
    if critical > cfg.max_critical:
        failed_reasons.append(f"critical findings {critical} > allowed {cfg.max_critical}")
    if high > cfg.max_high:
        failed_reasons.append(f"high findings {high} > allowed {cfg.max_high}")

    if failed_reasons:
        return False, "; ".join(failed_reasons)
    return True, "Severity thresholds satisfied"


def map_inline_findings(parsed: dict[str, Any], changed_files: list[dict[str, Any]], max_comments: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    changed_paths = {item.get("path") for item in changed_files}
    inline: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for finding in parsed["findings"]:
        if len(inline) >= max_comments:
            skipped.append({**finding, "skip_reason": "max_inline_comments_limit"})
            continue
        if not finding.get("path") or finding.get("path") not in changed_paths:
            skipped.append({**finding, "skip_reason": "path_not_in_changed_files"})
            continue
        if not finding.get("line") or int(finding["line"]) <= 0:
            skipped.append({**finding, "skip_reason": "invalid_line"})
            continue
        inline.append(finding)

    return inline, skipped


def build_summary_comment(parsed: dict[str, Any], policy_ok: bool, policy_message: str, report_path: str, skipped_inline_count: int) -> str:
    sev = parsed["severity_counts"]
    status = "PASS" if policy_ok else "FAIL"

    lines = [
        SUMMARY_MARKER,
        "## AI PR Review Summary",
        f"- Status: **{status}**",
        f"- Policy: {policy_message}",
        f"- Findings: critical={sev['critical']}, high={sev['high']}, medium={sev['medium']}, low={sev['low']}, info={sev['info']}",
        f"- Markdown report: `{report_path}`",
        "",
        parsed["summary"],
    ]
    if skipped_inline_count:
        lines.extend(["", f"Inline comment skips: {skipped_inline_count} finding(s) were summarized only."])

    return "\n".join(lines)


def render_markdown_report(context: dict[str, Any], parsed: dict[str, Any], policy_ok: bool, policy_message: str) -> str:
    pr = context["pr"]
    sev = parsed["severity_counts"]
    out: list[str] = [
        f"# PR Review Report: #{pr['number']}",
        "",
        f"- PR: {pr['title']}",
        f"- URL: {pr['url']}",
        f"- Author: {pr['author']}",
        f"- Policy status: {'PASS' if policy_ok else 'FAIL'}",
        f"- Policy details: {policy_message}",
        "",
        "## Summary",
        "",
        parsed["summary"],
        "",
        "## Severity Counts",
        "",
        f"- Critical: {sev['critical']}",
        f"- High: {sev['high']}",
        f"- Medium: {sev['medium']}",
        f"- Low: {sev['low']}",
        f"- Info: {sev['info']}",
        "",
        "## Findings",
        "",
    ]

    if parsed["findings"]:
        for idx, finding in enumerate(parsed["findings"], start=1):
            location = finding["path"]
            if finding.get("line"):
                location = f"{location}:{finding['line']}"
            out.extend(
                [
                    f"### {idx}. [{finding['severity'].upper()}] {finding['title']}",
                    "",
                    f"- Location: {location or 'n/a'}",
                    f"- Details: {finding['details'] or 'n/a'}",
                    f"- Suggestion: {finding['suggestion'] or 'n/a'}",
                    "",
                ]
            )
    else:
        out.append("No findings returned by the agent.\n")

    if parsed["documentation_markdown"]:
        out.extend(["## Agent Documentation", "", parsed["documentation_markdown"], ""])

    return "\n".join(out).strip() + "\n"


def write_report(report_dir: str, pr_number: int, markdown: str) -> str:
    directory = pathlib.Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pr-{pr_number}-review.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path).replace("\\", "/")


def commit_report_if_enabled(cfg: Config, report_path: str, pr_number: int) -> None:
    if not cfg.commit_report:
        return

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", report_path], check=True)

    check = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if check.returncode == 0:
        print("No report changes to commit.")
        return

    subprocess.run(["git", "commit", "-m", f"docs: add PR review report for #{pr_number}"], check=True)

    if cfg.commit_report_branch:
        subprocess.run(["git", "push", "origin", f"HEAD:{cfg.commit_report_branch}"], check=True)
    else:
        subprocess.run(["git", "push"], check=True)


def publish_results(
    cfg: Config,
    gh: GitHubClient,
    context: dict[str, Any],
    parsed: dict[str, Any],
    report_path: str,
    policy_ok: bool,
    policy_message: str,
) -> None:
    pr_number = context["pr"]["number"]
    head_sha = context["pr"]["head_sha"]

    inline_findings, skipped = map_inline_findings(parsed, context["changed_files"], cfg.max_inline_comments)

    for finding in inline_findings:
        try:
            gh.post_inline_comment(pr_number, head_sha, finding)
        except Exception as exc:
            skipped.append({**finding, "skip_reason": f"publish_error: {exc}"})

    summary_body = build_summary_comment(parsed, policy_ok, policy_message, report_path, len(skipped))
    gh.upsert_summary_comment(pr_number, summary_body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrate external PR review agent")
    parser.add_argument("--event-path", default=None, help="Path to GitHub event payload JSON")
    args = parser.parse_args()

    cfg = load_config()
    if args.event_path:
        cfg.github_event_path = args.event_path

    if not cfg.github_event_path:
        raise RuntimeError("GITHUB_EVENT_PATH is required")

    event = read_event(cfg.github_event_path)
    if "pull_request" not in event:
        print("Event has no pull_request data, skipping.")
        return 0

    if not cfg.github_repository:
        repo = event.get("repository", {}).get("full_name", "")
        cfg.github_repository = repo

    gh = GitHubClient(cfg.github_token, cfg.github_api_url, cfg.github_repository, dry_run=cfg.dry_run)
    context = collect_pr_context(cfg, gh, event)

    agent_error = None
    raw_result: dict[str, Any]
    try:
        raw_result = invoke_agent_with_retry(cfg, context)
    except Exception as exc:
        agent_error = str(exc)
        raw_result = {
            "summary": "Agent execution failed. Fallback summary generated by orchestrator.",
            "findings": [],
            "documentation_markdown": f"Agent failure details:\n\n```\n{agent_error}\n```",
        }

    parsed = parse_agent_result(raw_result)
    policy_ok, policy_message = evaluate_policy(parsed, cfg)

    report_markdown = render_markdown_report(context, parsed, policy_ok, policy_message)
    report_path = write_report(cfg.report_dir, context["pr"]["number"], report_markdown)

    publish_error = None
    try:
        publish_results(cfg, gh, context, parsed, report_path, policy_ok, policy_message)
    except Exception as exc:
        publish_error = str(exc)
        print(f"Publish error: {publish_error}", file=sys.stderr)

    try:
        commit_report_if_enabled(cfg, report_path, context["pr"]["number"])
    except Exception as exc:
        print(f"Report commit failed: {exc}", file=sys.stderr)

    if agent_error and cfg.fail_on_agent_error:
        print(agent_error, file=sys.stderr)
        return 1

    if publish_error and cfg.fail_on_publish_error:
        return 1

    if not policy_ok:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

