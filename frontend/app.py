import streamlit as st

st.set_page_config(
    page_title="Invoice Agent Console",
    page_icon=":material/receipt_long:",
    layout="wide",
)

from components.layout import render_banner, render_sidebar  # noqa: E402

pages = [
    st.Page(
        "pages/1_Dashboard.py",
        title="Dashboard",
        icon=":material/dashboard:",
        default=True,
    ),
    st.Page(
        "pages/2_Invoice_Processing.py",
        title="Invoice Processing",
        icon=":material/upload_file:",
    ),
    st.Page(
        "pages/3_Invoice_Management.py",
        title="Invoice Management",
        icon=":material/table_view:",
    ),
    st.Page(
        "pages/4_Approval_Center.py",
        title="Approval Center",
        icon=":material/fact_check:",
    ),
    st.Page(
        "pages/5_Review_Cases.py",
        title="Review Cases",
        icon=":material/policy:",
    ),
    st.Page(
        "pages/6_Audit_Logs.py",
        title="Audit Logs",
        icon=":material/history:",
    ),
]

navigation = st.navigation(pages)
render_sidebar()
render_banner()
navigation.run()