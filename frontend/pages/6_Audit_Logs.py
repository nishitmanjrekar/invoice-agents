import pandas as pd
import streamlit as st

from api_client import ApiError, get_invoice, get_invoices
from components.formatting import fmt_timestamp, parse_audit_details
from components.status import get_status_label, label_tone, style_tone_columns
from components.ui import empty_state, field_grid, kpi_card, page_header, section_title


def load_invoices():
    return get_invoices()


def load_invoice(invoice_id):
    return get_invoice(invoice_id)


actions = page_header(
    "Audit Logs",
    "Audit history for a single invoice, recorded by the backend.",
)

if actions.button("Refresh", icon=":material/refresh:", width="stretch"):
    st.cache_data.clear()
    st.rerun()

try:
    invoices = load_invoices()
except ApiError as exc:
    st.error(f"Unable to load invoices: {exc}")
    st.stop()

if not invoices:
    empty_state(
        "No invoices have been processed yet.",
        "Audit entries are recorded once an invoice exists.",
    )
    st.stop()

invoices = sorted(invoices, key=lambda i: i["id"], reverse=True)
by_id = {i["id"]: i for i in invoices}


def describe(invoice_id):
    inv = by_id[invoice_id]
    return (
        f"#{inv['id']} · {inv.get('invoice_number')} · "
        f"{inv.get('vendor_name')} · {get_status_label(inv.get('status'))}"
    )


with st.container(key="card_audit_filters"):
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    invoice_id = c1.selectbox("Invoice", options=list(by_id.keys()), format_func=describe)
    newest_first = c2.checkbox("Newest first", value=False)

try:
    detail = load_invoice(invoice_id)
except ApiError as exc:
    st.error(f"Could not load audit history for invoice {invoice_id}: {exc}")
    st.stop()

audit = detail.get("audit_history", [])

st.write("")

if not audit:
    empty_state("No audit entries exist for this invoice.")
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

with st.container(key="card_audit_search"):
    s1, s2, s3 = st.columns([2, 2, 2])
    search = s1.text_input("Search", placeholder="Action, actor or details")
    action_choice = s2.multiselect("Filter by action", options=sorted(df["Action"].dropna().unique()))
    actor_choice = s3.multiselect("Filter by actor", options=sorted(df["Actor"].dropna().unique()))

if action_choice:
    df = df[df["Action"].isin(action_choice)]
if actor_choice:
    df = df[df["Actor"].isin(actor_choice)]
if search.strip():
    needle = search.strip().lower()
    haystack = df[["Action", "Actor", "Details"]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    df = df[haystack.str.contains(needle, regex=False)]

df = df.drop(columns="_sort").reset_index(drop=True)

st.write("")
m1, m2, m3 = st.columns(3)
with m1:
    kpi_card("Entries shown", f"{len(df)} of {len(audit)}", small=True)
with m2:
    kpi_card("Current status", get_status_label(by_id[invoice_id].get("status")), small=True)
with m3:
    kpi_card("Distinct actors", df["Actor"].nunique() if not df.empty else 0, small=True)

st.write("")

if df.empty:
    empty_state("No entries match the current filters.", "Clear a filter or change the search text.")
else:
    with st.container(key="card_audit_table"):
        section_title("Audit history", "Select a row to see the full event.")
        event = st.dataframe(
            style_tone_columns(df, {"Previous status": label_tone, "New status": label_tone}),
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key=f"audit_table_{invoice_id}",
        )
        st.download_button(
            "Download as CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"audit_invoice_{invoice_id}.csv",
            mime="text/csv",
            icon=":material/download:",
        )

    if event.selection.rows and event.selection.rows[0] < len(df):
        row = df.iloc[event.selection.rows[0]]
        st.write("")
        with st.container(key="card_audit_event"):
            section_title("Event details")
            field_grid([
                ("Action", row["Action"]),
                ("Actor", row["Actor"]),
                ("Timestamp", row["Timestamp"]),
                ("Previous status", row["Previous status"]),
                ("New status", row["New status"]),
            ])
            if row["Details"]:
                st.write("")
                st.caption("Details")
                st.code(row["Details"], language=None)

st.caption(
    "Entries are reached through an invoice ID. Events with no invoice "
    "(for example PROCESSING_FAILED, or review cases for unknown vendors) "
    "are stored by the backend but cannot be listed here without a new "
    "endpoint. Review cases for duplicates appear under the original invoice."
)
