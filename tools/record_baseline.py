"""Record or check the offline evaluation baseline.

The evaluation covers the rules engine only: no Gemini calls, no database
writes, and it never modifies the dataset.

    python tools/record_baseline.py            # record (refuses to overwrite)
    python tools/record_baseline.py --force    # overwrite an existing baseline
    python tools/record_baseline.py --check    # fail if metrics regressed or data changed
"""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "docs" / "baselines"
JSON_PATH = OUT_DIR / "offline_eval_baseline.json"
TXT_PATH = OUT_DIR / "offline_eval_baseline.txt"


def dataset_sha256():
    files = sorted(DATA_DIR.glob("inv_*.json")) + sorted(DATA_DIR.glob("inv_*.txt"))
    manifest = DATA_DIR / "manifest.json"
    if manifest.exists():
        files.append(manifest)
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest(), len(files)


def run_evaluation():
    result = subprocess.run(
        [sys.executable, "-m", "src.evaluate"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr)
        sys.exit("Evaluation failed.")
    return result.stdout


def _pct(block, label):
    match = re.search(rf"{re.escape(label)}\s*:\s*([\d.]+)%", block)
    return float(match.group(1)) if match else None


def parse_metrics(output):
    head, _, tail = output.partition("PROMPT INJECTION DETECTION")
    evaluated = re.search(r"Invoices evaluated\s*:\s*(\d+)", output)
    routing = re.search(r"Correct routing decisions\s*:\s*(\d+)/(\d+)", output)
    return {
        "invoices_evaluated": int(evaluated.group(1)) if evaluated else None,
        "routing_correct": int(routing.group(1)) if routing else None,
        "routing_accuracy_pct": _pct(head, "Routing accuracy"),
        "exact_issue_accuracy_pct": _pct(head, "Exact issue accuracy"),
        "issue_precision_pct": _pct(head, "Precision"),
        "issue_recall_pct": _pct(head, "Recall"),
        "issue_f1_pct": _pct(head, "F1 Score"),
        "injection_precision_pct": _pct(tail, "Precision"),
        "injection_recall_pct": _pct(tail, "Recall"),
        "injection_f1_pct": _pct(tail, "F1 Score"),
    }


def record(force):
    if JSON_PATH.exists() and not force:
        sys.exit(f"{JSON_PATH} exists. Use --force to overwrite.")
    output = run_evaluation()
    metrics = parse_metrics(output)
    if None in metrics.values():
        sys.exit(f"Could not parse every metric: {metrics}")
    sha, count = dataset_sha256()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TXT_PATH.write_text(output, encoding="utf-8")
    JSON_PATH.write_text(json.dumps({
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "scope": "rules engine only (offline, labels replayed; no Gemini extraction)",
        "dataset_sha256": sha,
        "dataset_files": count,
        "metrics": metrics,
    }, indent=2), encoding="utf-8")
    print(output)
    print(f"Baseline saved:\n  {JSON_PATH}\n  {TXT_PATH}")


def check():
    if not JSON_PATH.exists():
        sys.exit("No baseline recorded yet.")
    baseline = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    sha, _ = dataset_sha256()
    if sha != baseline["dataset_sha256"]:
        sys.exit("FAIL: the dataset changed since the baseline was recorded.")
    current = parse_metrics(run_evaluation())
    problems = []
    for key, old in baseline["metrics"].items():
        new = current[key]
        if key == "invoices_evaluated":
            if new != old:
                problems.append(f"{key}: {old} -> {new}")
        elif new is None or new < old - 1e-9:
            problems.append(f"{key}: {old} -> {new}")
    if problems:
        sys.exit("FAIL: metrics regressed:\n  " + "\n  ".join(problems))
    print("OK: offline evaluation matches or beats the baseline.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else record(args.force)


if __name__ == "__main__":
    main()