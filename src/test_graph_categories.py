
import json
import re
from pathlib import Path
from unittest.mock import patch

import src.extract as extraction_module
from src.agents.invoice_graph import invoice_graph
from src.schemas import Invoice

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

TEST_CASES = [
    ("inv_001", "clean", "pending_approval", None),
    ("inv_091", "amount_mismatch", "human_review", "amount_mismatch"),
    ("inv_111", "duplicate", "human_review", "duplicate_invoice"),
    ("inv_126", "unknown_vendor", "human_review", "unknown_or_unapproved_vendor"),
    ("inv_141", "injection", "human_review", "prompt_injection"),
]


def build_offline_invoice(invoice_text, fixture_id):
    """Build an invoice from synthetic ground-truth JSON."""

    with open(DATA_DIR / f"{fixture_id}.json", encoding="utf-8") as file:
        data = json.load(file)

    pattern = re.compile(
        r"Description:\s*([^\r\n]+)\s*\r?\n"
        r"\s*Quantity:\s*([\d,]+(?:\.\d+)?)\s*\r?\n"
        r"\s*Unit Price:\s*(?:Rs\.?|INR)?\s*"
        r"([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    matches = pattern.findall(invoice_text)

    line_items = [
        {
            "description": description.strip(),
            "quantity": float(quantity.replace(",", "")),
            "unit_price": float(price.replace(",", "")),
        }
        for description, quantity, price in matches
    ]

    return Invoice.model_validate({
        "vendor": data["vendor"],
        "invoice_number": data["invoice_number"],
        "invoice_date": data["invoice_date"],
        "due_date": data.get("due_date"),
        "currency": data.get("currency", "INR"),
        "line_items": line_items,
        "total": data["total"],
        "po_number": data.get("po_number"),
    })


def run_category_test(fixture_id, category, expected_status, expected_issue):
    """Run one category through LangGraph without Gemini or real DB writes."""

    (DATA_DIR / f"{fixture_id}.txt").read_text(encoding="utf-8")

    with open(DATA_DIR / f"{fixture_id}.json", encoding="utf-8") as file:
        label = json.load(file)

    is_unknown = category == "unknown_vendor"
    is_duplicate = category == "duplicate"

    def offline_extract(text):
        return build_offline_invoice(text, fixture_id)

    with (
        patch.object(
            extraction_module,
            "extract_invoice",
            side_effect=offline_extract,
        ),
        patch(
            "src.rules.get_vendor",
            return_value=None if is_unknown else {
                "vendor_name": label["vendor"],
                "is_approved": True,
            },
        ),
        patch(
            "src.rules.find_duplicate",
            return_value=(
                {
                    "id": 999,
                    "invoice_number": label["invoice_number"],
                }
                if is_duplicate else None
            ),
        ),
        patch(
            "src.db.get_vendor",
            return_value=None if is_unknown else {
                "vendor_name": label["vendor"],
                "is_approved": True,
            },
        ),
        patch(
            "src.db.find_duplicate",
            return_value=(
                {
                    "id": 999,
                    "invoice_number": label["invoice_number"],
                }
                if is_duplicate else None
            ),
        ),
        patch(
            "src.db.save_invoice",
            return_value=1000,
        ) as save_mock,
        patch(
            "src.db.create_review_case",
            return_value=2000,
        ) as case_mock,
        patch("src.db.log_audit_event") as audit_mock,
    ):
        result = invoice_graph.invoke({
            "invoice_path": f"data/{fixture_id}.txt"
        })

        if "validation_report" not in result:
            print("\nDEBUG: GRAPH FAILURE")
            print("Result:", result)
            raise AssertionError(
                f"{category} failed before validation completed."
            )

        report = result["validation_report"]
        issues = report["issues"]

        assert result["status"] == expected_status, (
            f"{category}: expected {expected_status}, "
            f"got {result.get('status')}"
        )

        if expected_issue:
            assert expected_issue in issues, (
                f"{category}: expected issue {expected_issue}, "
                f"got {issues}"
            )

        if category == "clean":
            assert not issues
            save_mock.assert_called_once()
            case_mock.assert_not_called()

        elif category in ("amount_mismatch", "injection"):
            assert expected_issue in issues
            save_mock.assert_called_once()
            case_mock.assert_not_called()
            assert result["status"] == "human_review"

        elif category in ("duplicate", "unknown_vendor"):
            assert result["invoice_id"] is None
            assert result["review_case_id"] == 2000
            save_mock.assert_not_called()
            case_mock.assert_called_once()
            audit_mock.assert_not_called()

            expected_case_type = (
                "unknown_vendor" if is_unknown else "duplicate"
            )

            case_arguments = case_mock.call_args.kwargs

            assert case_arguments["case_type"] == expected_case_type
            assert case_arguments["invoice"] == result["invoice"]
            assert (
                case_arguments["validation_report"]
                == result["validation_report"]
            )

        print(
            f"{fixture_id} | {category:<16} | "
            f"{result['status']:<16} | PASS"
        )

        if expected_issue:
            print(f"    Detected issue: {expected_issue}")

        if category in ("duplicate", "unknown_vendor"):
            print(f"    Review case ID: {result['review_case_id']}")
            print(f"    Review case type: {expected_case_type}")

        print(f"    Validation issues: {issues}")


def main():
    print("LANGGRAPH CATEGORY EVALUATION")
    print("=" * 75)
    print("Mode: Offline")
    print("Gemini API calls: None")
    print("Real database writes: None")
    print()

    passed = 0

    for fixture_id, category, expected_status, expected_issue in TEST_CASES:
        run_category_test(
            fixture_id,
            category,
            expected_status,
            expected_issue,
        )
        passed += 1

    print("\nFINAL CATEGORY TEST REPORT")
    print("=" * 75)
    print(f"Categories passed: {passed}/{len(TEST_CASES)}")
    print("Gemini API calls: None")
    print("Real database writes: None")

    if passed == len(TEST_CASES):
        print("\nALL FIVE LANGGRAPH CATEGORIES PASSED")


if __name__ == "__main__":
    main()
