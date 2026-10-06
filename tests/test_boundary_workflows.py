"""The boundary steps are wired where their inputs exist and before their readers."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _names(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    return re.findall(r"^      - name: (.+)$", text, re.MULTILINE), text


def test_trace_builds_the_perimeter_then_provenance_then_correlates_then_rosters():
    names, text = _names("trace.yml")
    order = ["Run the trace engine", "Build his perimeter", "Resolve funding provenance",
             "Correlate custody-gap exits", "Build wallet roster"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    assert "python src/correlator.py --pools bridge cctp --no-etherscan" in text
    assert "Correlate Circle deposits" not in names
    # Job-level ceilings sit at four spaces; the cron gate job's 3 is one of them.
    jobs = [int(x) for x in re.findall(r"^    timeout-minutes: (\d+)$", text, re.MULTILINE)]
    assert max(jobs) >= 45


def test_watch_attributes_withdrawals_with_a_bounded_step():
    names, text = _names("watch.yml")
    assert "Attribute withdrawals to his world" in names
    block = text.split("- name: Attribute withdrawals to his world", 1)[1].split("- name:", 1)[0]
    assert "scripts/check_boundary.py" in block and "timeout-minutes: 4" in block
