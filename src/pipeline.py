
import argparse
import json
import sys
from pathlib import Path

from src.db import (
    PROJECT_ROOT,
    find_duplicate,
    get_vendor,
    log_audit_event,
    save_invoice,
)
from src.extract import extract_invoice
from src.rules import print_validation_report, validate_invoice


def process_invoice(file_path: str | Path) -> None:
    path = Path(file_path)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"Invoice file not found: {path}")

    print("\nINVOICE PROCESSING PIPELINE")
    print("=" * 55)
    print(f"File: {path.name}")

    # Step 1: Read the original invoice
    invoice_text = path.read_text(encoding="utf-8")

    if not invoice_text.strip():
        raise ValueError("Invoice file is empty.")

    # Step 2: Extract invoice information using Gemini
    print("\n[1/4] Extracting invoice information with Gemini...")

    invoice = extract_invoice(invoice_text)

    print("Extraction completed.")
    print(f"Vendor: {invoice.vendor}")
    print(f"Invoice Number: {invoice.invoice_number}")
    print(f"Total: {invoice.currency} {invoice.total:.2f}")

    # Step 3: Run deterministic business and security checks
    print("\n[2/4] Running validation checks...")

    report = validate_invoice(invoice, invoice_text)

    print_validation_report(report)

    # Step 4: Decide whether the invoice can enter the database
    print("\n[3/4] Checking database eligibility...")

    vendor = get_vendor(invoice.vendor)
    duplicate = find_duplicate(
        invoice.vendor,
        invoice.invoice_number,
    )

    if not vendor or not vendor.get("is_approved"):
        status = "human_review"
        action = "UNKNOWN_VENDOR_REVIEW"
        message = "Invoice held because the vendor is not approved."

        log_audit_event(
            None,
            action,
            "SYSTEM",
            json.dumps({
                "file": path.name,
                "vendor": invoice.vendor,
                "invoice_number": invoice.invoice_number,
                "reason": message,
            }),
        )

        print(message)

    elif duplicate:
        status = "human_review"
        action = "DUPLICATE_INVOICE_REVIEW"
        message = "Invoice held because a matching invoice already exists."

        log_audit_event(
            None,
            action,
            "SYSTEM",
            json.dumps({
                "file": path.name,
                "vendor": invoice.vendor,
                "invoice_number": invoice.invoice_number,
                "reason": message,
            }),
        )

        print(message)

    else:
        # Never automatically approve or pay an invoice.
        status = (
            "human_review"
            if report["requires_human_review"]
            else "pending_approval"
        )

        save_invoice(invoice, status=status)

        print(f"Invoice saved successfully with status: {status}")

    # Step 5: Display final processing result
    print("\n[4/4] PROCESSING SUMMARY")
    print("=" * 55)
    print(f"Invoice: {invoice.invoice_number}")
    print(f"Vendor: {invoice.vendor}")
    print(f"Validation route: {report['route']}")
    print(f"Database status: {status}")
    print(f"Risk level: {report['risk_level']}")
    print(f"Human review required: {report['requires_human_review']}")

    print("\nPIPELINE EXECUTION COMPLETED")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(
        description="Process an invoice using the AI invoice agent."
    )

    parser.add_argument(
        "invoice",
        nargs="?",
        help="Path to an invoice text file.",
    )

    args = parser.parse_args()

    if args.invoice:
        selected_file = args.invoice
    else:
        invoice_files = sorted(
            (PROJECT_ROOT / "data").glob("inv_*.txt")
        )

        if not invoice_files:
            print("ERROR: No invoice files found in the data directory.")
            sys.exit(1)

        selected_file = invoice_files[0]

    try:
        process_invoice(selected_file)

    except Exception as error:
        print("\nPIPELINE EXECUTION FAILED")
        print(f"Error: {type(error).__name__}: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
