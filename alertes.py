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


class CompteZohoBloqueError(Exception):
    """Levée par les fonctions d'envoi (ceo_agent.py::send_email_prospect,
    outbound_pro_btp.py::envoyer_email) quand SMTP renvoie un blocage du
    COMPTE Zoho lui-même (ex: '550 5.4.6 Unusual sending activity detected'),
    PAS un échec ponctuel sur un seul destinataire (adresse inexistante,
    boîte pleine...). À catcher dans la boucle appelante pour arrêter le run
    immédiatement : une fois ce blocage déclenché, tous les envois suivants
    échoueront de la même façon jusqu'à ce qu'il soit levé côté Zoho —
    continuer à tenter les leads restants ne fait que perdre le temps de la
    pause anti-spam entre chacun, pour zéro chance de succès (cas vécu :
    un run de 172 leads qui aurait pu s'arrêter dès le 1er échec)."""


def est_blocage_compte_zoho(exception: Exception) -> bool:
    """Vrai si le texte de l'exception SMTP correspond à un blocage du
    compte Zoho lui-même plutôt qu'à un échec spécifique à un destinataire —
    voir CompteZohoBloqueError pour la distinction et pourquoi elle compte.
    Détection par motif texte (pas de code d'erreur structuré fiable
    disponible ici) : couvre le message exact rencontré en usage réel
    ("Unusual sending activity detected") et son code SMTP associé (5.4.6),
    en restant volontairement large plutôt que de coller au mot près à un
    seul message précis qui pourrait varier légèrement."""
    texte = str(exception).lower()
    return "unusual sending activity" in texte or "5.4.6" in texte


def alerter_blocage_compte_zoho(contexte: str, exception: Exception) -> None:
    """Alerte Discord dédiée à ce type de panne infra — jusqu'ici seuls les
    leads chauds déclenchaient une alerte Discord (mail_processor.py) ;
    un blocage du compte d'envoi lui-même est au moins aussi urgent (toute
    la prospection s'arrête tant qu'il n'est pas levé) mais ne remontait
    nulle part avant ce correctif, seulement visible en creusant les logs."""
    alerter_discord(
        f"🚫 **Compte Zoho bloqué** — {contexte}\n"
        f"Toute la prospection est à l'arrêt tant que ce blocage n'est pas levé "
        f"(lien « Unblock Me » dans l'erreur, ou attendre).\n```{exception}```"
    )


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
