"""
Journalisation des envois d'e-mail — insert Supabase direct de l'événement
'envoye' (table email_events), utilisé par outbound_pro_btp.py::statut_ramp_warmup
pour compter les envois du jour (montée en charge progressive du domaine).

Le pixel invisible + les liens redirigés (ouverture/clic) ont été retirés :
ils dépendaient des routes /track/open et /track/click du backend FastAPI,
supprimé au profit d'une app 100% Streamlit. Les e-mails repartent donc en
texte brut uniquement (voir ceo_agent.py::send_email_prospect,
outbound_chantiers/outbound_pro_btp.py::envoyer_email).
"""

import logging
import os
import uuid

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRACKING] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def demarrer_tracking(lead_type: str, lead_id: str, client_final: str | None = None) -> str:
    """Crée un identifiant unique pour cet envoi et journalise l'événement
    'envoye' (utilisé par statut_ramp_warmup pour le comptage quotidien).
    Ne bloque JAMAIS l'envoi : si Supabase est injoignable, un identifiant
    local est quand même renvoyé, l'événement reste simplement orphelin."""
    tracking_id = str(uuid.uuid4())
    if not SUPABASE_URL or not SUPABASE_KEY:
        return tracking_id
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/email_events",
            headers=_supabase_headers(),
            json={
                "tracking_id": tracking_id,
                "type_evenement": "envoye",
                "lead_type": lead_type,
                "lead_id": lead_id,
                "client_final": client_final,
            },
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        log.error(f"Échec journalisation envoi (tracking_id={tracking_id}) : {e}")
    return tracking_id
