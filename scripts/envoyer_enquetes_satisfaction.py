#!/usr/bin/env python3
"""
Module 4 (pilotage) — envoie une enquête de satisfaction aux artisans dont
le PREMIER paiement (contracts.paid_at) date d'il y a environ une semaine
(délai confirmé par l'utilisateur le 27/08/2026, voir
sql/init_satisfaction_enquetes.sql pour le détail du choix et de la
doctrine de réponse manuelle).

Fenêtre J-6/J-8 (pas juste "il y a 7 jours" exactement) : ce script est
déclenché par un cron QUOTIDIEN (pas horaire), une fenêtre de 3 jours
absorbe sans double-envoi un run manqué ou décalé — la contrainte
UNIQUE(contract_id) sur satisfaction_enquetes empêche de toute façon tout
doublon même en cas de chevauchement entre deux runs.

Usage : python3 scripts/envoyer_enquetes_satisfaction.py
Déclenché quotidiennement par .github/workflows/envoyer_enquetes_satisfaction.yml.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "dashboard"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SATISFACTION] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from supabase_client import supabase  # noqa: E402
from ceo_agent import CompteZohoBloqueError, send_email_prospect  # noqa: E402

DELAI_ENQUETE_JOURS = 7
FENETRE_JOURS = 1  # marge de chaque côté du délai cible (6 à 8 jours après paiement)


def main() -> None:
    borne_haute = (datetime.now(timezone.utc) - timedelta(days=DELAI_ENQUETE_JOURS - FENETRE_JOURS)).isoformat()
    borne_basse = (datetime.now(timezone.utc) - timedelta(days=DELAI_ENQUETE_JOURS + FENETRE_JOURS)).isoformat()

    contrats = (
        supabase.table("contracts").select("id,lead_id,paid_at,leads(email,company)")
        .eq("payment_status", "paye").gte("paid_at", borne_basse).lte("paid_at", borne_haute)
        .execute().data
    )
    if not contrats:
        log.info("Aucun contrat payé dans la fenêtre J-6/J-8 — rien à envoyer.")
        return

    deja_envoyees = {
        e["contract_id"]
        for e in supabase.table("satisfaction_enquetes").select("contract_id").execute().data
    }

    envoyees = 0
    eligibles = [c for c in contrats if c["id"] not in deja_envoyees]
    for i, contrat in enumerate(eligibles, start=1):
        lead = contrat.get("leads") or {}
        email = lead.get("email")
        if not email:
            log.warning(f"Contrat {contrat['id']} sans e-mail lead associé — enquête non envoyée.")
            continue

        corps = (
            "Bonjour,\n\n"
            "Cela fait maintenant une semaine que vous recevez des demandes de devis via notre "
            "service — nous aimerions avoir votre avis.\n\n"
            "Pourriez-vous répondre à cet e-mail avec :\n"
            "- Une note de satisfaction de 0 à 10\n"
            "- Un commentaire libre si vous le souhaitez (qualité des leads, réactivité, "
            "ce qu'on pourrait améliorer...)\n\n"
            "Merci pour votre retour, il nous est précieux.\n\nCordialement"
        )
        try:
            envoye = send_email_prospect(email, "Votre avis sur nos services", corps, lead_id=contrat["lead_id"])
        except CompteZohoBloqueError:
            # Déjà loggé + alerté dans send_email_prospect (voir livraison_devis.py
            # pour le même traitement) — inutile de continuer, les envois
            # suivants échoueraient tous pareil tant que le blocage n'est pas levé.
            log.error(f"Envoi interrompu après {i - 1}/{len(eligibles)} enquête(s) (compte Zoho bloqué).")
            break

        if envoye:
            supabase.table("satisfaction_enquetes").insert({
                "lead_id": contrat["lead_id"],
                "contract_id": contrat["id"],
                "envoyee_le": datetime.now(timezone.utc).isoformat(),
            }).execute()
            envoyees += 1
            log.info(f"Enquête envoyée à {lead.get('company') or email} (contrat {contrat['id']}).")
        else:
            log.error(f"Échec d'envoi de l'enquête pour le contrat {contrat['id']} — retentée au prochain run.")

    log.info(f"=== Terminé : {envoyees}/{len(eligibles)} enquête(s) envoyée(s) ===")


if __name__ == "__main__":
    main()
