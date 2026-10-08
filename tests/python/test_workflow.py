import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "scan.yml"


def test_checkout_uses_branch_tip_so_queued_runs_can_push():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"uses: actions/checkout@v4\s*\n\s+with:\s*\n(?:\s+#.*\n)*\s+ref: main", text)
