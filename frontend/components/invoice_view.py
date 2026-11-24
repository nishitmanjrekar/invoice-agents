import pandas as pd
import streamlit as st

from components.formatting import fmt_timestamp, parse_audit_details
from components.status import render_status


def _text(value):
    return "" if value is None else str(value)


def _money(currency, amount):
    try:
        return f"{currency or ''} {float(amount):,.2f}".strip()
    except (TypeError, ValueError):
        return _text(amount)


def render_invoice_detail(data, compact=False, vendor_invoice_count=None):
    """Shared invoice detail view. `data` is the GET /invoices/{id} response."""
    invoice = data.get("invoice", {})
    audit = data.get("audit_history", [])

    extracted = invoice.get("extracted_data")
    if not isinstance(extracted, dict):
        extracted = {}

    vendor = extracted.get("vendor") or invoice.get("vendor_name")

    st.subheader(f"{_text(invoice.get('invoice_number'))} · {_text(vendor)}")
    render_status(invoice.get("status"))

    names = ["Extracted data", "Line items"]
    if not compact:
        names += ["Vendor", "Purchase order"]
    names.append("Audit history")
    tabs = dict(zip(names, st.tabs(names)))

    with tabs["Extracted data"]:
        fields = {
            "Vendor": vendor,
            "Invoice number": extracted.get("invoice_number")
            or invoice.get("invoice_number"),
            "Invoice date": extracted.get("invoice_date")
            or invoice.get("invoice_date"),
            "Due date": extracted.get("due_date") or invoice.get("due_date"),
            "Currency": extracted.get("currency") or invoice.get("currency"),
            "Total": _money(invoice.get("currency"), invoice.get("total")),
            "PO number": extracted.get("po_number") or invoice.get("po_number"),
            "Recorded at": fmt_timestamp(invoice.get("created_at")),
        }
        st.dataframe(
            pd.DataFrame({
                "Field": list(fields.keys()),
                "Value": [_text(v) for v in fields.values()],
            }),
            hide_index=True,
            width="stretch",
        )

    with tabs["Line items"]:
        items = extracted.get("line_items") or []
        if not items:
            st.info("No line items available.")
        else:
            items_df = pd.DataFrame(items)
            for column in ("quantity", "unit_price"):
                items_df[column] = pd.to_numeric(
                    items_df.get(column), errors="coerce"
                )
            items_df["line_total"] = items_df["quantity"] * items_df["unit_price"]
            st.dataframe(
                items_df[["description", "quantity", "unit_price", "line_total"]]
                .rename(columns={
                    "description": "Description",
                    "quantity": "Quantity",
                    "unit_price": "Unit price",
                    "line_total": "Line total",
                }),
                hide_index=True,
                width="stretch",
            )

    if not compact:
        with tabs["Vendor"]:
            st.write(f"**Name:** {_text(vendor)}")
            st.write(f"**Vendor ID:** {_text(invoice.get('vendor_id'))}")
            if vendor_invoice_count is not None:
                st.write(f"**Invoices on record for this vendor:** "
                         f"{vendor_invoice_count}")
            st.caption(
                "The backend has no vendor endpoint yet, so only the fields "
                "returned with the invoice are shown."
            )

        with tabs["Purchase order"]:
            po = extracted.get("po_number") or invoice.get("po_number")
            st.write(f"**PO number:** {_text(po) or 'None on invoice'}")
            st.write("**Invoice total:** "
                     f"{_money(invoice.get('currency'), invoice.get('total'))}")
            st.caption(
                "The PO amount is not exposed by the API, so only the "
                "reference is shown."
            )

    with tabs["Audit history"]:
        if not audit:
            st.info("No audit entries for this invoice.")
        else:
            rows = []
            for entry in audit:
                parsed = parse_audit_details(entry.get("details"))
                rows.append({
                    "Action": entry.get("action"),
                    "Actor": entry.get("actor"),
                    "Timestamp": fmt_timestamp(entry.get("created_at")),
                    "Previous status": parsed["previous"],
                    "New status": parsed["new"],
                    "Details": parsed["text"],
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")