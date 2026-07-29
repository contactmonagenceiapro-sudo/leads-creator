"""Alerte Discord partagée (leads chauds, demandes de devis entrantes)."""

import logging
import os

import requests

log = logging.getLogger(__name__)


def alerter_discord(message: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Échec envoi alerte Discord : {e}")
