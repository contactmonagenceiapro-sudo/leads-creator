"""
Blacklist durable des adresses e-mail à ne plus jamais recontacter — table
emails_blacklistes (voir sql/init_bounces.sql), alimentée par
mail_processor.py sur deux événements : un rebond DÉFINITIF (hard bounce,
voir traiter_bounce()) et une désinscription explicite ("STOP", voir
update_lead_status()/update_lead_professionnel_status(), correctif du
10/08 — RGPD/droit d'opposition, art. L34-5 CPCE). Consultée par tous les
pipelines d'envoi (lead_worker.py, ceo_agent.py, relance_prospects.py,
outbound_chantiers/outbound_pro_btp.py) AVANT chaque campagne.

Nécessaire EN PLUS du statut posé sur le lead lui-même (status='invalide'/
'decline' côté leads, statut='invalide'/'decline' côté leads_professionnels) :
un même email peut réapparaître sur une NOUVELLE ligne lead après un
re-scraping ultérieur (nouvel id, statut réinitialisé à 'new'/'a_contacter')
— seule cette table, indépendante des lignes leads, survit à ça.

Module séparé (ni dans mail_processor.py, ni dans ceo_agent.py) pour
éviter tout risque de cycle d'import : mail_processor.py importe déjà
ceo_agent.py de façon différée pour la même raison (voir
envoyer_suivi_positif()).
"""

import logging
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _headers(on_conflict: str | None = None) -> dict:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if on_conflict:
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    return headers


def emails_blacklistes() -> set[str]:
    """Ensemble de TOUTES les adresses blacklistées — à récupérer UNE fois
    par run (pas par lead) avant d'envoyer une campagne, jamais une requête
    par lead (même pattern que sourcing_acteurs_pro.py::recuperer_sirens_deja_connus).
    Échec réseau ou table absente (migration sql/init_bounces.sql pas encore
    appliquée) -> ensemble vide : on ne bloque jamais tout un run pour une
    panne de lecture de cette table (le statut du lead reste la protection
    principale, celle-ci n'est qu'une protection supplémentaire)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/emails_blacklistes",
            headers=_headers(),
            params={"select": "email"},
            timeout=10,
        )
        if reponse.status_code == 200:
            return {ligne["email"].lower() for ligne in reponse.json() if ligne.get("email")}
        log.warning(f"Lecture emails_blacklistes : HTTP {reponse.status_code} — {reponse.text[:200]}")
    except requests.exceptions.RequestException as e:
        log.error(f"Impossible de charger la blacklist e-mail : {e}")
    return set()


def blacklister_email(
    email_address: str,
    raison: str,
    code_statut: str | None = None,
    diagnostic: str | None = None,
    lead_type: str | None = None,
    lead_id: str | None = None,
) -> None:
    """Ajoute (ou met à jour, upsert sur email) une adresse à la blacklist
    durable — à appeler UNIQUEMENT sur un hard bounce confirmé (voir
    mail_processor.py::traiter_bounce), jamais sur un simple délai
    temporaire (le serveur destinataire retente encore dans ce cas)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    email_address = email_address.strip().lower()
    domaine = email_address.split("@")[-1] if "@" in email_address else None
    try:
        reponse = requests.post(
            f"{SUPABASE_URL}/rest/v1/emails_blacklistes",
            headers=_headers(on_conflict="email"),
            params={"on_conflict": "email"},
            json={
                "email": email_address,
                "domaine": domaine,
                "raison": raison,
                "code_statut": code_statut,
                "diagnostic": diagnostic,
                "lead_type": lead_type,
                "lead_id": lead_id,
            },
            timeout=10,
        )
        if reponse.status_code in (200, 201):
            log.info(f"📵 {email_address} ajouté à la blacklist ({raison})")
        else:
            log.error(f"Échec d'ajout à la blacklist pour {email_address} : HTTP {reponse.status_code} — {reponse.text[:200]}")
    except requests.exceptions.RequestException as e:
        log.error(f"Échec d'ajout à la blacklist pour {email_address} : {e}")
