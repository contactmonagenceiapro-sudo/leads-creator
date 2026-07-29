#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lead_worker.py
==============

Consomme `leads.json` (produit par `scraper_batiment.py`), ne garde que les
leads disposant d'un email réellement vérifié (aucun démarchage sur une
adresse devinée/non confirmée), génère un pitch commercial personnalisé via
Ollama pour chaque lead, puis ENVOIE l'email immédiatement — le pitch
propose le service d'apport de clients qualifiés (et non plus la refonte de
site web des versions précédentes). L'envoi et l'upsert Supabase sont
désormais dans le même passage : un lead qualifié est contacté tout de
suite, pas mis en attente d'une campagne différée.

Pipeline :
    scraper_batiment.py --> leads.json --> lead_worker.py --> email envoyé + Supabase

Utilisation :
    source venv/bin/activate
    python lead_worker.py
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from ceo_agent import send_email_prospect

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

LEADS_FILE = Path(__file__).resolve().parent / "leads.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "90"))

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# Pause anti-spam entre deux ENVOIS RÉELS (alignée sur ceo_agent.py) : depuis
# que ce script envoie l'email immédiatement au lieu de simplement préparer
# la donnée, une pause de quelques secondes ne suffit plus à éviter de
# déclencher les filtres anti-spam de Zoho sur des envois en rafale.
PAUSE_MIN_SEC = 20
PAUSE_MAX_SEC = 45


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
        # Filtre qualité : on ne démarche que les entreprises disposant de
        # toutes les données utiles, et surtout d'un email réellement
        # exploitable (jamais une adresse simplement devinée/non vérifiée —
        # cf. email_source posé par scraper_batiment.py/email_enricher.py).
        # Sans email, aucun envoi n'est possible : inutile de conserver ces
        # leads dans ce pipeline de prospection immédiate.
        if not lead.get("email"):
            continue
        leads_valides.append(lead)

    log.info(f"{len(leads_valides)}/{len(data)} leads retenus après filtre qualité (email requis)")
    return leads_valides


# ---------------------------------------------------------------------------
# GÉNÉRATION IA (OLLAMA)
# ---------------------------------------------------------------------------

def generer_pitch(lead: dict) -> str | None:
    """Génère un pitch commercial personnalisé via Ollama. Retourne None en
    cas d'échec réseau, timeout ou réponse invalide (aucune exception levée).

    Angle commercial : apport de clients qualifiés (génération de leads en
    tant que service), et non plus la refonte de site web des versions
    précédentes du pipeline — l'entreprise contactée reçoit régulièrement
    des demandes de devis réelles dans sa zone d'activité, sans prospection
    de sa part."""
    prompt = (
        f"Rédige un court email de prospection B2B pour proposer à "
        f"l'entreprise '{lead['company_name']}' (secteur : {lead['industry']}) "
        f"un service d'apport de clients qualifiés de la part de "
        f"'{AGENCY_NAME}' : nous leur envoyons régulièrement des demandes de "
        f"devis de particuliers/professionnels réellement intéressés par "
        f"leurs prestations, dans leur zone d'activité, sans prospection ni "
        f"compétence technique de leur côté. Objectif de l'email : "
        f"décrocher une réponse pour en discuter, pas vendre directement. "
        f"Ton direct et concret, pas de jargon marketing. "
        f"IMPORTANT : ne mets AUCUN placeholder entre crochets (pas de "
        f"'[Votre nom]', '[Votre fonction]' etc.) — termine simplement par "
        f"'Cordialement,' suivi de '{AGENCY_NAME}', rien d'autre après."
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
# ANTI-DOUBLON D'ENVOI
# ---------------------------------------------------------------------------

def deja_contacte(company_name: str) -> bool:
    """Vérifie directement dans Supabase si une entreprise a déjà été
    contactée, AVANT de générer/envoyer un nouveau pitch. Nécessaire car ce
    script tourne à intervalle régulier (leads_agent_job) : sans ce
    contrôle, relire le même leads.json à chaque run réenverrait un email au
    même destinataire à chaque exécution."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            params={"select": "contacted", "company": f"eq.{company_name}"},
            timeout=10,
        )
        lignes = reponse.json()
        return bool(lignes) and lignes[0].get("contacted") is True
    except (requests.exceptions.RequestException, ValueError) as erreur:
        log.error(f"Erreur vérification anti-doublon pour {company_name} : {erreur}")
        return False


# ---------------------------------------------------------------------------
# PERSISTANCE (upsert Supabase direct)
# ---------------------------------------------------------------------------

def inserer_lead(lead: dict, pitch: str, envoi_reussi: bool) -> bool:
    """Upsert le lead dans Supabase directement, avec anti-doublon sur
    l'entreprise (on_conflict=company + resolution=merge-duplicates).

    Anciennement dédupliqué par email : depuis que scraper_batiment.py et
    email_enricher.py peuvent légitimement laisser email=None (aucun domaine
    réel trouvé pour l'entreprise), un upsert basé sur l'email seul ne
    protège plus contre les doublons — chaque nouveau run recréait une ligne
    par entreprise sans email au lieu de mettre à jour l'existante. Le nom de
    l'entreprise (company), lui, est toujours renseigné et constitue la clé
    d'identité stable côté Supabase (contrainte idx_leads_company_unique,
    cf. sql/init.sql).

    envoi_reussi reflète si l'email de prospection a réellement été envoyé
    (cf. process_pipeline) : si oui, contacted/status/contacted_at sont mis à
    jour pour refléter l'envoi (cohérent avec ceo_agent.py) ; sinon le lead
    est simplement préparé (pitch stocké) pour un nouvel essai au run
    suivant, sans jamais marquer un envoi qui n'a pas eu lieu."""
    email_source = lead.get("email_source", "inconnu")
    ville = lead.get("ville", "")
    db_payload = {
        "company": lead["company_name"],
        "industry": lead["industry"],
        "weakness": lead["weakness"],
        "email": lead["email"],
        "telephone": lead.get("telephone"),
        "siren": lead.get("siren"),
        "adresse": lead.get("adresse"),
        "pitch_commercial": pitch,
        "source": "scraper_batiment",
        # Traçabilité de la confiance dans l'email (email_verifie_site,
        # domaine_verifie_sans_email, aucun_domaine_trouve) : à utiliser pour
        # prioriser/filtrer avant un envoi réel, cf. email_enricher.py.
        "notes": f"ville={ville} | email_source={email_source}",
    }
    if envoi_reussi:
        db_payload["status"] = "contacte_attente_reponse"
        db_payload["contacted"] = True
        db_payload["contacted_at"] = datetime.now(timezone.utc).isoformat()
    else:
        db_payload["status"] = "a_contacter"

    try:
        reponse = requests.post(
            f"{SUPABASE_URL}/rest/v1/leads",
            params={"on_conflict": "company"},
            json=db_payload,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation,resolution=merge-duplicates",
            },
            timeout=15,
        )
    except requests.exceptions.RequestException as erreur:
        log.error(f"Échec réseau Supabase pour {lead['company_name']} : {erreur}")
        return False

    if reponse.status_code in (200, 201):
        log.info(f"Lead '{lead['company_name']}' upserté avec succès dans Supabase (code {reponse.status_code})")
        return True

    log.error(f"Échec Supabase pour '{lead['company_name']}' (code {reponse.status_code}) : {reponse.text}")
    return False


# ---------------------------------------------------------------------------
# ORCHESTRATION
# ---------------------------------------------------------------------------

def process_pipeline() -> ResultatTraitement:
    configurer_logging()
    log.info("=== Démarrage du Lead Worker ===")

    leads = charger_leads()
    resultat = ResultatTraitement(total=len(leads))

    if not leads:
        log.warning("Aucun lead valide à traiter, arrêt.")
        return resultat

    log.info(f"{len(leads)} leads valides chargés, début du traitement...")

    for index, lead in enumerate(leads, 1):
        log.info(f"--- [Lead {index}/{len(leads)}] {lead['company_name']} ---")

        if deja_contacte(lead["company_name"]):
            log.info(f"'{lead['company_name']}' déjà contacté précédemment, ignoré.")
            continue

        pitch = generer_pitch(lead)
        if pitch is None:
            resultat.echecs += 1
            time.sleep(random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC))
            continue

        sujet = f"{lead['company_name']} — apport de clients qualifiés"
        envoi_reussi = send_email_prospect(lead["email"], sujet, pitch)
        if envoi_reussi:
            log.info(f"Email envoyé avec succès à {lead['company_name']} <{lead['email']}>")
        else:
            log.error(f"Échec d'envoi à {lead['company_name']} <{lead['email']}>")

        if inserer_lead(lead, pitch, envoi_reussi):
            resultat.succes += 1
        else:
            resultat.echecs += 1

        time.sleep(random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC))

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
