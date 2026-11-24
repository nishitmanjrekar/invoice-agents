import streamlit as st

STATUS_CONFIG = {
    "approved": {"label": "Approved", "color": "green"},
    "pending_approval": {"label": "Pending Approval", "color": "orange"},
    "human_review": {"label": "Human Review", "color": "violet"},
    "rejected": {"label": "Rejected", "color": "red"},
    "processing": {"label": "Processing", "color": "blue"},
    "processing_failed": {"label": "Processing Failed", "color": "red"},
    "failed": {"label": "Failed", "color": "red"},
}

# Hex colors for charts, keyed by display label.
STATUS_COLORS = {
    "Approved": "#2e7d32",
    "Pending Approval": "#ed6c02",
    "Human Review": "#8e24aa",
    "Rejected": "#c62828",
    "Processing": "#1976d2",
    "Processing Failed": "#c62828",
    "Failed": "#c62828",
}


def get_status_label(status):
    return STATUS_CONFIG.get(
        str(status).lower(),
        {"label": str(status).replace("_", " ").title()}
    )["label"]


def render_status(status):
    config = STATUS_CONFIG.get(
        str(status).lower(),
        {"label": get_status_label(status), "color": "gray"}
    )

    st.badge(config["label"], color=config["color"])