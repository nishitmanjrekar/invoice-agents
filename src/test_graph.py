
import json
import re
from pathlib import Path
from unittest.mock import patch

import src.extract as extraction_module
from src.agents.invoice_graph import invoice_graph
from src.schemas import Invoice

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TEST_INVOICE = "inv_004"


def build_offline_invoice(invoice_text):
    """Build an invoice using the dataset ground truth."""

    label_path = DATA_DIR / f"{TEST_INVOICE}.json"

    with open(label_path, encoding="utf-8") as file:
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


def offline_extract(invoice_text):
    """Replace Gemini extraction with local fixture data."""
    return build_offline_invoice(invoice_text)


def run_success_test():
    print("\nTEST 1: SUCCESSFUL PROCESSING")
    print("-" * 55)

    with (
        patch.object(
            extraction_module,
            "extract_invoice",
            side_effect=offline_extract,
        ),
        patch(
            "src.db.get_vendor",
            return_value={
                "vendor_name": "Acme Supplies",
                "is_approved": True,
            },
        ),
        patch("src.db.find_duplicate", return_value=None),
        patch("src.db.save_invoice", return_value=999),
        patch("src.db.log_audit_event"),
    ):
        result = invoice_graph.invoke({
            "invoice_path": f"data/{TEST_INVOICE}.txt"
        })

    assert result["status"] == "pending_approval"
    assert result["invoice_id"] == 999
    assert not result.get("error_stage")

    print("Status:", result["status"])
    print("Invoice ID:", result["invoice_id"])
    print("Database writes: Mocked")
    print("Result: PASS")

    return True


def run_extraction_failure_test():
    print("\nTEST 2: EXTRACTION FAILURE")
    print("-" * 55)

    with (
        patch.object(
            extraction_module,
            "extract_invoice",
            side_effect=RuntimeError(
                "Simulated Gemini 503 UNAVAILABLE"
            ),
        ),
        patch("src.db.log_audit_event") as audit_mock,
    ):
        result = invoice_graph.invoke({
            "invoice_path": f"data/{TEST_INVOICE}.txt"
        })

    assert result["status"] == "processing_failed"
    assert result["error_stage"] == "extraction"
    assert result["invoice_id"] is None
    assert "validation_report" not in result
    audit_mock.assert_called_once()

    print("Status:", result["status"])
    print("Failure stage:", result["error_stage"])
    print("Reason:", result["route_reason"])
    print("Invoice saved: No")
    print("Result: PASS")

    return True


def run_database_failure_test():
    print("\nTEST 3: DATABASE LOOKUP FAILURE")
    print("-" * 55)

    with (
        patch.object(
            extraction_module,
            "extract_invoice",
            side_effect=offline_extract,
        ),
        patch(
            "src.db.get_vendor",
            side_effect=RuntimeError(
                "Simulated SQLite connection failure"
            ),
        ),
        patch("src.db.log_audit_event") as audit_mock,
    ):
        result = invoice_graph.invoke({
            "invoice_path": f"data/{TEST_INVOICE}.txt"
        })

    assert result["status"] == "processing_failed"
    assert result["error_stage"] == "database_check"
    assert result["invoice_id"] is None
    assert "validation_report" in result
    audit_mock.assert_called_once()

    print("Status:", result["status"])
    print("Failure stage:", result["error_stage"])
    print("Reason:", result["route_reason"])
    print("Invoice saved: No")
    print("Result: PASS")

    return True


def main():
    print("MODE: OFFLINE")
    print("Gemini API calls: None")
    print("Real database writes: None")
    print("Test invoice:", TEST_INVOICE)

    results = [
        run_success_test(),
        run_extraction_failure_test(),
        run_database_failure_test(),
    ]

    print("\nFINAL TEST REPORT")
    print("=" * 55)
    print(f"Tests passed: {sum(results)}/{len(results)}")
    print("Gemini API calls: None")
    print("Real database writes: None")

    if all(results):
        print("\nALL LANGGRAPH ERROR-HANDLING TESTS PASSED")
    else:
        raise AssertionError("One or more LangGraph tests failed.")


if __name__ == "__main__":
    main()
