import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "scan.yml"


def test_checkout_uses_branch_tip_so_queued_runs_can_push():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"uses: actions/checkout@v4\s*\n\s+with:\s*\n(?:\s+#.*\n)*\s+ref: main", text)


def test_scan_step_receives_the_serpapi_key():
    text = WORKFLOW.read_text(encoding="utf-8")
    scan_step = text.split("- name: Scan\n", 1)[1].split("- name:", 1)[0]
    assert "SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}" in scan_step


def test_bot_push_retries_after_rebasing_so_spent_checks_are_kept():
    text = WORKFLOW.read_text(encoding="utf-8")
    commit_step = text.split("- name: Commit results" + chr(10), 1)[1].split("- uses:", 1)[0]
    assert "git pull --rebase" in commit_step
    assert "for attempt in 1 2 3" in commit_step
