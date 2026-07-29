"""
Alertes partagées entre les scripts du pipeline (ceo_agent.py,
mail_processor.py, relance_prospects.py, outbound_chantiers/, dashboard/) —
un seul module pour éviter de dupliquer le même appel webhook Discord dans
chaque script (scraper_batiment.py et mail_processor.py en avaient chacun
déjà une copie avant ce module).
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

import requests

log = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
CEO_EMAIL = os.getenv("CEO_EMAIL", "")


def alerter_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Échec envoi alerte Discord : {e}")


def envoyer_alerte_email(sujet: str, corps: str) -> None:
    """Notification e-mail directe, réservée aux événements rares et à forte
    valeur (ex: lead ultra-qualifié) — jamais aux ouvertures/clics (un e-mail
    par interaction serait ingérable, ceux-ci restent sur Discord + le
    dashboard uniquement). Silencieux si Zoho/CEO_EMAIL n'est pas configuré."""
    if not (ZOHO_USER and ZOHO_PASSWORD and CEO_EMAIL):
        return
    try:
        message = MIMEText(corps, "plain", "utf-8")
        message["Subject"] = sujet
        message["From"] = ZOHO_USER
        message["To"] = CEO_EMAIL
        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as serveur:
            serveur.login(ZOHO_USER, ZOHO_PASSWORD)
            serveur.sendmail(ZOHO_USER, CEO_EMAIL, message.as_string())
    except smtplib.SMTPException as e:
        log.error(f"Échec envoi alerte e-mail : {e}")
