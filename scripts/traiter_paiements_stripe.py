#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
traiter_paiements_stripe.py
============================

Traite la file d'attente `stripe_webhook_events` (remplie par la Supabase
Edge Function supabase/functions/stripe-webhook/, qui se contente de
vérifier la signature Stripe et d'insérer l'événement brut — Streamlit
Community Cloud ne pouvant pas recevoir de requête HTTP entrante, voir
sql/init_stripe_webhook_events.sql pour l'architecture complète).

Remplace la confirmation manuelle du paiement pour l'événement
checkout.session.completed — SEUL type d'événement mis en file par la Edge
Function. Les deux flux existants se distinguent par les clés présentes
dans session.metadata :
- contract_id -> abonnement/à l'unité signé via un contrat (voir
  dashboard/contrats_signature.py::creer_et_envoyer_lien_paiement).
- demande_id -> proposition à l'unité sur une demande de devis particulier
  (voir livraison_devis.py::_proposer).

Reproduit ICI (pas importé) la même logique que dashboard/data_access.py::
marquer_contrat_paye / marquer_demande_devis_payee_et_livree, plutôt que
d'importer ces deux fonctions précises : elles appellent
journaliser_action_admin(), qui lit l'identité de l'appelant dans
st.session_state — non défini/fiable hors d'une exécution `streamlit run`
réelle, contrairement aux fonctions de LECTURE de data_access.py (ex.
get_taux_bounce), déjà importées sans problème par des scripts cron
autonomes ailleurs dans ce projet (voir scripts/controle_delivrabilite.py).
Ce script journalise directement avec utilisateur_email="webhook_stripe",
explicite plutôt que de dépendre d'un session_state absent. Toute évolution
de la règle métier (marquer payé / livrer) doit être répercutée des DEUX
côtés.

Les boutons manuels du dashboard restent le filet de secours (événement
Stripe manqué, webhook down, secret mal configuré...) — ce script ne les
remplace pas, il les rend simplement inutiles dans le cas nominal.

Usage :
    python3 scripts/traiter_paiements_stripe.py
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from alertes import alerter_discord  # noqa: E402

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PAIEMENTS-STRIPE] %(message)s")
log = logging.getLogger(__name__)


def _traiter_contrat(contract_id: str, payment_intent_id: str | None) -> None:
    """Même effet que dashboard/data_access.py::marquer_contrat_paye — voir
    le docstring du module pour pourquoi c'est dupliqué plutôt qu'importé."""
    contrats = supabase.table("contracts").select("*").eq("id", contract_id).execute().data
    if not contrats:
        raise ValueError(f"Contrat introuvable : {contract_id}")
    contrat = contrats[0]

    if contrat.get("payment_status") == "paye":
        log.info(f"Contrat {contract_id} déjà marqué payé — événement ignoré (doublon Stripe ou déjà traité manuellement).")
        return

    champs_maj = {"payment_status": "paye", "paid_at": datetime.now(timezone.utc).isoformat()}
    if payment_intent_id:
        champs_maj["stripe_payment_intent_id"] = payment_intent_id
    supabase.table("contracts").update(champs_maj).eq("id", contract_id).execute()
    supabase.table("leads").update({"status": "paye"}).eq("id", contrat["lead_id"]).execute()
    _journaliser("marquer_contrat_paye", "contract", contract_id, {
        "lead_id": contrat["lead_id"], "stripe_payment_intent_id": payment_intent_id, "source": "webhook_stripe",
    })
    log.info(f"Contrat {contract_id} marqué payé automatiquement (webhook Stripe).")


def _traiter_demande_devis(demande_id: str, payment_intent_id: str | None) -> None:
    """Même effet que dashboard/data_access.py::marquer_demande_devis_payee_et_livree
    — voir _traiter_contrat ci-dessus pour la raison de la duplication."""
    demandes = supabase.table("demandes_devis_particuliers").select("*").eq("id", demande_id).execute().data
    if not demandes:
        raise ValueError(f"Demande de devis introuvable : {demande_id}")
    demande = demandes[0]

    if demande.get("statut") != "proposee":
        log.info(
            f"Demande {demande_id} n'est plus en attente de paiement "
            f"(statut={demande.get('statut')}) — événement ignoré."
        )
        return
    if not demande.get("lead_id_livraison"):
        raise ValueError(f"Demande {demande_id} sans artisan destinataire — impossible de livrer.")

    champs_maj = {"statut": "livree", "livree_le": datetime.now(timezone.utc).isoformat()}
    if payment_intent_id:
        champs_maj["stripe_payment_intent_id"] = payment_intent_id
    supabase.table("demandes_devis_particuliers").update(champs_maj).eq("id", demande_id).execute()
    _journaliser("marquer_demande_devis_payee_et_livree", "demande_devis_particulier", demande_id, {
        "stripe_payment_intent_id": payment_intent_id, "source": "webhook_stripe",
    })
    log.info(f"Demande de devis {demande_id} livrée automatiquement (webhook Stripe).")


def _journaliser(action: str, cible_type: str, cible_id: str, detail: dict) -> None:
    """Même table que dashboard/data_access.py::journaliser_action_admin,
    utilisateur_id laissé NULL (colonne nullable, voir
    sql/init_journal_audit_admin.sql) : aucun compte utilisateurs_dashboard
    ne correspond à ce déclencheur automatique. Best effort, comme
    l'original — un échec d'écriture du journal ne doit jamais faire
    remonter une erreur sur l'action métier déjà effectuée avec succès."""
    try:
        supabase.table("journal_audit_admin").insert({
            "utilisateur_email": "webhook_stripe",
            "action": action,
            "cible_type": cible_type,
            "cible_id": cible_id,
            "detail": detail,
        }).execute()
    except Exception as e:
        log.error(f"Échec écriture journal_audit_admin (action={action}, cible={cible_type}/{cible_id}) : {e}")


def _traiter_un_evenement(evenement: dict) -> None:
    payload = evenement.get("payload") or {}
    session = (payload.get("data") or {}).get("object") or {}
    metadata = session.get("metadata") or {}
    payment_intent_id = session.get("payment_intent")
    contract_id = metadata.get("contract_id")
    demande_id = metadata.get("demande_id")

    if contract_id:
        _traiter_contrat(contract_id, payment_intent_id)
    elif demande_id:
        _traiter_demande_devis(demande_id, payment_intent_id)
    else:
        raise ValueError("Ni contract_id ni demande_id dans les métadonnées de la session Stripe.")


def traiter_file_attente() -> None:
    evenements = (
        supabase.table("stripe_webhook_events")
        .select("*")
        .eq("statut", "recu")
        .order("created_at")
        .execute()
        .data
    )
    if not evenements:
        log.info("Aucun événement Stripe en attente.")
        return

    log.info(f"{len(evenements)} événement(s) Stripe à traiter.")
    for evenement in evenements:
        try:
            _traiter_un_evenement(evenement)
            supabase.table("stripe_webhook_events").update({
                "statut": "traite",
                "traite_le": datetime.now(timezone.utc).isoformat(),
            }).eq("id", evenement["id"]).execute()
        except Exception as e:
            log.error(f"Échec traitement événement {evenement['id']} : {e}")
            supabase.table("stripe_webhook_events").update({
                "statut": "echec",
                "erreur": str(e),
                "traite_le": datetime.now(timezone.utc).isoformat(),
            }).eq("id", evenement["id"]).execute()
            # Un paiement reçu mais jamais activé (leads pas livrés, abonnement
            # pas ouvert) doit être vu vite — pas seulement découvert au
            # prochain coup d'œil admin sur la table, voir alertes.py.
            alerter_discord(f"⚠️ Échec traitement d'un paiement Stripe (événement {evenement['id']}) : {e}")


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("Identifiants Supabase manquants — arrêt.")
        raise SystemExit(1)
    traiter_file_attente()


if __name__ == "__main__":
    main()
