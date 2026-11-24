import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "docs" / "baselines" / "offline_eval_baseline.json"


@pytest.mark.regression
@pytest.mark.skipif(not BASELINE.exists(), reason="no baseline recorded yet")
def test_offline_evaluation_has_not_regressed():
    result = subprocess.run(
        [sys.executable, "tools/record_baseline.py", "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr