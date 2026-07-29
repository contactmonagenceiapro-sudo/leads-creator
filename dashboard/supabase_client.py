"""
Client Supabase partagé par tout le dashboard — remplace les appels HTTP vers
le backend FastAPI (supprimé) : le dashboard Streamlit accède désormais
directement à Supabase, comme le font déjà ceo_agent.py / relance_prospects.py
côté scripts racine.
"""

import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Supabase est une dépendance dure (aucune donnée n'est accessible sans
# elle) : pas de mode dégradé possible ici, contrairement à Ollama. Mais un
# secret manquant/mal nommé côté Streamlit Cloud ne doit pas planter l'app
# avec une erreur interne cryptique ("Invalid API key") — le message ci-
# dessous pointe directement vers la cause probable et sa correction.
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    raise RuntimeError(
        "Impossible de se connecter à Supabase — SUPABASE_URL/SUPABASE_KEY "
        "sont probablement manquantes ou invalides. Si l'app tourne sur "
        "Streamlit Community Cloud, vérifie les secrets configurés dans "
        "App settings → Secrets (voir dashboard/SECRETS.md) ; en local, "
        "vérifie ton fichier .env. "
        f"Erreur d'origine : {e}"
    ) from e
