# AI PR Review Orchestration

This repository contains **integration/orchestration logic** for automated PR review. The actual review intelligence stays in an external agent repository ("agent repo") exposed through a CLI or Python module interface.

## What It Does

On pull request events (`opened`, `synchronize`, `reopened`, `ready_for_review`), CI:

1. Collects PR context:
   - PR number, title, description
   - changed files and patches
   - optional file contents for changed files
2. Invokes the external review agent
3. Parses structured findings
4. Posts results back to GitHub PR:
   - one upserted summary comment
   - inline comments (best effort) when file/line mapping is valid
5. Generates markdown report:
   - `docs/pr-reviews/pr-<number>-review.md`
   - uploaded as CI artifact
6. Enforces severity policy and fails CI if thresholds are exceeded

## Added Components

- Workflow: `.github/workflows/ai-pr-review.yml`
- Orchestrator: `.github/scripts/pr_review_orchestrator.py`
- Tests: `tests/test_pr_review_orchestrator.py`

## Agent Integration Contract

Configure **one** of the following:

1. CLI mode (recommended):
   - `PR_REVIEW_AGENT_CLI_COMMAND`
   - Command must accept formatted placeholders `{input}` and `{output}`
   - Example:
     - `agent-review-cli review --input {input} --output {output}`

2. Python module mode:
   - `PR_REVIEW_AGENT_PYTHON_MODULE`
   - Optional: `PR_REVIEW_AGENT_PYTHON_FUNCTION` (default: `review_pull_request`)
   - Function signature: `fn(context: dict) -> dict`

### Expected Agent Output JSON (example)

```json
{
  "summary": "Overall review summary",
  "documentation_markdown": "# Detailed documentation...",
  "findings": [
    {
      "severity": "high",
      "title": "Potential null dereference",
      "description": "Pointer may be null before use.",
      "suggestion": "Add null check before dereference.",
      "path": "src/main.c",
      "line": 42
    }
  ]
}
```

## Configuration

Use repo variables, environment variables, or a config file `.pr-review-config.json`.

Supported keys:

- `PR_REVIEW_AGENT_CLI_COMMAND`
- `PR_REVIEW_AGENT_PYTHON_MODULE`
- `PR_REVIEW_AGENT_PYTHON_FUNCTION`
- `PR_REVIEW_MAX_CRITICAL` (default `0`)
- `PR_REVIEW_MAX_HIGH` (default `0`)
- `PR_REVIEW_DRY_RUN` (default `false`)
- `PR_REVIEW_FAIL_ON_AGENT_ERROR` (default `false`)
- `PR_REVIEW_FAIL_ON_PUBLISH_ERROR` (default `false`)
- `PR_REVIEW_INCLUDE_FILE_CONTENTS` (default `true`)
- `PR_REVIEW_MAX_FILE_CONTENT_BYTES` (default `200000`)
- `PR_REVIEW_MAX_INLINE_COMMENTS` (default `25`)
- `PR_REVIEW_REPORT_DIR` (default `docs/pr-reviews`)
- `PR_REVIEW_COMMIT_REPORT` (default `false`)
- `PR_REVIEW_COMMIT_REPORT_BRANCH` (optional)
- `PR_REVIEW_AGENT_TIMEOUT_SEC` (default `300`)
- `PR_REVIEW_AGENT_RETRY_COUNT` (default `2`)
- `PR_REVIEW_AGENT_RETRY_DELAY_SEC` (default `5`)
- `PR_REVIEW_CONFIG` (optional custom config path)

### Example `.pr-review-config.json`

```json
{
  "agent_cli_command": "agent-review-cli review --input {input} --output {output}",
  "max_critical": 0,
  "max_high": 1,
  "dry_run": false,
  "agent_retry_count": 2,
  "agent_retry_delay_sec": 5
}
```

## Dry Run (Local)

You can test orchestration without posting comments:

```bash
DRY_RUN=true \
PR_REVIEW_AGENT_CLI_COMMAND="agent-review-cli review --input {input} --output {output}" \
GITHUB_EVENT_PATH=/path/to/pull_request_event.json \
GITHUB_REPOSITORY=owner/repo \
python .github/scripts/pr_review_orchestrator.py
```

In dry-run mode, comment publishing is skipped, but markdown report generation and parsing/policy flow still execute.

## Error Handling and Fallbacks

- Agent invocation supports retry with configurable attempts/delay.
- If agent execution fails, orchestrator generates fallback summary/report documentation.
- CI failure behavior is configurable via:
  - `PR_REVIEW_FAIL_ON_AGENT_ERROR`
  - `PR_REVIEW_FAIL_ON_PUBLISH_ERROR`
- Severity thresholds always gate CI when findings exceed limits.

## Running Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## Notes

- Inline comments are best-effort and only posted when finding path/line can be mapped safely.
- Summary comment is upserted (updated if existing marker comment is found).
- Report path convention:
  - `docs/pr-reviews/pr-<number>-review.md`

