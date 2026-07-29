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

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
