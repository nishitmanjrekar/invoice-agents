import pandas as pd
import streamlit as st
from api_client import ApiError, get_invoice, get_invoices
from components.formatting import fmt_timestamp, parse_audit_details
from components.status import get_status_label

st.title("Audit Logs")
st.caption("Audit history for a single invoice, recorded by the backend.")


@st.cache_data(ttl=30, show_spinner="Loading invoices...")
def load_invoices():
    return get_invoices()


@st.cache_data(ttl=10, show_spinner="Loading audit history...")
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

if not invoices:
    st.info("No invoices have been processed yet.")
    st.stop()

invoices = sorted(invoices, key=lambda i: i["id"], reverse=True)
by_id = {i["id"]: i for i in invoices}


def describe(invoice_id):
    inv = by_id[invoice_id]
    return (
        f"#{inv['id']} · {inv.get('invoice_number')} · "
        f"{inv.get('vendor_name')} · {get_status_label(inv.get('status'))}"
    )


c1, c2 = st.columns([3, 1])
invoice_id = c1.selectbox("Invoice", options=list(by_id.keys()), format_func=describe)
newest_first = c2.checkbox("Newest first", value=False)

try:
    detail = load_invoice(invoice_id)
except ApiError as exc:
    st.error(f"Could not load audit history for invoice {invoice_id}: {exc}")
    st.stop()

audit = detail.get("audit_history", [])

if not audit:
    st.info("No audit entries exist for this invoice.")
    st.stop()

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
        "_sort": entry.get("created_at") or "",
    })

df = pd.DataFrame(rows).sort_values("_sort", ascending=not newest_first)

actions = sorted(df["Action"].dropna().unique())
action_choice = st.multiselect("Filter by action", options=actions)
if action_choice:
    df = df[df["Action"].isin(action_choice)]

df = df.drop(columns="_sort").reset_index(drop=True)

m1, m2, m3 = st.columns(3)
with m1.container(border=True):
    st.metric("Entries shown", f"{len(df)} of {len(audit)}")
with m2.container(border=True):
    st.metric("Current status", get_status_label(by_id[invoice_id].get("status")))
with m3.container(border=True):
    st.metric("Distinct actors", df["Actor"].nunique() if not df.empty else 0)

if df.empty:
    st.info("No entries match the selected actions.")
else:
    st.dataframe(df, hide_index=True, width="stretch")
    st.download_button(
        "Download as CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"audit_invoice_{invoice_id}.csv",
        mime="text/csv",
        icon=":material/download:",
    )

st.caption(
    "Entries are reached through an invoice ID. Events with no invoice "
    "(for example PROCESSING_FAILED, or review cases for unknown vendors) "
    "are stored by the backend but cannot be listed here without a new "
    "endpoint. Review cases for duplicates appear under the original invoice."
)