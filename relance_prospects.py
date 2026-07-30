#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relance_prospects.py
=====================

Relance automatiquement les prospects contactés qui n'ont donné aucune
réponse (positive ou négative détectée par mail_processor.py) après un
délai raisonnable. Avant ce script, un prospect contacté sans réponse
restait indéfiniment en status "contacte_attente_reponse", sans aucune
action de suivi — le tunnel de vente s'arrêtait silencieusement là pour la
grande majorité des prospects (peu de gens répondent à un premier email).

Règles :
    - 1ère relance : DELAI_PREMIERE_RELANCE_JOURS (4j par défaut) après le
      premier contact (contacted_at), si relance_count == 0.
    - 2e relance (dernière) : DELAI_RELANCE_SUIVANTE_JOURS (4j par défaut)
      après la relance précédente (last_relance_at), si relance_count == 1.
    - Au-delà (relance_count >= MAX_RELANCES), le lead est marqué
      "sans_reponse" et n'est plus jamais recontacté automatiquement.

Nécessite les colonnes contacted_at / relance_count / last_relance_at sur
la table leads (cf. sql/init.sql).

Utilisation :
    source venv/bin/activate
    python relance_prospects.py
"""

import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client

from ceo_agent import send_email_prospect
from email_blacklist import emails_blacklistes

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RELANCE] %(message)s")
log = logging.getLogger(__name__)

DELAI_PREMIERE_RELANCE_JOURS = int(os.getenv("DELAI_PREMIERE_RELANCE_JOURS", "4"))
DELAI_RELANCE_SUIVANTE_JOURS = int(os.getenv("DELAI_RELANCE_SUIVANTE_JOURS", "4"))
MAX_RELANCES = int(os.getenv("MAX_RELANCES", "2"))

# Pause anti-spam entre deux ENVOIS RÉELS (alignée sur lead_worker.py,
# ceo_agent.py et outbound_pro_btp.py — tous partagent le même compte Zoho,
# donc la même limite anti-spam). Portée à 45-90s suite à un premier
# blocage Zoho ("Unusual sending activity detected"), puis à 60-120s après
# un second blocage malgré ce premier ajustement — configurable sans
# toucher au code si besoin de réajuster.
PAUSE_MIN_SEC = int(os.getenv("PAUSE_ENVOI_MIN_SEC", "60"))
PAUSE_MAX_SEC = int(os.getenv("PAUSE_ENVOI_MAX_SEC", "120"))

# Un message différent par palier : la 1ère relance rappelle poliment, la
# dernière prévient explicitement qu'on n'insistera plus (évite de harceler
# indéfiniment un prospect qui ne souhaite simplement pas répondre).
MESSAGES_RELANCE = [
    (
        "Bonjour,\n\n"
        "Je me permets de revenir vers vous suite à mon précédent message "
        "concernant {company} — peut-être est-il passé inaperçu.\n\n"
        "Si le sujet vous intéresse toujours, n'hésitez pas à me répondre, "
        "même brièvement : je me ferai un plaisir de vous en dire plus.\n\n"
        "Cordialement"
    ),
    (
        "Bonjour,\n\n"
        "Dernier message de ma part concernant {company} : si le moment "
        "n'est pas opportun, je comprends tout à fait et ne vous "
        "solliciterai plus à ce sujet.\n\n"
        "Si en revanche vous souhaitez qu'on en discute, il suffit de "
        "répondre à cet email.\n\nCordialement"
    ),
]


def recuperer_prospects_a_relancer() -> list[dict]:
    """Récupère les leads contactés sans réponse dont le délai de relance
    est dépassé, et qui n'ont pas encore atteint le nombre maximum de
    relances. Le filtre de délai (dépend du palier de chaque lead) est
    appliqué côté Python plutôt qu'en SQL : la référence de date change
    selon relance_count (contacted_at pour la 1ère, last_relance_at
    ensuite), ce qui est plus simple à exprimer ainsi qu'en filtre
    PostgREST conditionnel."""
    maintenant = datetime.now(timezone.utc)
    resultats = []
    try:
        reponse = (
            supabase.table("leads")
            .select("*")
            .eq("status", "contacte_attente_reponse")
            .lt("relance_count", MAX_RELANCES)
            .execute()
        )
    except Exception as e:
        log.error(f"Erreur Supabase lors de la récupération des prospects à relancer : {e}")
        return []

    # Filet de sécurité supplémentaire au statut (déjà passé à 'invalide' sur
    # hard bounce par mail_processor.py, donc déjà exclu du .eq("status", ...)
    # ci-dessus) : couvre le cas où le bounce n'a pas encore été traité au
    # moment de ce run (voir email_blacklist.py).
    blacklist = emails_blacklistes()

    for lead in reponse.data or []:
        if blacklist and (lead.get("email") or "").strip().lower() in blacklist:
            continue
        relance_count = lead.get("relance_count") or 0
        if relance_count == 0:
            reference = lead.get("contacted_at")
            delai = DELAI_PREMIERE_RELANCE_JOURS
        else:
            reference = lead.get("last_relance_at") or lead.get("contacted_at")
            delai = DELAI_RELANCE_SUIVANTE_JOURS

        if not reference:
            continue
        try:
            # Postgres tronque parfois les zéros de fin des microsecondes
            # (ex: '.88185' au lieu de '.881850'), que le parseur strict de
            # Python 3.10 rejette (n'accepte que 0, 3 ou 6 chiffres) — on
            # normalise à 6 chiffres avant de parser plutôt que de laisser
            # ce lead silencieusement ignoré à chaque run.
            valeur = reference.replace("Z", "+00:00")
            valeur = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], valeur)
            date_reference = datetime.fromisoformat(valeur)
        except ValueError:
            continue

        if maintenant - date_reference >= timedelta(days=delai):
            resultats.append(lead)

    return resultats


def relancer_prospects() -> None:
    prospects = recuperer_prospects_a_relancer()
    if not prospects:
        log.info("Aucun prospect à relancer aujourd'hui.")
        return

    log.info(f"{len(prospects)} prospect(s) à relancer.")

    for i, lead in enumerate(prospects, 1):
        company = lead.get("company") or "votre entreprise"
        email = lead.get("email")
        relance_count = lead.get("relance_count") or 0

        if not email:
            continue

        log.info(f"--- [{i}/{len(prospects)}] Relance #{relance_count + 1} pour {company} <{email}> ---")

        corps = MESSAGES_RELANCE[min(relance_count, len(MESSAGES_RELANCE) - 1)].format(company=company)
        sujet = f"Relance — {company}"
        succes = send_email_prospect(email, sujet, corps, lead_id=lead["id"])

        nouveau_count = relance_count + 1
        maj = {
            "relance_count": nouveau_count,
            "last_relance_at": datetime.now(timezone.utc).isoformat(),
        }
        if succes and nouveau_count >= MAX_RELANCES:
            # Dernière relance envoyée sans obtenir de réponse : on arrête
            # définitivement les tentatives automatiques sur ce lead plutôt
            # que de le solliciter indéfiniment.
            maj["status"] = "sans_reponse"

        if succes:
            log.info(f"Relance envoyée à {company}")
        else:
            log.error(f"Échec de la relance pour {company} <{email}>")

        supabase.table("leads").update(maj).eq("id", lead["id"]).execute()

        if i < len(prospects):
            pause = random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC)
            time.sleep(pause)


if __name__ == "__main__":
    relancer_prospects()
