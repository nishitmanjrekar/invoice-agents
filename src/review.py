import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from src import db_core
from src.db_core import DB_PATH, PROJECT_ROOT, connect  # noqa: F401

REVIEWABLE_STATUSES = ("pending_approval", "human_review")


def get_connection():
    return connect(require_exists=True)

def _postgres():
    return db_core.get_database_backend() == "postgresql"


def _get_postgres_review():
    from src import review_postgres
    return review_postgres

# ============================================================
# NORMAL INVOICE REVIEW
# ============================================================

def list_pending_invoices():

    if _postgres():
        return _get_postgres_review().list_pending_invoices()

    with closing(get_connection()) as connection:
        placeholders = ",".join("?" for _ in REVIEWABLE_STATUSES)

        invoices = connection.execute(
            f"""
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
            WHERE i.status IN ({placeholders})
            ORDER BY i.id
            """,
            REVIEWABLE_STATUSES,
        ).fetchall()

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

    if _postgres():
        return _get_postgres_review().show_invoice(invoice_id)

    with closing(get_connection()) as connection:
        invoice = connection.execute(
            """
            SELECT i.*, v.vendor_name
            FROM invoices i
            LEFT JOIN vendors v ON i.vendor_id = v.id
            WHERE i.id = ?
            """,
            (invoice_id,),
        ).fetchone()

        if invoice is None:
            print(f"Invoice ID {invoice_id} not found.")
            return

        logs = connection.execute(
            """
            SELECT action, actor, details, created_at
            FROM audit_logs
            WHERE invoice_id = ?
            ORDER BY id
            """,
            (invoice_id,),
        ).fetchall()

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


def make_decision(invoice_id, decision, reason=None, actor="human_reviewer"):
    """
    Record a human decision.

    pending_approval: approve or reject
    human_review: reject only
    """
    if _postgres():
        return _get_postgres_review().make_decision(
            invoice_id, decision, reason, actor
        )

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

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        invoice = connection.execute(
            """
            SELECT id, invoice_number, status
            FROM invoices
            WHERE id = ?
            """,
            (invoice_id,),
        ).fetchone()

        if invoice is None:
            print(f"Invoice ID {invoice_id} not found.")
            connection.rollback()
            return False

        previous_status = invoice["status"]

        if previous_status not in REVIEWABLE_STATUSES:
            print(
                f"Cannot review this invoice. "
                f"Current status: {previous_status}"
            )
            connection.rollback()
            return False

        if (
            previous_status == "human_review"
            and new_status == "approved"
        ):
            print(
                "APPROVAL BLOCKED: This invoice has unresolved "
                "validation or security issues."
            )
            connection.rollback()
            return False

        timestamp = datetime.now(UTC).isoformat()

        cursor = connection.execute(
            """
            UPDATE invoices
            SET status = ?
            WHERE id = ? AND status = ?
            """,
            (new_status, invoice_id, previous_status),
        )

        if cursor.rowcount != 1:
            connection.rollback()
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
            """
            INSERT INTO audit_logs (
                invoice_id,
                action,
                actor,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                action_name,
                actor,
                json.dumps(details),
                timestamp,
            ),
        )

        connection.commit()

        print("\nDECISION RECORDED SUCCESSFULLY")
        print("=" * 45)
        print(f"Invoice: {invoice['invoice_number']}")
        print(f"Previous status: {previous_status}")
        print(f"New status: {new_status}")

        if reason:
            print(f"Reason: {reason}")

        return True

    except sqlite3.Error as error:
        connection.rollback()
        print(f"Database error: {error}")
        return False

    finally:
        connection.close()


# ============================================================
# REVIEW CASE MANAGEMENT
# ============================================================

def list_review_cases():

    if _postgres():
        return _get_postgres_review().list_review_cases()

    with get_connection() as connection:
        cases = connection.execute(
            """
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
            """
        ).fetchall()

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

    if _postgres():
        return _get_postgres_review().show_review_case(case_id)

    with get_connection() as connection:
        case = connection.execute(
            """
            SELECT *
            FROM invoice_review_cases
            WHERE id = ?
            """,
            (case_id,),
        ).fetchone()

        if case is None:
            print(f"Review case ID {case_id} not found.")
            return

        logs = connection.execute(
            """
            SELECT action, actor, details, created_at
            FROM audit_logs
            WHERE details LIKE ?
            ORDER BY id
            """,
            (f'%"case_id": {case_id}%',),
        ).fetchall()

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


def reject_review_case(case_id, reason, actor="human_reviewer"):

    if _postgres():
        return _get_postgres_review().reject_review_case(case_id, reason, actor)

    reason = str(reason or "").strip()

    if not reason:
        print("A rejection reason is required.")
        return False

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        case = connection.execute(
            """
            SELECT id, invoice_number, case_type, status,
                   existing_invoice_id
            FROM invoice_review_cases
            WHERE id = ?
            """,
            (case_id,),
        ).fetchone()

        if case is None:
            print(f"Review case ID {case_id} not found.")
            connection.rollback()
            return False

        if case["status"] != "open":
            print(
                f"Cannot reject this case. "
                f"Current status: {case['status']}"
            )
            connection.rollback()
            return False

        timestamp = datetime.now(UTC).isoformat()

        cursor = connection.execute(
            """
            UPDATE invoice_review_cases
            SET status = 'rejected',
                reviewer_reason = ?,
                reviewed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (reason, timestamp, case_id),
        )

        if cursor.rowcount != 1:
            connection.rollback()
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
            """
            INSERT INTO audit_logs (
                invoice_id,
                action,
                actor,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                case["existing_invoice_id"],
                "INVOICE_REVIEW_CASE_REJECTED",
                actor,
                json.dumps(details),
                timestamp,
            ),
        )

        connection.commit()

        print("\nREVIEW CASE REJECTED SUCCESSFULLY")
        print("=" * 50)
        print(f"Case ID: {case_id}")
        print(f"Invoice Number: {case['invoice_number']}")
        print(f"Case Type: {case['case_type']}")
        print("Previous status: open")
        print("New status: rejected")
        print(f"Reason: {reason}")

        return True

    except sqlite3.Error as error:
        connection.rollback()
        print(f"Database error: {error}")
        return False

    finally:
        connection.close()


# ============================================================
# COMMAND LINE INTERFACE
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Invoice human review and approval system"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "list",
        help="List invoices awaiting review",
    )

    show_parser = subparsers.add_parser(
        "show",
        help="Show invoice details",
    )
    show_parser.add_argument("invoice_id", type=int)

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve an eligible invoice",
    )
    approve_parser.add_argument("invoice_id", type=int)

    reject_parser = subparsers.add_parser(
        "reject",
        help="Reject an invoice",
    )
    reject_parser.add_argument("invoice_id", type=int)
    reject_parser.add_argument(
        "--reason",
        required=True,
        help="Reason for rejecting the invoice",
    )

    subparsers.add_parser(
        "cases",
        help="List open invoice review cases",
    )

    show_case_parser = subparsers.add_parser(
        "show-case",
        help="Show review case details",
    )
    show_case_parser.add_argument("case_id", type=int)

    reject_case_parser = subparsers.add_parser(
        "reject-case",
        help="Reject an invoice review case",
    )
    reject_case_parser.add_argument("case_id", type=int)
    reject_case_parser.add_argument(
        "--reason",
        required=True,
        help="Reason for rejecting the review case",
    )

    args = parser.parse_args()

    try:
        if args.command == "list":
            list_pending_invoices()

        elif args.command == "show":
            show_invoice(args.invoice_id)

        elif args.command == "approve":
            success = make_decision(args.invoice_id, "approve")
            if not success:
                sys.exit(1)

        elif args.command == "reject":
            success = make_decision(
                args.invoice_id,
                "reject",
                args.reason,
            )
            if not success:
                sys.exit(1)

        elif args.command == "cases":
            list_review_cases()

        elif args.command == "show-case":
            show_review_case(args.case_id)

        elif args.command == "reject-case":
            success = reject_review_case(
                args.case_id,
                args.reason,
            )
            if not success:
                sys.exit(1)

    except (sqlite3.Error, SQLAlchemyError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
