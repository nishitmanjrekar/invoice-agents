import pandas as pd
import streamlit as st

from components.formatting import fmt_timestamp, parse_audit_details
from components.status import label_tone, style_tone_columns
from components.ui import esc, field_grid, money, status_pill


def _text(value):
    return "" if value is None else str(value)


def _money(currency, amount):
    return money(currency, amount)


def render_invoice_detail(data, compact=False, vendor_invoice_count=None):
    """Shared invoice detail view. `data` is the GET /invoices/{id} response."""
    invoice = data.get("invoice", {})
    audit = data.get("audit_history", [])

    extracted = invoice.get("extracted_data")
    if not isinstance(extracted, dict):
        extracted = {}

    vendor = extracted.get("vendor") or invoice.get("vendor_name")

    st.markdown(
        '<div class="ia-item-head"><div class="ia-item-title">'
        f"{esc(_text(invoice.get('invoice_number')))} &middot; {esc(_text(vendor))}"
        f"</div>{status_pill(invoice.get('status'))}</div>",
        unsafe_allow_html=True,
    )

    names = ["Extracted data", "Line items"]
    if not compact:
        names += ["Vendor", "Purchase order"]
    names.append("Audit history")
    tabs = dict(zip(names, st.tabs(names)))

    with tabs["Extracted data"]:
        field_grid(
            [
                ("Vendor", vendor),
                ("Invoice number", extracted.get("invoice_number")
                 or invoice.get("invoice_number")),
                ("Invoice date", extracted.get("invoice_date")
                 or invoice.get("invoice_date")),
                ("Due date", extracted.get("due_date") or invoice.get("due_date")),
                ("Currency", extracted.get("currency") or invoice.get("currency")),
                ("Total", _money(invoice.get("currency"), invoice.get("total"))),
                ("PO number", extracted.get("po_number") or invoice.get("po_number")),
                ("Recorded at", fmt_timestamp(invoice.get("created_at"))),
            ],
            numeric=("Total",),
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
                column_config={
                    "Unit price": st.column_config.NumberColumn(format="%.2f"),
                    "Line total": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    if not compact:
        with tabs["Vendor"]:
            field_grid(
                [
                    ("Name", vendor),
                    ("Vendor ID", invoice.get("vendor_id")),
                    ("Invoices on record for this vendor", vendor_invoice_count),
                ]
            )
            st.caption(
                "The backend has no vendor endpoint yet, so only the fields "
                "returned with the invoice are shown."
            )

        with tabs["Purchase order"]:
            po = extracted.get("po_number") or invoice.get("po_number")
            field_grid(
                [
                    ("PO number", _text(po) or "None on invoice"),
                    ("Invoice total", _money(invoice.get("currency"), invoice.get("total"))),
                ],
                numeric=("Invoice total",),
            )
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
            table = pd.DataFrame(rows)
            st.dataframe(
                style_tone_columns(
                    table,
                    {"Previous status": label_tone, "New status": label_tone},
                ),
                hide_index=True,
                width="stretch",
            )
