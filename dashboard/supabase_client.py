"""
Client Supabase partagé par tout le dashboard — remplace les appels HTTP vers
le backend FastAPI (supprimé) : le dashboard Streamlit accède désormais
directement à Supabase, comme le font déjà ceo_agent.py / relance_prospects.py
côté scripts racine.
"""

import os

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Supabase est une dépendance dure (aucune donnée n'est accessible sans
# elle) : pas de mode dégradé possible ici, contrairement à Ollama.
# app.py::secrets_loader.initialiser_secrets() a déjà vérifié plus tôt que
# ces deux variables sont au moins PRÉSENTES (sinon l'app se serait arrêtée
# avant même d'importer ce module) — si create_client() échoue quand même
# ici, c'est qu'une valeur est présente mais INVALIDE (clé expirée, projet
# différent, faute de frappe dans la valeur elle-même). st.error + st.stop()
# plutôt que de laisser l'exception remonter telle quelle : jamais de
# traceback brut, illisible sur un petit écran (mobile notamment).
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(
        "⚠️ Connexion à Supabase refusée — SUPABASE_URL/SUPABASE_KEY sont "
        "présentes mais semblent invalides (clé expirée, faute de frappe, "
        "ou mauvais projet)."
    )
    st.markdown(
        "- Sur **Streamlit Community Cloud** : App settings → Secrets "
        "(voir `dashboard/SECRETS.md`).\n"
        "- En **local** : vérifie ton fichier `.env` à la racine du dépôt."
    )
    with st.expander("Détails techniques"):
        st.caption(str(e))
    st.stop()
