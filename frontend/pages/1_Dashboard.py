import pandas as pd
import plotly.express as px
import streamlit as st
from api_client import ApiError, get_health, get_invoices
from components.status import STATUS_COLORS, get_status_label

st.title("Dashboard Overview")
st.caption("Overview of your invoice processing system")


@st.cache_data(ttl=30, show_spinner="Loading data from the API...")
def load_dashboard_data():
    return get_health(), get_invoices()


if st.button("Refresh Dashboard", icon=":material/refresh:"):
    st.cache_data.clear()
    st.rerun()

try:
    health, invoices = load_dashboard_data()
except ApiError as exc:
    st.error(f"Unable to load dashboard data: {exc}")
    st.stop()

records = health.get("records", {})
df = pd.DataFrame(invoices)

if df.empty:
    counts = {}
else:
    df["status"] = df["status"].astype(str).str.lower()
    df["status_label"] = df["status"].apply(get_status_label)
    counts = df["status"].value_counts().to_dict()


def card(column, title, value):
    with column.container(border=True):
        st.metric(title, value)


st.subheader("Invoice Overview")

row1 = st.columns(4)
card(row1[0], "Total Invoices", records.get("invoices", len(df)))
card(row1[1], "Approved", counts.get("approved", 0))
card(row1[2], "Pending Approvals", counts.get("pending_approval", 0))
card(row1[3], "Human Review", counts.get("human_review", 0))

row2 = st.columns(4)
card(row2[0], "Rejected", counts.get("rejected", 0))
card(row2[1], "Registered Vendors", records.get("vendors", 0))
card(row2[2], "Purchase Orders", records.get("purchase_orders", 0))
card(row2[3], "Review Cases (all)", records.get("invoice_review_cases", 0))

if df.empty:
    st.info("No invoices have been processed yet.")
    st.stop()

st.divider()
left, right = st.columns(2)

with left:
    st.subheader("Invoice Status Distribution")

    status_df = df["status_label"].value_counts().reset_index()
    status_df.columns = ["Status", "Count"]

    fig = px.pie(
        status_df,
        names="Status",
        values="Count",
        hole=0.45,
        color="Status",
        color_discrete_map=STATUS_COLORS,
    )
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Invoice Processing Trend")

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

        fig = px.bar(
            trend,
            x="Date",
            y="Invoices",
            color="Status",
            color_discrete_map=STATUS_COLORS,
        )
        fig.update_xaxes(type="category")
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("Grouped by the date each invoice was recorded.")

st.divider()
st.subheader("Recent Invoice Activity")

recent = df.sort_values("id", ascending=False).head(10)

st.dataframe(
    recent[[
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
    }),
    width="stretch",
    hide_index=True,
)