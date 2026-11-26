"""Global look and feel for the Invoice Agent Console.

`inject_theme()` is called once per script run from app.py. It only emits CSS:
no data is read, nothing is cached and no session state is touched.
"""
from urllib.parse import quote

import streamlit as st

from components.status import TONE_COLORS

FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=IBM+Plex+Sans:wght@400;500;600&display=swap');"
)

_LOGO_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='8' fill='#2F6DB0'/>"
    "<path d='M10 8h12v16l-2.4-1.8L17.2 24l-2.4-1.8L12.4 24 10 22.2z' fill='#fff'/>"
    "<path d='M13 12.5h6M13 15.5h6M13 18.5h3.5' stroke='#2F6DB0' "
    "stroke-width='1.6' stroke-linecap='round'/></svg>"
)
LOGO_URL = "data:image/svg+xml;utf8," + quote(_LOGO_SVG)


def _tokens():
    return """
:root {
  --ia-navy: #0E1A2B;
  --ia-navy-2: #16253B;
  --ia-navy-3: #1F3452;
  --ia-side-text: #B7C3D6;
  --ia-side-muted: #7F8DA3;
  --ia-bg: #F2F4F8;
  --ia-card: #FFFFFF;
  --ia-line: #E1E6EE;
  --ia-line-strong: #C9D1DD;
  --ia-text: #172033;
  --ia-muted: #586478;
  --ia-faint: #8792A5;
  --ia-brand: #1F4E79;
  --ia-brand-hover: #173D60;
  --ia-brand-soft: #E8EFF7;
  --ia-radius-card: 12px;
  --ia-radius-ctl: 8px;
  --ia-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 1px 3px rgba(16, 24, 40, .06);
  --ia-font: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
}
"""


def _tone_css():
    rules = []
    for tone, (fg, bg) in TONE_COLORS.items():
        rules.append(
            f".ia-pill.ia-{tone} {{ color: {fg}; background: {bg}; }}"
            f".ia-dot.ia-{tone} {{ background: {fg}; }}"
            f".ia-step.ia-{tone} .ia-step-mark {{ background: {fg}; border-color: {fg}; color: #fff; }}"
        )
    return "\n".join(rules)


_BASE_CSS = """
html, body, .stApp, [data-testid="stAppViewContainer"] {
  font-family: var(--ia-font);
  color: var(--ia-text);
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background: var(--ia-bg);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"], [data-testid="stDecoration"] { display: none; }
[data-testid="stMainBlockContainer"] {
  max-width: 1280px;
  padding: 1.6rem 2.5rem 4rem;
}
h1, h2, h3, h4 { font-family: var(--ia-font); letter-spacing: -0.01em; color: var(--ia-text); }
p, label, li, input, textarea, button, select { font-family: var(--ia-font); }
:focus-visible { outline: 2px solid var(--ia-brand); outline-offset: 2px; }

/* ------------------------------------------------------------ page header */
.ia-page-head { margin-bottom: .9rem; }
.ia-page-head h1 {
  font-size: 1.6rem; font-weight: 600; line-height: 1.25;
  margin: 0; padding: 0;
}
.ia-page-head p { margin: .3rem 0 0; color: var(--ia-muted); font-size: .95rem; max-width: 70ch; }
.ia-section-title { font-size: 1.02rem; font-weight: 600; margin: 0; color: var(--ia-text); }
.ia-section-sub { font-size: .86rem; color: var(--ia-muted); margin: .15rem 0 .2rem; }
.ia-muted { color: var(--ia-muted); }

/* ------------------------------------------------------------------ cards */
[class*="st-key-card"] {
  background: var(--ia-card);
  border: 1px solid var(--ia-line);
  border-radius: var(--ia-radius-card);
  padding: 1.15rem 1.3rem 1.25rem;
  box-shadow: var(--ia-shadow);
}
[data-testid="stHorizontalBlock"]:has([class*="st-key-card"]) { align-items: stretch; }
[data-testid="stColumn"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [class*="st-key-card"]) {
  display: flex; flex-direction: column;
}
[data-testid="stColumn"] > [data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] > [class*="st-key-card"]) { flex: 1; }
[data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-card"]) { flex: 1; }
[data-testid="stLayoutWrapper"] > [class*="st-key-card"] { height: 100%; box-sizing: border-box; }
.ia-kpi {
  background: var(--ia-card); border: 1px solid var(--ia-line);
  border-radius: var(--ia-radius-card); padding: 1rem 1.15rem 1.05rem;
  box-shadow: var(--ia-shadow); height: 100%;
}
.ia-kpi-label { display: flex; align-items: center; gap: .45rem; font-size: .86rem; color: var(--ia-muted); font-weight: 500; }
.ia-kpi-value { font-size: 1.95rem; font-weight: 600; line-height: 1.2; margin-top: .35rem; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.ia-kpi-value.ia-kpi-sm { font-size: 1.3rem; }
.ia-kpi-hint { font-size: .8rem; line-height: 1.5; color: var(--ia-faint); margin-top: .15rem; min-height: 1.5em; }
.ia-dot { display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; flex: none; }
.ia-dot.ia-neutral { background: var(--ia-faint); }

/* ------------------------------------------------------------------ pills */
.ia-pill {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .16rem .6rem; border-radius: 999px;
  font-size: .78rem; font-weight: 600; line-height: 1.4; white-space: nowrap;
}

/* ------------------------------------------------------------ field grids */
.ia-fields { margin: .9rem 0 .5rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .9rem 1.5rem; }
.ia-field-label { font-size: .78rem; color: var(--ia-muted); margin-bottom: .12rem; }
.ia-field-value { font-size: .95rem; font-weight: 500; overflow-wrap: anywhere; }
.ia-field-value.ia-num { font-variant-numeric: tabular-nums; }
.ia-item-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.ia-item-title { font-size: 1.08rem; font-weight: 600; }
.ia-item-amount { font-size: 1.35rem; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.ia-item-meta { color: var(--ia-muted); font-size: .82rem; overflow-wrap: anywhere; }

/* ------------------------------------------------- status distribution bar */
.ia-dist-bar { display: flex; height: 14px; border-radius: 999px; overflow: hidden; background: var(--ia-line); margin: .6rem 0 1rem; }
.ia-dist-bar span { display: block; height: 100%; }
.ia-dist-bar span + span { border-left: 2px solid #fff; }
.ia-dist-row { display: flex; align-items: center; gap: .6rem; padding: .42rem 0; border-top: 1px solid var(--ia-line); font-size: .92rem; }
.ia-dist-row:first-of-type { border-top: 0; }
.ia-dist-name { flex: 1; }
.ia-dist-count { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 2.5rem; text-align: right; }
.ia-dist-pct { color: var(--ia-muted); min-width: 3.2rem; text-align: right; font-variant-numeric: tabular-nums; }
.ia-sw { width: .7rem; height: .7rem; border-radius: 3px; flex: none; }

/* --------------------------------------------------------------- stepper */
.ia-steps { display: flex; gap: .5rem; align-items: flex-start; margin: .25rem 0 .5rem; }
.ia-step { flex: 1; display: flex; flex-direction: column; gap: .4rem; }
.ia-step-bar { height: 4px; border-radius: 4px; background: var(--ia-line); }
.ia-step.ia-done .ia-step-bar, .ia-step.ia-active .ia-step-bar { background: var(--ia-brand); }
.ia-step.ia-active .ia-step-name { color: var(--ia-brand); }
.ia-step.ia-success .ia-step-bar { background: #2E9E6B; }
.ia-step.ia-warning .ia-step-bar { background: #E0A02B; }
.ia-step.ia-danger .ia-step-bar { background: #D0453B; }
.ia-step-name { font-size: .86rem; font-weight: 600; }
.ia-step.ia-pending .ia-step-name { color: var(--ia-faint); font-weight: 500; }
.ia-step-note { font-size: .78rem; color: var(--ia-muted); margin-top: -.25rem; }

/* ----------------------------------------------------------- empty states */
.ia-empty { text-align: center; padding: 2.2rem 1rem; border: 1px dashed var(--ia-line-strong); border-radius: var(--ia-radius-card); background: #FAFBFD; }
.ia-empty-title { font-weight: 600; font-size: 1.02rem; }
.ia-empty-body { color: var(--ia-muted); font-size: .92rem; margin-top: .25rem; }
.ia-note { font-size: .84rem; color: var(--ia-muted); }
.ia-list { margin: .35rem 0 0; padding-left: 1.1rem; color: var(--ia-muted); font-size: .9rem; }
.ia-list li { margin: .25rem 0; }
.ia-kv { display: grid; grid-template-columns: 8.5rem 1fr; gap: .45rem 1rem; font-size: .9rem; margin-top: .4rem; }
.ia-kv dt { color: var(--ia-muted); }
.ia-kv dd { margin: 0; font-weight: 500; }

/* ---------------------------------------------------------------- sidebar */
[data-testid="stSidebar"] { background: var(--ia-navy); border-right: 0; }
[data-testid="stSidebar"] [data-testid="stSidebarHeader"] { padding: .9rem 1rem 0; background: transparent; }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { background: var(--ia-navy); }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding: 0 1rem 1.5rem; }
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] label, [data-testid="stSidebar"] small { color: var(--ia-side-text); }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.09); margin: .9rem 0; }
.ia-brand { display: flex; align-items: center; gap: .7rem; padding: .35rem .25rem 1.1rem; }
.ia-logo { width: 36px; height: 36px; border-radius: 9px; flex: none; background: center / contain no-repeat url("__LOGO__"); }
.ia-brand-name { color: #fff; font-weight: 600; font-size: 1.02rem; line-height: 1.2; }
.ia-brand-sub { color: var(--ia-side-muted); font-size: .78rem; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] {
  border-radius: var(--ia-radius-ctl); padding: .5rem .75rem; margin-bottom: 2px; min-height: 0;
  text-decoration: none;
}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] * { color: var(--ia-side-text); font-weight: 500; font-size: .93rem; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover { background: var(--ia-navy-2); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover * { color: #fff; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"] { background: var(--ia-navy-3); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"] * { color: #fff; }
.ia-side-card { background: var(--ia-navy-2); border: 1px solid rgba(255,255,255,.07); border-radius: 10px; padding: .75rem .85rem; }
.ia-side-row { display: flex; align-items: center; gap: .5rem; color: #fff; font-size: .88rem; font-weight: 500; }
.ia-side-row .ia-dot.ok { background: #3DC48A; }
.ia-side-row .ia-dot.warn { background: #F0B34A; }
.ia-side-row .ia-dot.bad { background: #F0716A; }
.ia-side-sub { color: var(--ia-side-muted); font-size: .78rem; margin-top: .25rem; overflow-wrap: anywhere; }
.ia-user { display: flex; align-items: center; gap: .65rem; margin-bottom: .7rem; }
.ia-avatar { width: 34px; height: 34px; border-radius: 50%; background: var(--ia-navy-3); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: .9rem; flex: none; }
.ia-user-name { color: #fff; font-weight: 600; font-size: .9rem; line-height: 1.2; overflow-wrap: anywhere; }
.ia-user-role { color: var(--ia-side-muted); font-size: .78rem; }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
  background: transparent; border: 1px solid rgba(255,255,255,.2); color: #fff;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] * { color: #fff; }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover { background: var(--ia-navy-2); border-color: rgba(255,255,255,.35); }

/* --------------------------------------------------------------- controls */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--ia-brand); border: 1px solid var(--ia-brand); color: #fff;
  border-radius: var(--ia-radius-ctl); font-weight: 500;
}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background: var(--ia-brand-hover); border-color: var(--ia-brand-hover); color: #fff;
}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"], [data-testid="stBaseButton-tertiary"] {
  background: #fff; border: 1px solid var(--ia-line-strong); color: var(--ia-text);
  border-radius: var(--ia-radius-ctl); font-weight: 500;
}
[data-testid="stBaseButton-secondary"]:hover { border-color: var(--ia-brand); color: var(--ia-brand); background: #fff; }
[data-testid="stBaseButton-primary"]:disabled, [data-testid="stBaseButton-secondary"]:disabled { opacity: .5; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  border-radius: var(--ia-radius-ctl); background-color: #fff;
}
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div { border-color: var(--ia-line-strong); }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="input"] input,
[data-baseweb="textarea"], [data-baseweb="textarea"] textarea,
[data-testid="stTextArea"] textarea, [data-testid="stTextArea"] [data-baseweb] { background-color: #fff !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {
  border-color: var(--ia-brand); box-shadow: 0 0 0 3px rgba(31, 78, 121, .16);
}
[data-baseweb="tag"] { background-color: var(--ia-brand-soft) !important; border-radius: 6px; }
[data-baseweb="tag"], [data-baseweb="tag"] span, [data-baseweb="tag"] svg { color: var(--ia-brand) !important; fill: var(--ia-brand) !important; }
[data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"], [data-baseweb="checkbox"] > span:first-child { border-color: var(--ia-brand); }
[data-testid="stWidgetLabel"] p { font-size: .86rem; font-weight: 500; color: var(--ia-text); }
[data-testid="stMain"] [data-testid="stPageLink-NavLink"] {
  background: #fff; border: 1px solid var(--ia-line); border-radius: var(--ia-radius-ctl); padding: .55rem .8rem; text-decoration: none;
}
[data-testid="stMain"] [data-testid="stPageLink-NavLink"]:hover { border-color: var(--ia-brand); background: var(--ia-brand-soft); }
[data-testid="stMain"] [data-testid="stPageLink-NavLink"] p { font-weight: 500; font-size: .92rem; }

/* ------------------------------------------------------------------- misc */
[data-testid="stFileUploaderDropzone"] {
  border: 1.5px dashed var(--ia-line-strong); background: #F8FAFC; border-radius: var(--ia-radius-card); padding: 1.8rem 1rem;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--ia-brand); background: var(--ia-brand-soft); }
/* Streamlit's built-in "200MB per file" text is replaced; the real limit is shown in the page. */
[data-testid="stFileUploaderDropzoneInstructions"] span { font-size: 0; }
[data-testid="stFileUploaderDropzoneInstructions"] span::after {
  content: "Drag and drop a .txt invoice here"; font-size: .92rem; color: var(--ia-muted);
}
[data-testid="stDataFrame"] { border: 1px solid var(--ia-line); border-radius: 10px; overflow: hidden; }
[data-testid="stAlert"] { border-radius: 10px; }
[data-testid="stExpander"] details { border: 1px solid var(--ia-line); border-radius: 10px; background: #fff; }
[data-testid="stExpander"] summary p { font-weight: 600; font-size: .92rem; }
[data-baseweb="tab-list"] { gap: 1.1rem; border-bottom: 1px solid var(--ia-line); }
[data-baseweb="tab"] { padding: .55rem 0; font-weight: 500; }
[data-baseweb="tab-highlight"] { background-color: var(--ia-brand); }
[data-testid="stForm"] { border: 0; padding: 0; }
[data-testid="stCode"] pre { border-radius: 8px; }
hr { border-color: var(--ia-line); }
[data-testid="stMetric"] { background: transparent; }

/* ------------------------------------------------------------------ login */
.st-key-login_shell { max-width: 940px; margin: 3vh auto 0; border-radius: 16px; overflow: hidden;
  background: #fff; border: 1px solid var(--ia-line); box-shadow: 0 8px 30px rgba(16,24,40,.10); gap: 0; }
.st-key-login_shell [data-testid="stHorizontalBlock"] { gap: 0; align-items: stretch; }
.st-key-login_left { background: var(--ia-navy); color: #fff; padding: 2.6rem 2.4rem; height: 100%; min-height: 470px; }
.st-key-login_right { padding: 2.6rem 2.6rem 2.2rem; }
.ia-login-name { font-size: 1.5rem; font-weight: 600; color: #fff; margin: 1.6rem 0 .4rem; letter-spacing: -0.01em; line-height: 1.25; }
.ia-login-lead { color: var(--ia-side-text); font-size: .95rem; line-height: 1.55; margin: 0 0 1.6rem; }
.ia-login-points { list-style: none; margin: 0; padding: 0; display: grid; gap: .8rem; }
.ia-login-points li { color: var(--ia-side-text); font-size: .9rem; line-height: 1.45; padding-left: 1.4rem; position: relative; }
.ia-login-points li::before { content: ""; position: absolute; left: 0; top: .5em; width: .5rem; height: .5rem; border-radius: 50%; background: #3DC48A; }
.ia-login-title { font-size: 1.45rem; font-weight: 600; margin: 0 0 .3rem; }
.ia-login-sub { color: var(--ia-muted); font-size: .92rem; margin: 0 0 1.4rem; }

/* -------------------------------------------------------------- responsive */
@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 3rem; }
  .ia-kpi-value { font-size: 1.6rem; }
  .st-key-login_left { min-height: 0; padding: 1.8rem 1.5rem; }
  .st-key-login_right { padding: 1.8rem 1.5rem; }
  .st-key-login_shell { margin-top: 0; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""


def build_css():
    return (
        FONT_IMPORT
        + _tokens()
        + _BASE_CSS.replace("__LOGO__", LOGO_URL.replace('"', "%22"))
        + _tone_css()
    )


def inject_theme():
    st.markdown(f"<style>{build_css()}</style>", unsafe_allow_html=True)
