import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import ApiError, get_health, get_invoices
from components.formatting import fmt_timestamp
from components.status import (
    STATUS_COLORS,
    get_status_label,
    label_tone,
    style_tone_columns,
)
from components.ui import (
    empty_state,
    esc,
    html_block,
    kpi_card,
    page_header,
    page_link_if_allowed,
    section_title,
)

actions = page_header(
    "Dashboard Overview",
    "Overview of your invoice processing system",
)

if actions.button("Refresh Dashboard", icon=":material/refresh:", width="stretch"):
    st.cache_data.clear()
    st.rerun()

# Each request is made separately so a failure names the endpoint that failed.
health, invoices, failures = None, None, []

try:
    health = get_health()
except ApiError as exc:
    failures.append(("GET /health", exc))

try:
    invoices = get_invoices()
except ApiError as exc:
    failures.append(("GET /invoices", exc))

if failures:
    for endpoint, exc in failures:
        st.error(f"Unable to load dashboard data: {exc}")
        status = f" (HTTP {exc.status_code})" if exc.status_code else ""
        st.caption(f"Failing request: {endpoint}{status}")
    st.stop()

records = health.get("records", {})
df = pd.DataFrame(invoices)

if df.empty:
    counts = {}
else:
    df["status"] = df["status"].astype(str).str.lower()
    df["status_label"] = df["status"].apply(get_status_label)
    counts = df["status"].value_counts().to_dict()

total = records.get("invoices", len(df))


def share(count):
    return f"{count / total:.0%} of all invoices" if total else ""


# ------------------------------------------------------------------- KPIs
cols = st.columns(5)
with cols[0]:
    kpi_card("Total invoices", total, "Recorded in the system")
with cols[1]:
    kpi_card(
        "Pending approval", counts.get("pending_approval", 0),
        share(counts.get("pending_approval", 0)), tone="warning",
    )
with cols[2]:
    kpi_card(
        "Human review", counts.get("human_review", 0),
        share(counts.get("human_review", 0)), tone="review",
    )
with cols[3]:
    kpi_card(
        "Approved", counts.get("approved", 0),
        share(counts.get("approved", 0)), tone="success",
    )
with cols[4]:
    kpi_card(
        "Rejected", counts.get("rejected", 0),
        share(counts.get("rejected", 0)), tone="danger",
    )

st.write("")

if df.empty:
    empty_state(
        "No invoices have been processed yet.",
        "Upload an invoice to see activity, status breakdowns and trends here.",
    )
    st.write("")
    page_link_if_allowed(
        "pages/2_Invoice_Processing.py", "Upload an invoice", ":material/upload_file:"
    )

else:
    # ------------------------------------------- distribution + quick actions
    left, right = st.columns([3, 2])

    with left.container(key="card_distribution"):
        section_title(
            "Invoice status distribution",
            f"Share of the {len(df)} invoices loaded",
        )

        status_counts = df["status_label"].value_counts()
        segments, rows = [], []

        for label, count in status_counts.items():
            pct = count / len(df) * 100
            color = STATUS_COLORS.get(label, "#98A2B3")
            segments.append(
                f'<span style="width:{pct:.2f}%;background:{color}" '
                f'title="{esc(label)}: {count}"></span>'
            )
            rows.append(
                f'<div class="ia-dist-row"><span class="ia-sw" style="background:{color}"></span>'
                f'<span class="ia-dist-name">{esc(label)}</span>'
                f'<span class="ia-dist-count">{count}</span>'
                f'<span class="ia-dist-pct">{pct:.0f}%</span></div>'
            )

        html_block(f'<div class="ia-dist-bar">{"".join(segments)}</div>')
        html_block("".join(rows))

    with right.container(key="card_quick_actions"):
        section_title("Quick actions", "Jump to the work that needs you")

        page_link_if_allowed(
            "pages/2_Invoice_Processing.py", "Upload an invoice", ":material/upload_file:"
        )
        pending = counts.get("pending_approval", 0)
        page_link_if_allowed(
            "pages/4_Approval_Center.py",
            f"Review approvals ({pending} pending)" if pending else "Review approvals",
            ":material/fact_check:",
        )
        page_link_if_allowed(
            "pages/5_Review_Cases.py", "Open review cases", ":material/policy:"
        )
        page_link_if_allowed(
            "pages/3_Invoice_Management.py", "Browse all invoices", ":material/table_view:"
        )
        page_link_if_allowed(
            "pages/6_Audit_Logs.py", "View audit logs", ":material/history:"
        )

    st.write("")

    # ----------------------------------------------------- trend + system
    left, right = st.columns([3, 2])

    with left.container(key="card_trend"):
        section_title(
            "Invoice processing trend",
            "Grouped by the date each invoice was recorded.",
        )

        created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
        trend_df = df.assign(Date=created.dt.strftime("%Y-%m-%d")).dropna(
            subset=["Date"]
        )

        if trend_df.empty:
            st.info("No valid invoice dates available.")
        else:
            trend = (
                trend_df.groupby(["Date", "status_label"])
                .size()
                .reset_index(name="Invoices")
                .rename(columns={"status_label": "Status"})
            )

            # Short day labels when everything falls in one year; order is by date.
            years = {value[:4] for value in trend["Date"]}
            ordered = sorted(trend["Date"].unique())
            if len(years) == 1:
                trend["Date"] = pd.to_datetime(trend["Date"]).dt.strftime("%d %b")
                ordered = [pd.Timestamp(v).strftime("%d %b") for v in ordered]

            fig = px.bar(
                trend,
                x="Date",
                y="Invoices",
                color="Status",
                color_discrete_map=STATUS_COLORS,
                category_orders={"Date": ordered},
            )
            fig.update_xaxes(type="category", title=None, showgrid=False, nticks=12, tickangle=0)
            peak = int(trend.groupby("Date")["Invoices"].sum().max())
            fig.update_yaxes(
                title=None, gridcolor="#E9EDF3", rangemode="tozero",
                dtick=1 if peak <= 8 else None, tickformat="d",
            )
            fig.update_layout(
                margin=dict(t=8, b=8, l=8, r=8),
                height=300,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="IBM Plex Sans, system-ui, sans-serif", color="#586478"),
                legend=dict(orientation="h", y=1.14, x=0, title=None),
                bargap=0.35,
            )
            st.plotly_chart(fig, width="stretch")

    with right.container(key="card_system"):
        section_title("System status", "Reported by the backend")

        healthy = health.get("status") == "healthy"
        tone = "success" if healthy else "warning"
        state = "Healthy" if healthy else "Degraded"
        db_state = str(health.get("database") or "unknown").title()

        details = [
            ("Backend", f'<span class="ia-pill ia-{tone}">{esc(state)}</span>'),
            ("Database", esc(db_state)),
        ]
        if records:
            details += [
                ("Vendors", esc(records.get("vendors", 0))),
                ("Purchase orders", esc(records.get("purchase_orders", 0))),
                ("Review cases", esc(records.get("invoice_review_cases", 0))),
            ]

        html_block(
            '<dl class="ia-kv">'
            + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in details)
            + "</dl>"
        )

    st.write("")

    # ------------------------------------------------------ recent activity
    recent = df.sort_values("id", ascending=False).head(10)

    with st.container(key="card_recent"):
        section_title("Recent invoice activity", "The 10 most recently recorded invoices")

        table = recent[[
            "id", "invoice_number", "vendor_name", "invoice_date",
            "currency", "total", "po_number", "status_label", "created_at",
        ]].rename(columns={
            "id": "ID",
            "invoice_number": "Invoice #",
            "vendor_name": "Vendor",
            "invoice_date": "Invoice date",
            "currency": "Cur.",
            "total": "Amount",
            "po_number": "PO #",
            "status_label": "Status",
            "created_at": "Recorded at",
        })

        table["Recorded at"] = table["Recorded at"].apply(fmt_timestamp)

        st.dataframe(
            style_tone_columns(table, {"Status": label_tone}),
            width="stretch",
            hide_index=True,
            column_config={"Amount": st.column_config.NumberColumn(format="%.2f")},
        )
