
import api_client
import pandas as pd
import streamlit as st
from api_client import ApiError
from components.formatting import fmt_timestamp
from components.status import get_status_label
from components.validation_view import parse_report, render_validation_report

state = st.session_state
state.setdefault("rc_results", {})        # case_id -> (kind, text)
state.setdefault("rc_attempted", set())   # case_ids with a request sent
state.setdefault("rc_confirm", None)      # case_id awaiting confirmation
state.setdefault("rc_field_errors", {})   # case_id -> message

CASE_TYPE_LABELS = {
    "unknown_vendor": "Unknown vendor",
    "duplicate": "Duplicate",
}


# -------------------------------------------------------------- callbacks
def ask_confirm(case_id):
    reason = (state.get(f"rc_reason_{case_id}") or "").strip()
    if not reason:
        state.rc_field_errors[case_id] = "A rejection reason is required."
        state.rc_confirm = None
        return
    state.rc_field_errors.pop(case_id, None)
    state.rc_confirm = case_id


def cancel_confirm():
    state.rc_confirm = None


def execute(case_id, label):
    """Runs once per click. The case is locked before the request is sent."""
    state.rc_confirm = None

    if case_id in state.rc_attempted:
        return
    state.rc_attempted.add(case_id)

    reason = (state.get(f"rc_reason_{case_id}") or "").strip()

    try:
        api_client.reject_review_case(case_id, reason)
    except ApiError as exc:
        if exc.status_code == 409:
            state.rc_results[case_id] = (
                "warning", f"{label}: {exc.message}"
            )
        elif exc.status_code in (400, 404):
            state.rc_attempted.discard(case_id)  # nothing was applied
            state.rc_results[case_id] = ("error", f"{label}: {exc.message}")
        elif exc.status_code is None:
            state.rc_results[case_id] = (
                "warning",
                f"{label}: could not confirm whether the rejection was "
                f"recorded ({exc.message}). Click Reload data and check the "
                "case status before trying again.",
            )
        else:
            state.rc_results[case_id] = (
                "error",
                f"{label}: {exc.message} Click Reload data and check the "
                "case status before trying again.",
            )
        return

    state.rc_results[case_id] = ("success", f"{label}: review case rejected.")


def reload_data():
    state.rc_attempted = set()
    state.rc_confirm = None


def dismiss(case_id):
    state.rc_results.pop(case_id, None)


# ------------------------------------------------------------------- page
st.title("Review Cases")
st.caption(
    "Unknown-vendor and duplicate submissions are held here instead of being "
    "added to the invoice list. They require investigation, so they can only "
    "be rejected, never approved."
)

st.button("Reload data", on_click=reload_data, icon=":material/refresh:")

for case_id, (kind, text) in list(state.rc_results.items()):
    box = st.container()
    getattr(box, kind)(text)
    box.button("Dismiss", key=f"rc_dismiss_{case_id}",
               on_click=dismiss, args=(case_id,))

try:
    cases = api_client.get_review_cases("all")
except ApiError as exc:
    st.error(f"Unable to load review cases: {exc}")
    st.stop()

if not cases:
    st.info("There are no review cases.")
    st.stop()

df = pd.DataFrame(cases)
df["status"] = df["status"].astype(str).str.lower()
df["case_label"] = df["case_type"].map(CASE_TYPE_LABELS).fillna(df["case_type"])

m1, m2, m3, m4 = st.columns(4)
with m1.container(border=True):
    st.metric("Open", int((df["status"] == "open").sum()))
with m2.container(border=True):
    st.metric("Rejected", int((df["status"] == "rejected").sum()))
with m3.container(border=True):
    st.metric("Resolved", int((df["status"] == "resolved").sum()))
with m4.container(border=True):
    st.metric("Total", len(df))

f1, f2 = st.columns(2)
status_choice = f1.multiselect(
    "Status",
    options=["open", "rejected", "resolved"],
    default=["open"],
    format_func=lambda s: s.title(),
)
type_choice = f2.multiselect(
    "Case type", options=sorted(df["case_label"].unique())
)

mask = pd.Series(True, index=df.index)
if status_choice:
    mask &= df["status"].isin(status_choice)
if type_choice:
    mask &= df["case_label"].isin(type_choice)

view = df[mask].sort_values("id", ascending=False).reset_index(drop=True)

st.caption(f"Showing {len(view)} of {len(df)} cases. Click a row to see details.")

if view.empty:
    st.info("No review cases match the current filters.")
    st.stop()

table = pd.DataFrame({
    "Case ID": view["id"],
    "Type": view["case_label"],
    "Vendor": view["vendor_name"],
    "Invoice number": view["invoice_number"],
    "Risk": view["risk_level"].astype(str).str.title(),
    "Status": view["status"].str.title(),
    "Created": view["created_at"].apply(fmt_timestamp),
})

event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    key="rc_table",
)

selected_rows = event.selection.rows
if not selected_rows or selected_rows[0] >= len(view):
    st.info("Select a case row to view its details.")
    st.stop()

case = view.iloc[selected_rows[0]]
case_id = int(case["id"])
case_label = f"Case {case_id} ({case['invoice_number']})"

st.divider()
st.subheader(f"Case {case_id} · {case['invoice_number']} · {case['vendor_name']}")

d1, d2, d3 = st.columns(3)
d1.caption("Type")
d1.write(case["case_label"])
d2.caption("Status")
d2.write(get_status_label(case["status"]))
d3.caption("Created")
d3.write(fmt_timestamp(case["created_at"]))

if case["case_type"] == "duplicate":
    existing = case.get("existing_invoice_id")
    if pd.notna(existing):
        st.info(
            f"This submission duplicates invoice ID {int(existing)}. "
            "Find the original in Invoice Management."
        )

if case["status"] != "open":
    reviewed = fmt_timestamp(case.get("reviewed_at"))
    reason = case.get("reviewer_reason")
    st.write(f"**Reviewer reason:** {reason if isinstance(reason, str) and reason else 'None recorded'}")
    if reviewed:
        st.write(f"**Reviewed at:** {reviewed}")

tab_report, tab_invoice = st.tabs(["Validation report", "Submitted invoice data"])

with tab_report:
    render_validation_report(case["validation_report"])

with tab_invoice:
    invoice_data = parse_report(case["invoice_data"])
    if not invoice_data:
        st.info("No invoice data is available for this case.")
    else:
        fields = {
            "Vendor": invoice_data.get("vendor"),
            "Invoice number": invoice_data.get("invoice_number"),
            "Invoice date": invoice_data.get("invoice_date"),
            "Due date": invoice_data.get("due_date"),
            "Currency": invoice_data.get("currency"),
            "Total": invoice_data.get("total"),
            "PO number": invoice_data.get("po_number"),
        }
        st.dataframe(
            pd.DataFrame({
                "Field": list(fields.keys()),
                "Value": ["" if v is None else str(v) for v in fields.values()],
            }),
            hide_index=True,
            width="stretch",
        )

        items = invoice_data.get("line_items") or []
        if items:
            st.write("**Line items**")
            st.dataframe(pd.DataFrame(items), hide_index=True, width="stretch")

# ------------------------------------------------------------ rejection
if case["status"] == "open":
    st.divider()
    st.subheader("Reject this case")
    st.caption(
        "Approval is not available for unknown-vendor or duplicate cases. "
        "A reason is mandatory and is stored with the rejection."
    )

    locked = case_id in state.rc_attempted
    confirming = state.rc_confirm == case_id

    st.text_area(
        "Rejection reason (required)",
        key=f"rc_reason_{case_id}",
        max_chars=1000,
        height=80,
        disabled=locked or confirming,
    )

    if case_id in state.rc_field_errors:
        st.error(state.rc_field_errors[case_id])

    if confirming:
        st.warning(f"Confirm rejection of {case_label}? This cannot be undone.")
        b1, b2, _ = st.columns([1, 1, 4])
        b1.button(
            "Confirm rejection",
            key=f"rc_confirm_{case_id}",
            type="primary",
            on_click=execute,
            args=(case_id, case_label),
        )
        b2.button("Cancel", key=f"rc_cancel_{case_id}", on_click=cancel_confirm)
    else:
        st.button(
            "Reject case",
            key=f"rc_reject_{case_id}",
            type="primary",
            icon=":material/close:",
            disabled=locked,
            on_click=ask_confirm,
            args=(case_id,),
        )
        if locked:
            st.caption("A rejection request was sent. Click Reload data to refresh.")