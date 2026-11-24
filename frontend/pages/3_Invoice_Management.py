import pandas as pd
import streamlit as st
from api_client import ApiError, get_invoice, get_invoices
from components.invoice_view import render_invoice_detail
from components.status import get_status_label

st.title("Invoice Management")
st.caption("Search, filter and inspect invoices.")


@st.cache_data(ttl=30, show_spinner="Loading invoices...")
def load_invoices():
    return get_invoices()


@st.cache_data(ttl=15, show_spinner="Loading invoice...")
def load_invoice(invoice_id):
    return get_invoice(invoice_id)


if st.button("Refresh", icon=":material/refresh:"):
    st.cache_data.clear()
    st.rerun()

try:
    invoices = load_invoices()
except ApiError as exc:
    st.error(f"Unable to load invoices: {exc}")
    st.stop()

df = pd.DataFrame(invoices)

if df.empty:
    st.info("No invoices have been processed yet.")
    st.stop()

df["status"] = df["status"].astype(str).str.lower()
df["status_label"] = df["status"].apply(get_status_label)
df["invoice_date_parsed"] = pd.to_datetime(df["invoice_date"], errors="coerce").dt.date

# ---------------------------------------------------------------- filters
f1, f2, f3 = st.columns([2, 2, 2])
query = f1.text_input(
    "Search",
    placeholder="Invoice ID, invoice number, vendor or PO number",
)
status_choice = f2.multiselect(
    "Status", sorted(df["status_label"].unique())
)
vendor_choice = f3.multiselect(
    "Vendor", sorted(df["vendor_name"].dropna().unique())
)

date_range = None
if st.checkbox("Filter by invoice date"):
    valid_dates = df["invoice_date_parsed"].dropna()
    if not valid_dates.empty:
        date_range = st.date_input(
            "Invoice date range",
            value=(valid_dates.min(), valid_dates.max()),
            min_value=valid_dates.min(),
            max_value=valid_dates.max(),
        )

mask = pd.Series(True, index=df.index)

if query.strip():
    haystack = (
        df[["id", "invoice_number", "vendor_name", "po_number"]]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .str.lower()
    )
    mask &= haystack.str.contains(query.strip().lower(), regex=False)

if status_choice:
    mask &= df["status_label"].isin(status_choice)

if vendor_choice:
    mask &= df["vendor_name"].isin(vendor_choice)

if date_range and len(date_range) == 2:
    start, end = date_range
    mask &= df["invoice_date_parsed"].between(start, end)

view = df[mask].sort_values("id", ascending=False).reset_index(drop=True)

st.caption(f"Showing {len(view)} of {len(df)} invoices. Click a row to see details.")

if view.empty:
    st.info("No invoices match the current filters.")
    st.stop()

# ------------------------------------------------------------------ table
table = view[[
    "id", "invoice_number", "vendor_name", "invoice_date",
    "currency", "total", "po_number", "status_label",
]].rename(columns={
    "id": "Invoice ID",
    "invoice_number": "Invoice number",
    "vendor_name": "Vendor",
    "invoice_date": "Date",
    "currency": "Cur.",
    "total": "Amount",
    "po_number": "PO number",
    "status_label": "Status",
})

event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="invoice_table",
    column_config={"Amount": st.column_config.NumberColumn(format="%.2f")},
)

selected_rows = event.selection.rows

if not selected_rows or selected_rows[0] >= len(view):
    st.info("Select an invoice row to view its details.")
    st.stop()

selected = view.iloc[selected_rows[0]]
invoice_id = int(selected["id"])

st.divider()

try:
    detail = load_invoice(invoice_id)
except ApiError as exc:
    st.error(f"Could not load invoice {invoice_id}: {exc}")
    st.stop()

vendor_count = int((df["vendor_name"] == selected["vendor_name"]).sum())
render_invoice_detail(detail, vendor_invoice_count=vendor_count)