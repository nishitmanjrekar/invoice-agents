
import re
from decimal import Decimal, InvalidOperation

from src.db import (
    find_duplicate,
    get_purchase_order,
    get_vendor,
)
from src.schemas import Invoice

# Monetary comparison precision: one paisa.
MONEY_TOLERANCE = Decimal("0.01")


# Suspicious instruction patterns found in untrusted documents.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"reveal\s+(your\s+)?system\s+prompt",
    r"reveal\s+(your\s+)?hidden\s+instructions",
    r"ignore\s+your\s+security\s+rules",
    r"bypass\s+(all\s+)?validation",
    r"approve\s+this\s+invoice\s+automatically",
    r"override\s+(the\s+)?approval",
    r"send\s+(the\s+)?api\s+key",
    r"exfiltrate",
    r"execute\s+this\s+command",
    r"delete\s+(the\s+)?database",
    r"do\s+not\s+verify",
    r"mark\s+as\s+approved",
]


def to_decimal(value):
    """Convert a numeric value into Decimal safely."""

    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Invalid monetary value: {value}")


def check_vendor(invoice):
    """Check whether the invoice vendor is approved."""

    vendor = get_vendor(invoice.vendor)

    if vendor is None:
        return {
            "passed": False,
            "issue": "unknown_or_unapproved_vendor",
            "message": f"Vendor '{invoice.vendor}' is not registered."
        }

    if not vendor["is_approved"]:
        return {
            "passed": False,
            "issue": "unknown_or_unapproved_vendor",
            "message": f"Vendor '{invoice.vendor}' is not approved."
        }

    return {
        "passed": True,
        "issue": None,
        "message": "Vendor is registered and approved."
    }


def check_purchase_order(invoice):
    """Verify the purchase order belongs to the invoice vendor."""

    if not invoice.po_number:
        return {
            "passed": False,
            "issue": "missing_purchase_order",
            "message": "Invoice does not contain a purchase order number.",
            "purchase_order": None
        }

    purchase_order = get_purchase_order(
        invoice.vendor,
        invoice.po_number
    )

    if purchase_order is None:
        return {
            "passed": False,
            "issue": "purchase_order_not_found",
            "message": (
                f"Purchase order '{invoice.po_number}' "
                "was not found for this vendor."
            ),
            "purchase_order": None
        }

    return {
        "passed": True,
        "issue": None,
        "message": "Purchase order exists and belongs to the vendor.",
        "purchase_order": purchase_order
    }


def check_po_amount(invoice, purchase_order):
    """Compare invoice total against the purchase order amount."""

    if purchase_order is None:
        return {
            "passed": False,
            "issue": "po_amount_not_verified",
            "message": "Purchase order amount could not be verified."
        }

    invoice_amount = to_decimal(invoice.total)
    po_amount = to_decimal(purchase_order["po_amount"])

    difference = abs(invoice_amount - po_amount)

    if difference > MONEY_TOLERANCE:
        return {
            "passed": False,
            "issue": "amount_mismatch",
            "message": (
                f"Invoice amount {invoice_amount} INR does not match "
                f"purchase order amount {po_amount} INR."
            ),
            "invoice_amount": float(invoice_amount),
            "po_amount": float(po_amount),
            "difference": float(difference)
        }

    return {
        "passed": True,
        "issue": None,
        "message": "Invoice amount matches purchase order amount.",
        "invoice_amount": float(invoice_amount),
        "po_amount": float(po_amount)
    }


def check_line_item_total(invoice):
    """Verify that line-item calculations match the invoice total."""

    calculated_total = sum(
        (
            Decimal(str(item.quantity))
            * Decimal(str(item.unit_price))
            for item in invoice.line_items
        ),
        Decimal("0")
    ).quantize(Decimal("0.01"))

    invoice_total = to_decimal(invoice.total)

    difference = abs(calculated_total - invoice_total)

    if difference > MONEY_TOLERANCE:
        return {
            "passed": False,
            "issue": "line_item_total_mismatch",
            "message": (
                f"Calculated line-item total {calculated_total} INR "
                f"does not match invoice total {invoice_total} INR."
            ),
            "calculated_total": float(calculated_total),
            "invoice_total": float(invoice_total)
        }

    return {
        "passed": True,
        "issue": None,
        "message": "Line-item calculations match invoice total.",
        "calculated_total": float(calculated_total),
        "invoice_total": float(invoice_total)
    }


def check_duplicate(invoice):
    """Check whether the invoice has already been processed."""

    duplicate = find_duplicate(
        invoice.vendor,
        invoice.invoice_number
    )

    if duplicate:
        return {
            "passed": False,
            "issue": "duplicate_invoice",
            "message": (
                f"Invoice '{invoice.invoice_number}' "
                "has already been recorded."
            ),
            "existing_invoice_id": duplicate["id"]
        }

    return {
        "passed": True,
        "issue": None,
        "message": "No previously recorded duplicate found."
    }


def check_prompt_injection(invoice_text):
    """
    Scan untrusted invoice text for suspicious instructions.

    This is a defensive heuristic, not a complete prompt-injection
    detector. Any match requires human review.
    """

    text = re.sub(r"\s+", " ", invoice_text.lower())

    detected_patterns = []

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            detected_patterns.append(pattern)

    if detected_patterns:
        return {
            "passed": False,
            "issue": "prompt_injection",
            "message": (
                "Suspicious instructions were detected in invoice text."
            ),
            "detected_patterns": detected_patterns
        }

    return {
        "passed": True,
        "issue": None,
        "message": "No known suspicious instruction patterns detected."
    }


def determine_risk(issues):
    """Assign a risk level based on detected issues."""

    if "prompt_injection" in issues:
        return "critical"

    if any(issue in issues for issue in [
        "duplicate_invoice",
        "unknown_or_unapproved_vendor",
        "amount_mismatch",
        "line_item_total_mismatch"
    ]):
        return "high"

    if issues:
        return "medium"

    return "low"


def validate_invoice(invoice: Invoice, invoice_text: str):
    """
    Run all deterministic invoice validation checks.

    Returns a structured decision report.

    This function does not approve payments or write invoice records.
    """

    checks = {}

    checks["vendor"] = check_vendor(invoice)

    checks["purchase_order"] = check_purchase_order(invoice)

    purchase_order = checks["purchase_order"]["purchase_order"]

    checks["po_amount"] = check_po_amount(
        invoice,
        purchase_order
    )

    checks["line_items"] = check_line_item_total(invoice)

    checks["duplicate"] = check_duplicate(invoice)

    checks["security"] = check_prompt_injection(invoice_text)

    issues = []

    for check in checks.values():
        if not check["passed"] and check["issue"]:
            if check["issue"] not in issues:
                issues.append(check["issue"])

    risk_level = determine_risk(issues)

    # No automatic approval when any check fails.
    route = "auto_approve" if not issues else "human_review"

    return {
        "invoice_number": invoice.invoice_number,
        "vendor": invoice.vendor,
        "total": invoice.total,
        "currency": invoice.currency,
        "checks": checks,
        "issues": issues,
        "risk_level": risk_level,
        "route": route,
        "requires_human_review": bool(issues)
    }


def print_validation_report(report):
    """Print a readable invoice validation report."""

    print("\nINVOICE VALIDATION REPORT")
    print("=" * 55)

    print("Invoice Number:", report["invoice_number"])
    print("Vendor:", report["vendor"])
    print("Total:", report["currency"], report["total"])

    print("\nVALIDATION CHECKS")
    print("-" * 55)

    for check_name, result in report["checks"].items():

        status = "PASS" if result["passed"] else "FAIL"

        print(f"{check_name.upper():20}: {status}")
        print("  ", result["message"])

    print("\nFINAL DECISION")
    print("-" * 55)

    print("Issues:", report["issues"])
    print("Risk Level:", report["risk_level"].upper())
    print("Route:", report["route"])
    print(
        "Human Review Required:",
        report["requires_human_review"]
    )

    print("=" * 55)


if __name__ == "__main__":

    # This is a standalone rules-engine test.
    # It does not call Gemini or make an API request.

    sample_invoice = Invoice(
        vendor="Acme Supplies",
        invoice_number="RULES-TEST-001",
        invoice_date="2026-11-06",
        due_date="2026-12-06",
        currency="INR",
        line_items=[
            {
                "description": "Office chairs",
                "quantity": 2,
                "unit_price": 1500
            }
        ],
        total=3000,
        po_number="PO-2001"
    )

    sample_text = """
    Vendor: Acme Supplies
    Invoice Number: RULES-TEST-001
    Item: Office chairs
    Quantity: 2
    Unit Price: 1500
    Total: INR 3000
    """

    report = validate_invoice(
        sample_invoice,
        sample_text
    )

    print_validation_report(report)
