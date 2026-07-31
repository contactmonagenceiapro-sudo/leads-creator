#!/usr/bin/env python3
"""
llm_config.py
=============

Configuration ET appel centralisés du LLM de génération de texte (pitchs de
prospection), utilisés par lead_worker.py, outbound_chantiers/outbound_pro_btp.py
et l'indicateur "État des services" du dashboard — un seul endroit pour
basculer entre Ollama local (dev) et une API cloud (prod), au lieu d'une
URL redéfinie séparément (et divergente) dans chaque script.

Résolution de l'URL, par ordre de priorité :
    1. LLM_API_URL  — endpoint explicite de production/cloud (ex: un Ollama
                       hébergé sur Render, ou toute API compatible avec la
                       route POST {url}/api/generate). À définir dès que
                       l'app tourne sans accès à un Ollama local (Streamlit
                       Cloud, serveur distant sans docker-compose...).
    2. OLLAMA_HOST  — variable historique, toujours supportée telle quelle
                       (docker-compose : http://ai_ollama:11434 ; lancement
                       local avec Ollama sur la même machine).
    3. http://127.0.0.1:11434 — repli par défaut (dev local, hors docker).

LLM_API_KEY (optionnel) : jeton envoyé en en-tête "Authorization: Bearer"
si l'endpoint cloud est protégé (un Ollama exposé publiquement sur Render
doit l'être, sans quoi n'importe qui peut consommer le quota de calcul).

generer_texte() ne lève JAMAIS d'exception réseau : en cas d'échec
(connexion refusée, timeout, réponse invalide/vide) après épuisement des
tentatives, elle retourne None — c'est à l'appelant de retomber sur un
texte par défaut plutôt que de bloquer toute la boucle d'envoi des leads.
"""

from __future__ import annotations

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

LLM_API_URL = (os.getenv("LLM_API_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

LLM_MODEL_MAIN = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
LLM_MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen2.5:3b")
LLM_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "90"))


def generer_texte(
    prompt: str,
    *,
    model: str | None = None,
    timeout: int | None = None,
    tentatives: int = 1,
    pause_retry_sec: float = 5.0,
) -> str | None:
    """Appelle l'API de génération de texte (route compatible Ollama
    /api/generate) à l'URL résolue par LLM_API_URL/OLLAMA_HOST ci-dessus.

    `tentatives` > 1 : utile pour absorber un premier appel lent dû au
    chargement à froid du modèle par Ollama (cf. lead_worker.py). Ne lève
    jamais : retourne None si toutes les tentatives échouent ou si la
    réponse est vide/invalide."""
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
    modele = model or LLM_MODEL_MAIN
    delai = timeout or LLM_TIMEOUT

    for tentative in range(1, tentatives + 1):
        derniere_tentative = tentative == tentatives
        try:
            reponse = requests.post(
                f"{LLM_API_URL}/api/generate",
                json={"model": modele, "prompt": prompt, "stream": False},
                headers=headers,
                timeout=delai,
            )
            reponse.raise_for_status()
        except requests.exceptions.Timeout:
            niveau = log.error if derniere_tentative else log.warning
            niveau(f"Timeout LLM ({LLM_API_URL}, {delai}s) — tentative {tentative}/{tentatives}")
            if derniere_tentative:
                return None
            time.sleep(pause_retry_sec)
            continue
        except requests.exceptions.RequestException as e:
            niveau = log.error if derniere_tentative else log.warning
            niveau(f"Échec appel LLM ({LLM_API_URL}) : {e} — tentative {tentative}/{tentatives}")
            if derniere_tentative:
                return None
            time.sleep(pause_retry_sec)
            continue

        try:
            texte = reponse.json().get("response", "").strip()
        except ValueError:
            log.error(f"Réponse LLM non-JSON ({LLM_API_URL})")
            return None

        return texte or None

    return None
