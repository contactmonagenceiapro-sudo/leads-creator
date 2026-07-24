"""
Dashboard client pour ai-company.

Lancement :
    streamlit run app.py

Configuration :
    Variable d'environnement AI_COMPANY_API_URL (défaut http://localhost:8000)
"""

import streamlit as st
import pandas as pd

from api_client import (
    ApiError,
    API_BASE_URL,
    get_stats,
    get_leads,
    get_contents,
    trigger_agent_action,
)

st.set_page_config(
    page_title="ai-company · Dashboard",
    page_icon="📊",
    layout="wide",
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def safe_call(fn, *args, **kwargs):
    """Exécute un appel API et affiche une erreur propre en cas de souci,
    sans faire planter la page."""
    try:
        return fn(*args, **kwargs), None
    except ApiError as e:
        return None, str(e)


def to_dataframe(data) -> pd.DataFrame:
    if not data:
        return pd.DataFrame()
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ Connexion")
    st.caption(f"API : `{API_BASE_URL}`")
    if st.button("🔄 Rafraîchir les données", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption("ai-company · dashboard client")


st.title("📊 ai-company — Dashboard")


# ---------------------------------------------------------------------
# 1. Statistiques globales
# ---------------------------------------------------------------------

st.subheader("Vue d'ensemble")

stats, stats_error = safe_call(get_stats)

if stats_error:
    st.error(stats_error)
elif stats:
    col1, col2, col3 = st.columns(3)
    col1.metric("Leads générés", stats.get("leads_count", "—"))
    col2.metric("Articles générés", stats.get("articles_count", "—"))

    last_report = stats.get("last_ceo_report")
    with col3:
        st.markdown("**Dernier rapport du CEO**")
        if isinstance(last_report, dict):
            st.caption(last_report.get("date", ""))
            st.write(last_report.get("summary", "—"))
        elif last_report:
            st.write(last_report)
        else:
            st.write("Aucun rapport disponible")
else:
    st.info("Aucune donnée de statistiques disponible.")

st.divider()


# ---------------------------------------------------------------------
# 2. Leads et contenus
# ---------------------------------------------------------------------

tab_leads, tab_contents = st.tabs(["🧑‍💼 Leads", "📝 Contenus"])

with tab_leads:
    leads, leads_error = safe_call(get_leads)
    if leads_error:
        st.error(leads_error)
    else:
        df_leads = to_dataframe(leads)
        if df_leads.empty:
            st.info("Aucun lead pour le moment.")
        else:
            st.dataframe(df_leads, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_leads)} lead(s)")

with tab_contents:
    contents, contents_error = safe_call(get_contents)
    if contents_error:
        st.error(contents_error)
    else:
        df_contents = to_dataframe(contents)
        if df_contents.empty:
            st.info("Aucun contenu pour le moment.")
        else:
            st.dataframe(df_contents, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_contents)} contenu(s)")

st.divider()


# ---------------------------------------------------------------------
# 3. Actions de l'agent
# ---------------------------------------------------------------------

st.subheader("Actions de l'agent")

col_a, col_b = st.columns([2, 1])
with col_a:
    action = st.selectbox(
        "Action à déclencher",
        options=["run_pipeline", "generate_report", "refresh_leads"],
        format_func=lambda x: {
            "run_pipeline": "Lancer le pipeline complet",
            "generate_report": "Générer un nouveau rapport",
            "refresh_leads": "Rafraîchir les leads",
        }.get(x, x),
    )
with col_b:
    st.write("")  # espacement pour aligner le bouton
    st.write("")
    if st.button("🚀 Déclencher", type="primary", use_container_width=True):
        with st.spinner("Action en cours..."):
            result, action_error = safe_call(trigger_agent_action, action)
        if action_error:
            st.error(action_error)
        else:
            st.success("Action déclenchée avec succès.")
            st.json(result)
