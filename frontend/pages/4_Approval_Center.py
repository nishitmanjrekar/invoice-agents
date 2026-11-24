import api_client
import pandas as pd
import streamlit as st
from api_client import ApiError
from components.status import get_status_label
from components.validation_view import render_validation_report

state = st.session_state
state.setdefault("appr_results", {})        # thread_id -> (kind, text)
state.setdefault("appr_attempted", set())   # thread_ids with a request sent
state.setdefault("appr_confirm", None)      # (thread_id, action) awaiting confirm
state.setdefault("appr_field_errors", {})   # thread_id -> message


# -------------------------------------------------------------- callbacks
def ask_confirm(thread_id, action):
    reason = (state.get(f"reason_{thread_id}") or "").strip()
    if action == "reject" and not reason:
        state.appr_field_errors[thread_id] = "A rejection reason is required."
        state.appr_confirm = None
        return
    state.appr_field_errors.pop(thread_id, None)
    state.appr_confirm = (thread_id, action)


def cancel_confirm():
    state.appr_confirm = None


def execute(thread_id, action, label):
    """Runs once per click. The thread is locked before the request is sent."""
    state.appr_confirm = None

    if thread_id in state.appr_attempted:
        return
    state.appr_attempted.add(thread_id)

    reason = (state.get(f"reason_{thread_id}") or "").strip()

    try:
        result = api_client.submit_decision(thread_id, action, reason)
    except ApiError as exc:
        if exc.status_code == 409:
            state.appr_results[thread_id] = (
                "warning",
                f"{label}: {exc.message} It is no longer waiting for a decision.",
            )
        elif exc.status_code in (400, 404):
            state.appr_attempted.discard(thread_id)  # nothing was applied
            state.appr_results[thread_id] = ("error", f"{label}: {exc.message}")
        elif exc.status_code is None:
            state.appr_results[thread_id] = (
                "warning",
                f"{label}: could not confirm whether the decision was recorded "
                f"({exc.message}). Click Reload data and check the status "
                "before trying again.",
            )
        else:
            state.appr_results[thread_id] = (
                "error",
                f"{label}: {exc.message} Click Reload data and check the "
                "status before trying again.",
            )
        return

    state.appr_results[thread_id] = (
        "success",
        f"{label}: {result.get('decision', action)} recorded. "
        f"Status is now {get_status_label(result.get('status'))}.",
    )


def reload_data():
    state.appr_attempted = set()
    state.appr_confirm = None


def dismiss(thread_id):
    state.appr_results.pop(thread_id, None)


# ------------------------------------------------------------------- page
st.title("Human Approval Center")
st.caption(
    "Approve or reject invoices that are waiting for a human decision. "
    "Decisions resume the saved workflow. No payment is made."
)

st.button("Reload data", on_click=reload_data, icon=":material/refresh:")

for thread_id, (kind, text) in list(state.appr_results.items()):
    box = st.container()
    getattr(box, kind)(text)
    box.button("Dismiss", key=f"dismiss_{thread_id}",
               on_click=dismiss, args=(thread_id,))

try:
    invoices = api_client.get_invoices()
    workflows = api_client.list_workflows()
except ApiError as exc:
    st.error(f"Unable to load data: {exc}")
    st.stop()

# Newest workflow wins if an invoice somehow has more than one thread.
workflow_by_invoice = {}
for wf in workflows:
    invoice_id = wf.get("invoice_id")
    if invoice_id is not None and invoice_id not in workflow_by_invoice:
        workflow_by_invoice[invoice_id] = wf

pending = [i for i in invoices if str(i.get("status")).lower() == "pending_approval"]
human_review = [i for i in invoices if str(i.get("status")).lower() == "human_review"]

actionable = [
    i for i in pending
    if workflow_by_invoice.get(i["id"], {}).get("approval_required")
]
actionable_ids = {i["id"] for i in actionable}
stuck = [i for i in pending if i["id"] not in actionable_ids]

m1, m2, m3 = st.columns(3)
with m1.container(border=True):
    st.metric("Awaiting decision", len(actionable))
with m2.container(border=True):
    st.metric("Pending, not actionable", len(stuck))
with m3.container(border=True):
    st.metric("Human review (read-only)", len(human_review))

# ------------------------------------------------------- awaiting decision
st.subheader("Awaiting decision")

if not actionable:
    st.info("No invoices are waiting for a human decision.")

for inv in sorted(actionable, key=lambda i: i["id"]):
    wf = workflow_by_invoice[inv["id"]]
    thread_id = wf["thread_id"]
    locked = thread_id in state.appr_attempted
    confirming = state.appr_confirm and state.appr_confirm[0] == thread_id
    amount = f"{inv.get('currency', '')} {float(inv.get('total', 0)):,.2f}".strip()

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 3])
        c1.caption("Vendor")
        c1.write(f"**{inv.get('vendor_name')}**")
        c2.caption("Invoice number")
        c2.write(inv.get("invoice_number"))
        c3.caption("Total amount")
        c3.write(amount)
        c4.caption("Purchase order")
        c4.write(inv.get("po_number") or "None")

        st.caption(
            f"Workflow: {get_status_label(wf.get('status'))} · "
            f"paused at human approval · ID {thread_id}"
        )

        with st.expander("Validation information"):
            try:
                detail = api_client.get_workflow(thread_id)
            except ApiError as exc:
                st.error(f"Could not load validation report: {exc}")
            else:
                if detail.get("route_reason"):
                    st.write(f"**Routing:** {detail['route_reason']}")
                render_validation_report(detail.get("validation_report"))

        reason = st.text_area(
            "Reason (required to reject, optional to approve)",
            key=f"reason_{thread_id}",
            max_chars=1000,
            height=80,
            disabled=locked or bool(confirming),
        )

        if thread_id in state.appr_field_errors:
            st.error(state.appr_field_errors[thread_id])

        label = f"{inv.get('invoice_number')}"

        if confirming:
            action = state.appr_confirm[1]
            st.warning(
                f"Confirm **{action}** for {label} ({amount})? "
                "This resumes the workflow and updates the database."
            )
            b1, b2, _ = st.columns([1, 1, 4])
            b1.button(
                f"Confirm {action}",
                key=f"confirm_{thread_id}",
                type="primary",
                on_click=execute,
                args=(thread_id, action, label),
            )
            b2.button("Cancel", key=f"cancel_{thread_id}", on_click=cancel_confirm)
        else:
            b1, b2, _ = st.columns([1, 1, 4])
            b1.button(
                "Approve",
                key=f"approve_{thread_id}",
                type="primary",
                icon=":material/check:",
                disabled=locked,
                on_click=ask_confirm,
                args=(thread_id, "approve"),
            )
            b2.button(
                "Reject",
                key=f"reject_{thread_id}",
                icon=":material/close:",
                disabled=locked,
                on_click=ask_confirm,
                args=(thread_id, "reject"),
            )
            if locked:
                st.caption("A decision request was sent. Click Reload data to refresh.")

# ----------------------------------------------------------- not actionable
def invoice_table(rows):
    df = pd.DataFrame(rows)[[
        "id", "invoice_number", "vendor_name", "currency",
        "total", "po_number", "status",
    ]]
    df["status"] = df["status"].apply(get_status_label)
    return df.rename(columns={
        "id": "Invoice ID", "invoice_number": "Invoice number",
        "vendor_name": "Vendor", "currency": "Cur.", "total": "Amount",
        "po_number": "PO number", "status": "Status",
    })


if stuck:
    st.subheader("Pending, but not actionable here")
    st.caption(
        "These invoices have status Pending Approval, but no paused workflow "
        "was found for them (no checkpoint, or the workflow already finished). "
        "Thread IDs are never guessed, so no buttons are shown."
    )
    st.dataframe(invoice_table(stuck), hide_index=True, width="stretch")

if human_review:
    st.subheader("Human review (read-only)")
    st.caption(
        "The workflow does not pause for these invoices, and the backend has "
        "no approve action for them, so they cannot be approved or rejected here."
    )
    st.dataframe(invoice_table(human_review), hide_index=True, width="stretch")