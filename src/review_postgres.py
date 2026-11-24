
"""PostgreSQL implementation of invoice human review."""

import json
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.db_core import get_postgres_engine

REVIEWABLE_STATUSES = ("pending_approval", "human_review")


def _now():
    return datetime.now(UTC).isoformat()


def list_pending_invoices():
    engine = get_postgres_engine()

    with engine.connect() as connection:
        invoices = connection.execute(
            text("""
                SELECT
                    i.id,
                    i.invoice_number,
                    i.invoice_date,
                    i.vendor_id,
                    v.vendor_name,
                    i.total,
                    i.currency,
                    i.po_number,
                    i.status,
                    i.created_at
                FROM invoices i
                LEFT JOIN vendors v ON i.vendor_id = v.id
                WHERE i.status IN (:pending, :human_review)
                ORDER BY i.id
            """),
            {
                "pending": "pending_approval",
                "human_review": "human_review",
            },
        ).mappings().all()

    print("\nINVOICES AWAITING REVIEW")
    print("=" * 75)

    if not invoices:
        print("No invoices are currently awaiting review.")
        return

    for invoice in invoices:
        print(f"\nID: {invoice['id']}")
        print(f"Invoice Number: {invoice['invoice_number']}")
        print(f"Vendor: {invoice['vendor_name'] or 'Unknown'}")
        print(f"Date: {invoice['invoice_date']}")
        print(f"Amount: {invoice['currency']} {invoice['total']}")
        print(f"PO Number: {invoice['po_number']}")
        print(f"Status: {invoice['status']}")

        if invoice["status"] == "human_review":
            print(
                "WARNING: Approval blocked until validation "
                "issues are resolved."
            )

        print("-" * 75)


def show_invoice(invoice_id):
    engine = get_postgres_engine()

    with engine.connect() as connection:
        invoice = connection.execute(
            text("""
                SELECT i.*, v.vendor_name
                FROM invoices i
                LEFT JOIN vendors v ON i.vendor_id = v.id
                WHERE i.id = :invoice_id
            """),
            {"invoice_id": invoice_id},
        ).mappings().first()

        if invoice is None:
            print(f"Invoice ID {invoice_id} not found.")
            return

        logs = connection.execute(
            text("""
                SELECT action, actor, details, created_at
                FROM audit_logs
                WHERE invoice_id = :invoice_id
                ORDER BY id
            """),
            {"invoice_id": invoice_id},
        ).mappings().all()

    print("\nINVOICE DETAILS")
    print("=" * 55)
    print(f"ID: {invoice['id']}")
    print(f"Invoice Number: {invoice['invoice_number']}")
    print(f"Vendor: {invoice['vendor_name'] or 'Unknown'}")
    print(f"Invoice Date: {invoice['invoice_date']}")
    print(f"Due Date: {invoice['due_date']}")
    print(f"Amount: {invoice['currency']} {invoice['total']}")
    print(f"PO Number: {invoice['po_number']}")
    print(f"Status: {invoice['status']}")

    print("\nEXTRACTED DATA")
    print("-" * 55)

    try:
        extracted = json.loads(invoice["extracted_data"])
        print(json.dumps(extracted, indent=4, ensure_ascii=False))
    except (TypeError, json.JSONDecodeError):
        print(invoice["extracted_data"] or "No extracted data available.")

    print("\nAUDIT HISTORY")
    print("-" * 55)

    if not logs:
        print("No audit events found.")
    else:
        for log in logs:
            print(f"Action: {log['action']}")
            print(f"Actor: {log['actor']}")
            print(f"Details: {log['details']}")
            print(f"Time: {log['created_at']}")
            print("-" * 35)


def make_decision(invoice_id, decision, reason=None):
    """
    Record a human decision.

    pending_approval: approve or reject
    human_review: reject only
    """

    decision = str(decision).strip().lower()

    if decision == "approve":
        new_status = "approved"
    elif decision == "reject":
        new_status = "rejected"
    elif decision in ("approved", "rejected"):
        new_status = decision
    else:
        print("Invalid decision. Use approve or reject.")
        return False

    reason = str(reason or "").strip()

    if new_status == "rejected" and not reason:
        print("A rejection reason is required.")
        return False

    engine = get_postgres_engine()

    try:
        with engine.begin() as connection:
            invoice = connection.execute(
                text("""
                    SELECT id, invoice_number, status
                    FROM invoices
                    WHERE id = :invoice_id
                    FOR UPDATE
                """),
                {"invoice_id": invoice_id},
            ).mappings().first()

            if invoice is None:
                print(f"Invoice ID {invoice_id} not found.")
                return False

            previous_status = invoice["status"]

            if previous_status not in REVIEWABLE_STATUSES:
                print(
                    "Cannot review this invoice. "
                    f"Current status: {previous_status}"
                )
                return False

            if (
                previous_status == "human_review"
                and new_status == "approved"
            ):
                print(
                    "APPROVAL BLOCKED: This invoice has unresolved "
                    "validation or security issues."
                )
                return False

            timestamp = _now()

            result = connection.execute(
                text("""
                    UPDATE invoices
                    SET status = :new_status
                    WHERE id = :invoice_id
                      AND status = :previous_status
                """),
                {
                    "new_status": new_status,
                    "invoice_id": invoice_id,
                    "previous_status": previous_status,
                },
            )

            if result.rowcount != 1:
                print("Invoice status changed unexpectedly. Try again.")
                return False

            details = {
                "previous_status": previous_status,
                "new_status": new_status,
                "reason": reason,
                "timestamp": timestamp,
            }

            action_name = (
                "INVOICE_APPROVED"
                if new_status == "approved"
                else "INVOICE_REJECTED"
            )

            connection.execute(
                text("""
                    INSERT INTO audit_logs (
                        invoice_id,
                        action,
                        actor,
                        details,
                        created_at
                    )
                    VALUES (
                        :invoice_id,
                        :action,
                        :actor,
                        :details,
                        :created_at
                    )
                """),
                {
                    "invoice_id": invoice_id,
                    "action": action_name,
                    "actor": "human_reviewer",
                    "details": json.dumps(details),
                    "created_at": timestamp,
                },
            )

        print("\nDECISION RECORDED SUCCESSFULLY")
        print("=" * 45)
        print(f"Invoice: {invoice['invoice_number']}")
        print(f"Previous status: {previous_status}")
        print(f"New status: {new_status}")

        if reason:
            print(f"Reason: {reason}")

        return True

    except SQLAlchemyError as error:
        print(f"Database error: {error}")
        return False


def list_review_cases():
    engine = get_postgres_engine()

    with engine.connect() as connection:
        cases = connection.execute(
            text("""
                SELECT
                    id,
                    invoice_number,
                    vendor_name,
                    case_type,
                    risk_level,
                    status,
                    created_at
                FROM invoice_review_cases
                WHERE status = 'open'
                ORDER BY id
            """)
        ).mappings().all()

    print("\nOPEN INVOICE REVIEW CASES")
    print("=" * 75)

    if not cases:
        print("No open review cases found.")
        return

    for case in cases:
        print(f"\nCase ID: {case['id']}")
        print(f"Invoice Number: {case['invoice_number']}")
        print(f"Vendor: {case['vendor_name']}")
        print(f"Case Type: {case['case_type']}")
        print(f"Risk Level: {case['risk_level']}")
        print(f"Status: {case['status']}")
        print(f"Created: {case['created_at']}")
        print("-" * 75)


def show_review_case(case_id):
    engine = get_postgres_engine()

    with engine.connect() as connection:
        case = connection.execute(
            text("""
                SELECT *
                FROM invoice_review_cases
                WHERE id = :case_id
            """),
            {"case_id": case_id},
        ).mappings().first()

        if case is None:
            print(f"Review case ID {case_id} not found.")
            return

        logs = connection.execute(
            text("""
                SELECT action, actor, details, created_at
                FROM audit_logs
                WHERE details LIKE :pattern
                ORDER BY id
            """),
            {"pattern": f'%"case_id": {case_id}%' },
        ).mappings().all()

    print("\nINVOICE REVIEW CASE")
    print("=" * 60)
    print(f"Case ID: {case['id']}")
    print(f"Invoice Number: {case['invoice_number']}")
    print(f"Vendor: {case['vendor_name']}")
    print(f"Case Type: {case['case_type']}")
    print(f"Risk Level: {case['risk_level']}")
    print(f"Status: {case['status']}")
    print(f"Created: {case['created_at']}")
    print(f"Reviewed: {case['reviewed_at'] or 'Not reviewed'}")

    print("\nEXTRACTED INVOICE DATA")
    print("-" * 60)

    try:
        invoice_data = json.loads(case["invoice_data"])
        print(json.dumps(invoice_data, indent=4, ensure_ascii=False))
    except (TypeError, json.JSONDecodeError):
        print("Could not decode stored invoice data.")

    print("\nVALIDATION REPORT")
    print("-" * 60)

    try:
        report = json.loads(case["validation_report"])
        print(json.dumps(report, indent=4, ensure_ascii=False))
    except (TypeError, json.JSONDecodeError):
        print("Could not decode validation report.")

    print("\nREVIEWER DECISION")
    print("-" * 60)
    print(case["reviewer_reason"] or "No reviewer decision recorded.")

    print("\nAUDIT HISTORY")
    print("-" * 60)

    if not logs:
        print("No matching audit events found.")
    else:
        for log in logs:
            print(f"Action: {log['action']}")
            print(f"Actor: {log['actor']}")
            print(f"Details: {log['details']}")
            print(f"Time: {log['created_at']}")
            print("-" * 35)

    if case["case_type"] == "unknown_vendor":
        print(
            "\nWARNING: Vendor identity and approval must be verified "
            "before this invoice can enter normal processing."
        )
    elif case["case_type"] == "duplicate":
        print(
            "\nWARNING: Investigate the existing invoice before "
            "taking further action."
        )


def reject_review_case(case_id, reason):
    reason = str(reason or "").strip()

    if not reason:
        print("A rejection reason is required.")
        return False

    engine = get_postgres_engine()

    try:
        with engine.begin() as connection:
            case = connection.execute(
                text("""
                    SELECT
                        id,
                        invoice_number,
                        case_type,
                        status,
                        existing_invoice_id
                    FROM invoice_review_cases
                    WHERE id = :case_id
                    FOR UPDATE
                """),
                {"case_id": case_id},
            ).mappings().first()

            if case is None:
                print(f"Review case ID {case_id} not found.")
                return False

            if case["status"] != "open":
                print(
                    "Cannot reject this case. "
                    f"Current status: {case['status']}"
                )
                return False

            timestamp = _now()

            result = connection.execute(
                text("""
                    UPDATE invoice_review_cases
                    SET status = 'rejected',
                        reviewer_reason = :reason,
                        reviewed_at = :reviewed_at
                    WHERE id = :case_id
                      AND status = 'open'
                """),
                {
                    "reason": reason,
                    "reviewed_at": timestamp,
                    "case_id": case_id,
                },
            )

            if result.rowcount != 1:
                print("Review case changed unexpectedly. Try again.")
                return False

            details = {
                "case_id": case_id,
                "invoice_number": case["invoice_number"],
                "case_type": case["case_type"],
                "previous_status": "open",
                "new_status": "rejected",
                "reason": reason,
                "timestamp": timestamp,
            }

            connection.execute(
                text("""
                    INSERT INTO audit_logs (
                        invoice_id,
                        action,
                        actor,
                        details,
                        created_at
                    )
                    VALUES (
                        :invoice_id,
                        :action,
                        :actor,
                        :details,
                        :created_at
                    )
                """),
                {
                    "invoice_id": case["existing_invoice_id"],
                    "action": "INVOICE_REVIEW_CASE_REJECTED",
                    "actor": "human_reviewer",
                    "details": json.dumps(details),
                    "created_at": timestamp,
                },
            )

        print("\nREVIEW CASE REJECTED SUCCESSFULLY")
        print("=" * 50)
        print(f"Case ID: {case_id}")
        print(f"Invoice Number: {case['invoice_number']}")
        print(f"Case Type: {case['case_type']}")
        print("Previous status: open")
        print("New status: rejected")
        print(f"Reason: {reason}")

        return True

    except SQLAlchemyError as error:
        print(f"Database error: {error}")
        return False
