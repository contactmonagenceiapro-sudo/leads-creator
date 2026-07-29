"""
Lancement des scripts de fond (scraping, pipelines B2B, campagnes d'e-mail...)
en subprocess depuis le dashboard Streamlit — remplace /agent/trigger et
/agent/status (api/main.py, supprimé). Le handle du processus (Popen) est
stocké directement dans st.session_state, qui persiste entre les reruns
d'une même session navigateur : plus besoin d'un dict en mémoire côté
serveur séparé pour faire le lien entre le clic du bouton et le suivi de
progression.

Limite connue (acceptée) : deux onglets/navigateurs différents ont chacun
leur propre st.session_state — un job lancé depuis un onglet n'est visible
que depuis ce même onglet. Sans impact pour un usage à un seul admin.

Les pipelines B2B (outbound_chantiers/) DOIVENT rester lancés en subprocess
frais (jamais appelés in-process) : outbound_chantiers/config.py lit
OUTBOUND_CAMPAGNE_JSON au moment de l'IMPORT du module, donc un process
Streamlit longue durée qui aurait déjà importé ce module une première fois
ne relirait jamais une configuration de campagne différente sans un
importlib.reload explicite. Un subprocess frais repart toujours d'un
interpréteur Python vierge : c'est le mécanisme le plus simple et le plus
sûr, on le garde pour toutes les actions ci-dessous, pas seulement celles-ci.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from supabase_client import supabase

RACINE_REPO = Path(__file__).resolve().parent.parent


def _cle_session(action: str, campagne: str | None) -> str:
    return f"_processus_en_cours::{action}::{campagne or '_default_'}"


def construire_env_campagne(
    nom_client: str | None, communes: list[str] | None = None, types_acteur: list[str] | None = None
) -> dict:
    """Construit les variables d'environnement du subprocess à partir d'une
    campagne enregistrée, avec surcharges ponctuelles (communes/types_acteur)
    pour ce run précis, SANS jamais modifier la configuration persistée."""
    env = os.environ.copy()
    if not nom_client:
        return env

    resultats = supabase.table("campagnes").select("*").eq("nom_client", nom_client).limit(1).execute().data
    campagne = resultats[0] if resultats else None
    if not campagne:
        return env

    if communes:
        campagne = {**campagne, "communes_cibles": communes}
    if types_acteur:
        cles_gardees = set(types_acteur)
        campagne = {
            **campagne,
            "types_acteur_cibles": [
                t for t in (campagne.get("types_acteur_cibles") or []) if t.get("cle") in cles_gardees
            ],
        }

    env["OUTBOUND_CAMPAGNE_JSON"] = json.dumps(campagne, ensure_ascii=False)
    return env


def lancer(action: str, campagne: str | None, commande: list[str], env: dict | None = None) -> None:
    """Lance `commande` en subprocess (cwd = racine du dépôt, pour que les
    chemins relatifs internes aux scripts — leads.json, article*.md... —
    se résolvent au même endroit qu'avant) et enregistre le handle pour
    suivi via statut()/afficher_suivi()."""
    processus = subprocess.Popen(commande, cwd=str(RACINE_REPO), env=env)
    st.session_state[_cle_session(action, campagne)] = {
        "processus": processus,
        "demarre_a": datetime.now(timezone.utc),
    }


def a_un_suivi(action: str, campagne: str | None = None) -> bool:
    """True si une tâche a été lancée au moins une fois dans cette session
    pour cette action/campagne (permet à afficher_suivi() de savoir s'il y a
    quelque chose à afficher)."""
    return _cle_session(action, campagne) in st.session_state


def statut(action: str, campagne: str | None = None) -> dict:
    """Équivalent de l'ancien GET /agent/status, lu depuis st.session_state
    au lieu d'un dict en mémoire côté API."""
    info = st.session_state.get(_cle_session(action, campagne))
    if not info:
        return {"action": action, "state": "jamais_lance"}

    ecoule = (datetime.now(timezone.utc) - info["demarre_a"]).total_seconds()
    code_retour = info["processus"].poll()
    if code_retour is None:
        return {"action": action, "state": "en_cours", "elapsed_seconds": round(ecoule)}
    return {
        "action": action,
        "state": "termine" if code_retour == 0 else "erreur",
        "elapsed_seconds": round(ecoule),
        "returncode": code_retour,
    }


def effacer_suivi(action: str, campagne: str | None = None) -> None:
    st.session_state.pop(_cle_session(action, campagne), None)


def lancer_scraping() -> None:
    lancer("scraping", None, [sys.executable, str(RACINE_REPO / "scraper_batiment.py")])


def lancer_leads() -> None:
    lancer("leads", None, [sys.executable, str(RACINE_REPO / "lead_worker.py")])


def lancer_mail_check() -> None:
    lancer("mail_check", None, [sys.executable, str(RACINE_REPO / "mail_processor.py")])


def lancer_ceo_report() -> None:
    lancer("ceo_report", None, [sys.executable, str(RACINE_REPO / "ceo_agent.py")])


def lancer_relance() -> None:
    lancer("relance", None, [sys.executable, str(RACINE_REPO / "relance_prospects.py")])


def lancer_pipeline_b2b(
    action: str, campagne: str, communes: list[str] | None = None, types_acteur: list[str] | None = None
) -> None:
    """`action` parmi 'pipeline_pro' (sourcing seul), 'envoi_pro' (campagne +
    relances seules), 'pipeline_auto' (tout enchaîné)."""
    arguments = {
        "pipeline_pro": [],
        "envoi_pro": ["--envoi-seul"],
        "pipeline_auto": ["--avec-envoi"],
    }[action]
    env = construire_env_campagne(campagne, communes, types_acteur)
    commande = [sys.executable, "-m", "outbound_chantiers.pipeline_outbound_chantiers"] + arguments
    lancer(action, campagne, commande, env=env)
