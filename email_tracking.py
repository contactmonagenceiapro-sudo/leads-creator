"""
Suivi des interactions e-mail (ouvertures/clics) — pixel invisible + liens
redirigés via l'API existante (endpoints publics /track/open, /track/click
dans api/main.py). Reste sur l'infrastructure d'envoi actuelle (SMTP Zoho) :
ce module se contente d'habiller en HTML le texte déjà généré par
ceo_agent.py / outbound_chantiers/outbound_pro_btp.py, sans rien changer à
la façon dont l'e-mail part.

ATTENTION DÉPLOIEMENT : PUBLIC_APP_URL (même variable que mail_processor.py,
déjà utilisée pour les liens de présentation/intake) doit pointer vers un
domaine réellement accessible depuis Internet — jamais un alias docker
interne ni 127.0.0.1 — sinon le pixel et les liens seront cassés pour le
destinataire.

La fiabilité du pixel d'ouverture a une limite connue et non contournable :
certains clients mail (ex: proxy images de Gmail) mettent l'image en cache
côté serveur, ce qui peut faire manquer des ouvertures ultérieures. C'est la
pratique standard du secteur, pas un bug de cette implémentation.
"""

import html
import logging
import os
import re
import uuid
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRACKING] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", os.getenv("API_URL", "http://127.0.0.1:8000"))

MOTIF_URL = re.compile(r"https?://[^\s<>\)\]]+")


def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def demarrer_tracking(lead_type: str, lead_id: str, client_final: str | None = None) -> str:
    """Crée un identifiant de suivi unique pour CET envoi précis et
    journalise l'événement 'envoye' (contexte réutilisé plus tard par
    api/main.py::_evenement_origine pour rattacher ouvertures/clics au bon
    lead). Ne bloque JAMAIS l'envoi : si Supabase est injoignable, un
    identifiant local est quand même renvoyé — le pixel/les liens
    fonctionneront techniquement, seuls les événements resteront orphelins
    (silencieusement ignorés côté API, jamais une erreur pour autant)."""
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


def habiller_html_avec_tracking(corps_texte: str, tracking_id: str) -> str:
    """Convertit un corps d'e-mail texte brut en HTML visuellement
    équivalent : les URLs présentes dans le texte deviennent des liens
    cliquables redirigés via /track/click, et un pixel invisible 1x1 est
    ajouté en fin de corps pour détecter l'ouverture."""
    morceaux: list[str] = []
    dernier_index = 0
    for match in MOTIF_URL.finditer(corps_texte):
        morceaux.append(html.escape(corps_texte[dernier_index:match.start()]))
        url_originale = match.group(0)
        url_suivie = f"{PUBLIC_APP_URL}/track/click/{tracking_id}?url={quote(url_originale, safe='')}"
        morceaux.append(f'<a href="{html.escape(url_suivie)}">{html.escape(url_originale)}</a>')
        dernier_index = match.end()
    morceaux.append(html.escape(corps_texte[dernier_index:]))

    corps_html = "".join(morceaux).replace("\n", "<br>\n")
    pixel = (
        f'<img src="{PUBLIC_APP_URL}/track/open/{tracking_id}.png" '
        f'width="1" height="1" alt="" style="display:none">'
    )
    return f'<html><body style="font-family:sans-serif;">{corps_html}{pixel}</body></html>'
