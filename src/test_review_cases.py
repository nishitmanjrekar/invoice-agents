
import json
import sqlite3
import tempfile
from contextlib import closing, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import src.db as db_module
import src.review as review_module
from src.schemas import Invoice, LineItem


def create_test_database(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY
            );

            CREATE TABLE invoice_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                vendor_name TEXT NOT NULL,
                case_type TEXT NOT NULL,
                invoice_data TEXT NOT NULL,
                validation_report TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                existing_invoice_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                reviewer_reason TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                FOREIGN KEY (existing_invoice_id) REFERENCES invoices(id),
                CHECK (case_type IN ('unknown_vendor', 'duplicate')),
                CHECK (status IN ('open', 'rejected', 'resolved'))
            );

            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()

def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        test_db = Path(temp_dir) / "review_test.db"
        create_test_database(test_db)
        original_connect = sqlite3.connect
        open_connections = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            open_connections.append(connection)
            return connection

        connect_patch = patch.object(
            sqlite3,
            "connect",
            side_effect=tracked_connect,
        )
        connect_patch.start()

        with (
            patch.object(db_module, "DB_PATH", test_db),
            patch.object(review_module, "DB_PATH", test_db),
        ):
            invoice = Invoice(
                vendor="Unknown Test Vendor",
                invoice_number="TEST-REVIEW-001",
                invoice_date=date(2026, 11, 13),
                currency="INR",
                line_items=[
                    LineItem(
                        description="Test item",
                        quantity=1,
                        unit_price=100,
                    )
                ],
                total=100,
                po_number="PO-TEST-001",
            )

            report = {
                "invoice_number": invoice.invoice_number,
                "vendor": invoice.vendor,
                "risk_level": "high",
                "checks": {
                    "duplicate": {
                        "is_duplicate": False,
                        "existing_invoice_id": None,
                    }
                },
                "issues": ["unknown_or_unapproved_vendor"],
            }

            print("TEST 1: CREATE REVIEW CASE")
            case_id = db_module.create_review_case(
                invoice=invoice,
                validation_report=report,
                case_type="unknown_vendor",
            )
            assert case_id > 0
            print(f"PASS - Review case created: {case_id}")

            print("\nTEST 2: LIST AND SHOW REVIEW CASE")
            with redirect_stdout(StringIO()):
                review_module.list_review_cases()
                review_module.show_review_case(case_id)
            print("PASS - Case listing and details work")

            print("\nTEST 3: REJECT REVIEW CASE")
            with redirect_stdout(StringIO()):
                review_module.reject_review_case(
                    case_id,
                    "Test rejection for integration verification",
                )

            with closing(sqlite3.connect(test_db)) as conn:
                case = conn.execute(
                    """
                    SELECT status, reviewer_reason, reviewed_at
                    FROM invoice_review_cases
                    WHERE id = ?
                    """,
                    (case_id,),
                ).fetchone()

                logs = conn.execute(
                    """
                    SELECT action, actor, details
                    FROM audit_logs
                    WHERE action = 'INVOICE_REVIEW_CASE_REJECTED'
                    """
                ).fetchall()

            assert case is not None
            assert case[0] == "rejected"
            assert case[1] == "Test rejection for integration verification"
            assert case[2] is not None
            assert len(logs) == 1

            log_details = json.loads(logs[0][2])
            assert log_details["case_id"] == case_id

            print("PASS - Case rejected and reviewer reason saved")
            print("PASS - Rejection audit log recorded")

            print("\nALL REVIEW CASE INTEGRATION TESTS PASSED")
            connect_patch.stop()

            for connection in open_connections:
                connection.close()

if __name__ == "__main__":
    main()
