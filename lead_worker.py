#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lead_worker.py
==============

Consomme `leads.json` (produit par `scraper_batiment.py`), génère un pitch
commercial personnalisé pour chaque lead via Ollama, puis upsert le résultat
dans Supabase (table `leads`) via l'API interne (`api/main.py`).

Pipeline :
    scraper_batiment.py  -->  leads.json  -->  lead_worker.py  -->  Supabase

Utilisation :
    source venv/bin/activate
    python lead_worker.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

LEADS_FILE = Path(__file__).resolve().parent / "leads.json"

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "90"))

PAUSE_ENTRE_LEADS_SEC = 3


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

class FormatteurPrefixe(logging.Formatter):
    PREFIXES = {
        logging.DEBUG: "[*]",
        logging.INFO: "[+]",
        logging.WARNING: "[!]",
        logging.ERROR: "[x]",
        logging.CRITICAL: "[x]",
    }

    def format(self, record):
        prefixe = self.PREFIXES.get(record.levelno, "[*]")
        return f"{prefixe} {record.getMessage()}"


def configurer_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatteurPrefixe())
    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.handlers.clear()
    racine.addHandler(handler)


log = logging.getLogger(__name__)


@dataclass
class ResultatTraitement:
    total: int = 0
    succes: int = 0
    echecs: int = 0


# ---------------------------------------------------------------------------
# CHARGEMENT DES LEADS
# ---------------------------------------------------------------------------

def charger_leads(chemin: Path = LEADS_FILE) -> list[dict]:
    """Charge et valide le fichier leads.json. Ne lève jamais d'exception :
    retourne une liste vide si le fichier est absent, vide ou corrompu."""
    if not chemin.exists():
        log.error(f"Fichier introuvable : {chemin}")
        return []

    try:
        with open(chemin, "r", encoding="utf-8") as fichier:
            data = json.load(fichier)
    except json.JSONDecodeError as erreur:
        log.error(f"JSON invalide dans {chemin} : {erreur}")
        return []
    except (IOError, OSError) as erreur:
        log.error(f"Impossible de lire {chemin} : {erreur}")
        return []

    if not isinstance(data, list):
        log.error(f"{chemin} doit contenir une liste JSON, reçu : {type(data).__name__}")
        return []

    leads_valides = []
    champs_requis = ("company_name", "industry", "weakness", "email")
    for i, lead in enumerate(data):
        if not isinstance(lead, dict) or not all(champ in lead for champ in champs_requis):
            log.warning(f"Lead #{i} ignoré : champs requis manquants ({champs_requis})")
            continue
        leads_valides.append(lead)

    return leads_valides


# ---------------------------------------------------------------------------
# GÉNÉRATION IA (OLLAMA)
# ---------------------------------------------------------------------------

def generer_pitch(lead: dict) -> str | None:
    """Génère un pitch commercial personnalisé via Ollama. Retourne None en
    cas d'échec réseau, timeout ou réponse invalide (aucune exception levée)."""
    prompt = (
        f"Rédige un court email de vente percutant pour l'entreprise "
        f"'{lead['company_name']}' dans le secteur '{lead['industry']}'. "
        f"Ils ont un problème majeur : {lead['weakness']}. "
        f"Propose une solution simple de la part de 'Holding'. "
        f"Pas de placeholders, texte direct."
    )

    try:
        reponse = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        reponse.raise_for_status()
    except requests.exceptions.Timeout:
        log.error(f"Timeout Ollama ({OLLAMA_TIMEOUT}s) pour {lead['company_name']}")
        return None
    except requests.exceptions.RequestException as erreur:
        log.error(f"Échec appel Ollama ({OLLAMA_HOST}) pour {lead['company_name']} : {erreur}")
        return None

    try:
        pitch = reponse.json().get("response", "").strip()
    except ValueError:
        log.error(f"Réponse Ollama non-JSON pour {lead['company_name']}")
        return None

    if not pitch:
        log.warning(f"Pitch vide généré pour {lead['company_name']}")
        return None

    return pitch


# ---------------------------------------------------------------------------
# PERSISTANCE (upsert Supabase via l'API interne)
# ---------------------------------------------------------------------------

def inserer_lead(lead: dict, pitch: str) -> bool:
    """Upsert le lead dans Supabase via l'API interne, avec anti-doublon sur
    l'email (on_conflict=email + resolution=merge-duplicates côté API)."""
    db_payload = {
        "company": lead["company_name"],
        "industry": lead["industry"],
        "weakness": lead["weakness"],
        "email": lead["email"],
        "pitch_commercial": pitch,
        "status": "a_contacter",
        "contacted": False,
        "source": "scraper_batiment",
    }

    headers = {"X-API-Key": API_SECRET_KEY} if API_SECRET_KEY else {}

    try:
        reponse = requests.post(
            f"{API_URL}/sb_insert",
            params={"table": "leads", "on_conflict": "email"},
            json=db_payload,
            headers=headers,
            timeout=15,
        )
        reponse.raise_for_status()
    except requests.exceptions.RequestException as erreur:
        log.error(f"Échec réseau vers l'API ({API_URL}) pour {lead['company_name']} : {erreur}")
        return False

    try:
        resultat = reponse.json()
    except ValueError:
        log.error(f"Réponse API non-JSON pour {lead['company_name']}")
        return False

    status_code = resultat.get("status_code")
    if status_code in (200, 201):
        log.info(f"Lead '{lead['company_name']}' upserté avec succès dans Supabase (code {status_code})")
        return True

    log.error(f"Échec Supabase pour '{lead['company_name']}' (code {status_code}) : {resultat.get('response')}")
    return False


# ---------------------------------------------------------------------------
# ORCHESTRATION
# ---------------------------------------------------------------------------

def process_pipeline() -> ResultatTraitement:
    configurer_logging()
    log.info("=== Démarrage du Lead Worker ===")

    if not API_SECRET_KEY:
        log.warning("API_SECRET_KEY absente de .env : les appels à l'API échoueront si elle est protégée")

    leads = charger_leads()
    resultat = ResultatTraitement(total=len(leads))

    if not leads:
        log.warning("Aucun lead valide à traiter, arrêt.")
        return resultat

    log.info(f"{len(leads)} leads valides chargés, début du traitement...")

    for index, lead in enumerate(leads, 1):
        log.info(f"--- [Lead {index}/{len(leads)}] {lead['company_name']} ---")

        pitch = generer_pitch(lead)
        if pitch is None:
            resultat.echecs += 1
            time.sleep(PAUSE_ENTRE_LEADS_SEC)
            continue

        if inserer_lead(lead, pitch):
            resultat.succes += 1
        else:
            resultat.echecs += 1

        time.sleep(PAUSE_ENTRE_LEADS_SEC)

    log.info(
        f"=== Terminé : {resultat.succes} succès / {resultat.echecs} échecs "
        f"sur {resultat.total} leads ==="
    )
    return resultat


def main() -> int:
    resultat = process_pipeline()
    if resultat.total == 0:
        return 1
    return 0 if resultat.echecs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
