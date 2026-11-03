"""Revenue Analytics — Q3 2026 review (Streamlit). Run from the repo root:

    streamlit run dashboard/app.py

Pages follow the storyline of the analysis. Every page reads the parquet cache written by
`make all` (`python run.py`); nothing here rebuilds the warehouse.
"""
import streamlit as st

st.set_page_config(page_title="Revenue Analytics · Q3 2026", page_icon=":material/insights:", layout="wide",
                   initial_sidebar_state="expanded")

pages = [
    st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/data_model.py", title="Data Model", icon=":material/schema:"),
    st.Page("views/mrr_waterfall.py", title="MRR Waterfall", icon=":material/waterfall_chart:"),
    st.Page("views/nrr_cohorts.py", title="NRR / GRR Cohorts", icon=":material/grid_on:"),
    st.Page("views/plan_transitions.py", title="Plan Transitions", icon=":material/swap_horiz:"),
    st.Page("views/expansion_signals.py", title="Expansion Signals", icon=":material/trending_up:"),
    st.Page("views/silent_churn.py", title="Silent Churn Watchlist", icon=":material/visibility:"),
    st.Page("views/recommendations.py", title="Recommendations", icon=":material/checklist:"),
]
st.navigation(pages, position="sidebar").run()
