
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import src.rules as rules
from src.schemas import Invoice

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def extract_line_items(invoice_text):
    """Extract line items from the synthetic invoice text."""

    pattern = re.compile(
        r"Description:\s*([^\r\n]+)\s*\r?\n"
        r"\s*Quantity:\s*([\d,]+(?:\.\d+)?)\s*\r?\n"
        r"\s*Unit Price:\s*(?:Rs\.?|INR)?\s*"
        r"([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    matches = pattern.findall(invoice_text)

    if not matches:
        raise ValueError("Could not extract any line items.")

    line_items = []

    for description, quantity, unit_price in matches:
        line_items.append({
            "description": description.strip(),
            "quantity": float(quantity.replace(",", "")),
            "unit_price": float(unit_price.replace(",", "")),
        })

    return line_items


def build_invoice(data, invoice_text):
    """Build a validated Invoice object from ground-truth data."""

    invoice_data = {
        "vendor": data["vendor"],
        "invoice_number": data["invoice_number"],
        "invoice_date": data["invoice_date"],
        "due_date": data.get("due_date"),
        "currency": data.get("currency", "INR"),
        "line_items": extract_line_items(invoice_text),
        "total": data["total"],
        "po_number": data.get("po_number"),
    }

    return Invoice.model_validate(invoice_data)


def simulate_database(data):
    """
    Replace database lookups with isolated in-memory test data.

    This prevents evaluation from changing the real SQLite database.
    """

    def mock_get_vendor(vendor_name):
        if vendor_name == data["vendor"]:
            if data["category"] == "unknown_vendor":
                return None

            return {"is_approved": True}

        return None

    def mock_get_purchase_order(vendor_name, po_number):
        if (
            vendor_name == data["vendor"]
            and po_number == data.get("po_number")
            and data.get("po_amount") is not None
        ):
            return {"po_amount": data["po_amount"]}

        return None

    def mock_find_duplicate(vendor_name, invoice_number):
        if data.get("duplicate_of") is not None:
            return {"id": 999}

        return None

    rules.get_vendor = mock_get_vendor
    rules.get_purchase_order = mock_get_purchase_order
    rules.find_duplicate = mock_find_duplicate


def calculate_metrics(tp, fp, fn):
    """Calculate precision, recall and F1 score."""

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0
    )

    return precision, recall, f1


def percentage(value):
    return f"{value * 100:.2f}%"


def evaluate_dataset():
    print("\nINVOICE RULES EVALUATION")
    print("=" * 65)
    print("Mode: Offline")
    print("AI API calls: None")
    print("Database writes: None")

    json_files = sorted(DATA_DIR.glob("inv_*.json"))

    if not json_files:
        print("\nERROR: No ground-truth JSON files found.")
        sys.exit(1)

    original_functions = (
        rules.get_vendor,
        rules.get_purchase_order,
        rules.find_duplicate,
    )

    total = 0
    skipped = 0

    route_correct = 0
    exact_issue_matches = 0

    issue_tp = 0
    issue_fp = 0
    issue_fn = 0

    security_tp = 0
    security_fp = 0
    security_fn = 0
    security_correct = 0

    category_results = defaultdict(
        lambda: {"total": 0, "correct": 0}
    )

    issue_counts = Counter()
    errors = []

    try:
        for json_file in json_files:

            text_file = DATA_DIR / (json_file.stem + ".txt")

            try:
                data = json.loads(
                    json_file.read_text(encoding="utf-8")
                )

                if not isinstance(data, dict) or not data.get("category"):
                    skipped += 1
                    continue

                if not text_file.exists():
                    raise FileNotFoundError(
                        f"Missing text file: {text_file.name}"
                    )

                invoice_text = text_file.read_text(encoding="utf-8")

                invoice = build_invoice(data, invoice_text)

                # Isolate all database lookups for this test case.
                simulate_database(data)

                report = rules.validate_invoice(
                    invoice,
                    invoice_text,
                )

                expected_issues = set(
                    data.get("expected_issues", [])
                )

                actual_issues = set(report["issues"])

                expected_route = data["expected_route"]
                actual_route = report["route"]

                total += 1

                category = data["category"]

                category_results[category]["total"] += 1

                if actual_route == expected_route:
                    route_correct += 1
                    category_results[category]["correct"] += 1

                if actual_issues == expected_issues:
                    exact_issue_matches += 1

                issue_tp += len(actual_issues & expected_issues)
                issue_fp += len(actual_issues - expected_issues)
                issue_fn += len(expected_issues - actual_issues)

                for issue in actual_issues:
                    issue_counts[issue] += 1

                expected_security = bool(data.get("security_flag"))
                actual_security = "prompt_injection" in actual_issues

                if expected_security and actual_security:
                    security_tp += 1
                elif not expected_security and actual_security:
                    security_fp += 1
                elif expected_security and not actual_security:
                    security_fn += 1

                if expected_security == actual_security:
                    security_correct += 1

                print(
                    f"[{total:03}] {json_file.name:<15} "
                    f"{category:<18} "
                    f"{'PASS' if actual_route == expected_route else 'FAIL'}"
                )

            except Exception as error:
                skipped += 1
                errors.append(
                    f"{json_file.name}: {type(error).__name__}: {error}"
                )

    finally:
        (
            rules.get_vendor,
            rules.get_purchase_order,
            rules.find_duplicate,
        ) = original_functions

    if total == 0:
        print("\nNo invoices could be evaluated.")
        sys.exit(1)

    precision, recall, f1 = calculate_metrics(
        issue_tp,
        issue_fp,
        issue_fn,
    )

    security_precision, security_recall, security_f1 = (
        calculate_metrics(
            security_tp,
            security_fp,
            security_fn,
        )
    )

    print("\n" + "=" * 65)
    print("FINAL EVALUATION REPORT")
    print("=" * 65)

    print(f"Dataset files found       : {len(json_files)}")
    print(f"Invoices evaluated        : {total}")
    print(f"Invoices skipped          : {skipped}")

    print("\nROUTING PERFORMANCE")
    print("-" * 65)

    print(f"Correct routing decisions : {route_correct}/{total}")
    print(
        "Routing accuracy         : "
        + percentage(route_correct / total)
    )

    print(
        f"Exact issue matches       : "
        f"{exact_issue_matches}/{total}"
    )
    print(
        "Exact issue accuracy     : "
        + percentage(exact_issue_matches / total)
    )

    print("\nISSUE DETECTION PERFORMANCE")
    print("-" * 65)

    print(f"True Positives            : {issue_tp}")
    print(f"False Positives           : {issue_fp}")
    print(f"False Negatives           : {issue_fn}")
    print(f"Precision                 : {percentage(precision)}")
    print(f"Recall                    : {percentage(recall)}")
    print(f"F1 Score                  : {percentage(f1)}")

    print("\nPROMPT INJECTION DETECTION")
    print("-" * 65)

    print(f"True Positives            : {security_tp}")
    print(f"False Positives           : {security_fp}")
    print(f"False Negatives           : {security_fn}")
    print(f"Detection accuracy        : {percentage(security_correct / total)}")
    print(f"Precision                 : {percentage(security_precision)}")
    print(f"Recall                    : {percentage(security_recall)}")
    print(f"F1 Score                  : {percentage(security_f1)}")

    print("\nCATEGORY-WISE ROUTING RESULTS")
    print("-" * 65)

    for category, result in sorted(category_results.items()):
        count = result["total"]
        correct = result["correct"]
        accuracy = correct / count if count else 0

        print(
            f"{category:<20} "
            f"{correct}/{count} "
            f"({percentage(accuracy)})"
        )

    print("\nDETECTED ISSUE COUNTS")
    print("-" * 65)

    for issue, count in sorted(issue_counts.items()):
        print(f"{issue:<35}: {count}")

    if errors:
        print("\nEVALUATION ERRORS")
        print("-" * 65)

        for error in errors:
            print(error)

    print("\n" + "=" * 65)
    print("OFFLINE EVALUATION COMPLETED")
    print("=" * 65)


if __name__ == "__main__":
    evaluate_dataset()
