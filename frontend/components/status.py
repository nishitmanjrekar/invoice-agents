import html

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

# Foreground / background pairs shared by badges, table cells and charts.
TONE_COLORS = {
    "success": ("#14704A", "#E2F3EA"),
    "warning": ("#8F5400", "#FFF1D6"),
    "review": ("#5936B0", "#EDE8FB"),
    "danger": ("#B02A22", "#FCE8E6"),
    "info": ("#1B5C9E", "#E3EEFA"),
    "neutral": ("#4F5B6D", "#ECEFF4"),
}

STATUS_TONE = {
    "approved": "success",
    "pending_approval": "warning",
    "human_review": "review",
    "rejected": "danger",
    "processing": "info",
    "processing_failed": "danger",
    "failed": "danger",
}

RISK_TONE = {"low": "success", "medium": "warning", "high": "danger"}

# Hex colors for charts, keyed by display label.
STATUS_COLORS = {
    "Approved": "#2E9E6B",
    "Pending Approval": "#E0A02B",
    "Human Review": "#7B5CD6",
    "Rejected": "#D0453B",
    "Processing": "#3C82C8",
    "Processing Failed": "#9C2B24",
    "Failed": "#9C2B24",
}


def get_status_label(status):
    return STATUS_CONFIG.get(
        str(status).lower(),
        {"label": str(status).replace("_", " ").title()}
    )["label"]


def status_tone(status):
    return STATUS_TONE.get(str(status).lower(), "neutral")


# Display label -> tone, for table columns that already hold labels.
LABEL_TONE = {
    config["label"]: STATUS_TONE[key] for key, config in STATUS_CONFIG.items()
}


def label_tone(label):
    return LABEL_TONE.get(str(label), "neutral")


def risk_tone(risk):
    return RISK_TONE.get(str(risk).lower(), "neutral")


def status_pill_html(status):
    """HTML badge for a status value (text is escaped)."""
    label = html.escape(get_status_label(status))
    return f'<span class="ia-pill ia-{status_tone(status)}">{label}</span>'


def render_status(status):
    st.markdown(status_pill_html(status), unsafe_allow_html=True)


def tone_cell_style(tone):
    fg, bg = TONE_COLORS.get(tone, TONE_COLORS["neutral"])
    return f"color: {fg}; background-color: {bg}; font-weight: 600;"


def style_tone_columns(df, columns):
    """
    Return a pandas Styler that colours cells like badges.
    `columns` maps a column name to a function: cell value -> tone name.
    Display-only; the underlying DataFrame is untouched.
    """
    styler = df.style
    for column, tone_of in columns.items():
        if column in df.columns:
            styler = styler.map(
                lambda value, fn=tone_of: (
                    "" if value in ("", None) else tone_cell_style(fn(value))
                ),
                subset=[column],
            )
    return styler
