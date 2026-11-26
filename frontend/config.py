import os
import streamlit as st

try:
    API_BASE_URL = st.secrets["INVOICE_API_URL"]
except Exception:
    API_BASE_URL = os.getenv(
        "INVOICE_API_URL",
        "http://127.0.0.1:8000"
    )

API_BASE_URL = API_BASE_URL.rstrip("/")

REQUEST_TIMEOUT = (3, 15)
UPLOAD_TIMEOUT = (3, 30)
PAGE_SIZE = 200