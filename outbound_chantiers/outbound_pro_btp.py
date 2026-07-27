"""
MODULE 4 — Prospection multi-canal (e-mail) vers les acteurs professionnels.

Deux fonctions, appelées séparément par le planificateur (voir README /
méthodologie de mise en production) :
- lancer_campagne_initiale() : premier contact des acteurs jamais contactés,
  par score décroissant.
- lancer_relances() : relances automatiques à J+3 puis J+7, arrêt définitif
  après MAX_RELANCES (cohérent avec relance_prospects.py existant).

Le contenu de l'e-mail ne référence QUE l'activité professionnelle publique
du destinataire et le dynamisme de sa zone (signal agrégé) — jamais un
permis de construire individuel ni une information personnelle d'un tiers.
C'est une offre de partenariat B2B, pas une relance sur un projet précis
que le destinataire ne nous aurait pas confié.

Générique multi-secteurs : le pitch du premier contact est généré par IA
(Ollama, même mécanisme que lead_worker.py) à partir du SECTEUR et de la
DESCRIPTION_SERVICES de la campagne active — aucun texte lié au bâtiment
n'est plus codé en dur ici.
"""

import logging
import os
import random
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from email_tracking import demarrer_tracking, habiller_html_avec_tracking
from outbound_chantiers.config import (
    AGENCY_NAME,
    CLIENT_FINAL,
    DELAI_PREMIERE_RELANCE_JOURS,
    DELAI_RELANCE_SUIVANTE_JOURS,
    DESCRIPTION_SERVICES,
    LIBELLES_TYPE_ACTEUR,
    MAX_RELANCES,
    OLLAMA_HOST,
    OLLAMA_MODEL_MAIN,
    OLLAMA_TIMEOUT,
    SECTEUR,
    ZOHO_PASSWORD,
    ZOHO_USER,
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [OUTBOUND-PRO] %(message)s")
log = logging.getLogger(__name__)

TABLE = "leads_professionnels"
PAUSE_MIN_SECONDES, PAUSE_MAX_SECONDES = 20, 45  # anti-spam, identique à ceo_agent.py


def supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def supabase_get(params: str) -> list[dict]:
    try:
        reponse = requests.get(f"{SUPABASE_URL}/rest/v1/{TABLE}?{params}", headers=supabase_headers(), timeout=10)
        return reponse.json() if reponse.status_code == 200 else []
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur lecture Supabase : {e}")
        return []


def supabase_patch(acteur_id: str, changements: dict) -> None:
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/{TABLE}?id=eq.{acteur_id}",
            headers=supabase_headers(),
            json=changements,
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur mise à jour {acteur_id} : {e}")


def envoyer_email(destinataire: str, sujet: str, corps: str, lead_id: str | None = None) -> bool:
    """Envoi SMTP Zoho inchangé. Si lead_id est fourni, une version HTML
    trackée (pixel d'ouverture + liens redirigés, voir email_tracking.py) est
    jointe en plus du texte brut."""
    if not ZOHO_USER or not ZOHO_PASSWORD:
        log.error("Identifiants Zoho manquants — envoi annulé.")
        return False
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = sujet
        message["From"] = ZOHO_USER
        message["To"] = destinataire
        message.attach(MIMEText(corps, "plain", "utf-8"))

        if lead_id:
            tracking_id = demarrer_tracking("lead_professionnel", lead_id, client_final=CLIENT_FINAL)
            message.attach(MIMEText(habiller_html_avec_tracking(corps, tracking_id), "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as serveur:
            serveur.login(ZOHO_USER, ZOHO_PASSWORD)
            serveur.sendmail(ZOHO_USER, [destinataire], message.as_string())
        return True
    except smtplib.SMTPException as e:
        log.error(f"Échec d'envoi à {destinataire} : {e}")
        return False


def _libelle_type_acteur(cle: str) -> str:
    return LIBELLES_TYPE_ACTEUR.get(cle, cle.replace("_", " "))


def _pitch_generique_repli(acteur: dict, commune: str) -> str:
    """Modèle de secours si Ollama est indisponible — le pipeline ne doit
    jamais être bloqué par une panne de l'IA. Générique : ne dépend que des
    champs de la campagne (secteur, description), aucun texte sectoriel fixe."""
    libelle_type = _libelle_type_acteur(acteur["type_acteur"])
    return (
        f"Bonjour,\n\n"
        f"{CLIENT_FINAL} intervient dans le secteur « {SECTEUR} » et est actif sur "
        f"{commune} et ses environs.\n\n"
        f"Nous cherchons à nouer des partenariats durables avec des professionnels "
        f"du secteur ({libelle_type}) pour vos prochains projets.\n\n"
        f"Seriez-vous ouvert à un premier échange pour évaluer une collaboration ?\n\n"
        f"Cordialement,\n{CLIENT_FINAL}"
    )


def generer_pitch_ia(acteur: dict) -> str:
    """Génère le corps du premier e-mail via IA locale (Ollama, même
    mécanisme que lead_worker.py::generer_pitch), personnalisé par le
    secteur et la description des services de la campagne active — jamais
    de secteur ni de texte en dur ici. Retombe sur un modèle générique si
    Ollama est indisponible ou renvoie une réponse vide."""
    commune = acteur.get("commune") or "votre secteur"
    libelle_type = _libelle_type_acteur(acteur["type_acteur"])

    prompt = (
        f"Rédige un court e-mail de prospection B2B (120 mots maximum, en français) "
        f"au nom de l'entreprise « {CLIENT_FINAL} », qui opère dans le secteur "
        f"« {SECTEUR} ». Description de son activité : "
        f"{DESCRIPTION_SERVICES or 'non précisée'}.\n\n"
        f"Le destinataire est un(e) {libelle_type} basé(e) à {commune}. L'objectif "
        f"est de proposer un partenariat professionnel durable (le destinataire "
        f"pourrait recommander {CLIENT_FINAL} ou lui confier des projets futurs), "
        f"PAS de vendre un produit ponctuel. Ton direct et concret, pas de jargon "
        f"marketing. Ne mentionne AUCUN projet ou permis individuel du destinataire : "
        f"uniquement son activité professionnelle publique et sa zone géographique. "
        f"IMPORTANT : n'invente ni ne mentionne AUCUN nom de personne (pas de directeur, "
        f"responsable commercial ou signataire fictif) — l'e-mail est signé uniquement par "
        f"l'entreprise. Aucun placeholder entre crochets (pas de '[Votre nom]' etc.) — "
        f"termine simplement par 'Cordialement,' suivi de '{CLIENT_FINAL}', rien d'autre après."
    )

    try:
        reponse = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL_MAIN, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        reponse.raise_for_status()
        texte = reponse.json().get("response", "").strip()
        if texte:
            return texte
        log.warning(f"Réponse IA vide pour {acteur['nom_entreprise']} — repli sur le modèle générique.")
    except requests.exceptions.Timeout:
        log.error(f"Timeout Ollama ({OLLAMA_TIMEOUT}s) pour {acteur['nom_entreprise']} — repli sur le modèle générique.")
    except requests.exceptions.RequestException as e:
        log.error(f"Échec appel Ollama pour {acteur['nom_entreprise']} : {e} — repli sur le modèle générique.")

    return _pitch_generique_repli(acteur, commune)


def message_premier_contact(acteur: dict) -> tuple[str, str]:
    commune = acteur.get("commune") or "votre secteur"
    sujet = f"{CLIENT_FINAL} — partenariat professionnel sur {commune}"
    corps = generer_pitch_ia(acteur) + f"\n\n(message transmis pour le compte de {CLIENT_FINAL} par {AGENCY_NAME})"
    return sujet, corps


def message_relance(acteur: dict, numero_relance: int) -> tuple[str, str]:
    commune = acteur.get("commune") or "votre secteur"
    sujet = f"Re : {CLIENT_FINAL} — partenariat professionnel sur {commune}"
    if numero_relance == 1:
        corps = (
            f"Bonjour,\n\nJe me permets de revenir vers vous suite à mon message "
            f"précédent concernant une possible collaboration avec {CLIENT_FINAL} "
            f"sur {commune}. Si le sujet vous intéresse, même à titre exploratoire, "
            f"n'hésitez pas à me répondre.\n\nCordialement,\n{CLIENT_FINAL}"
        )
    else:
        corps = (
            f"Bonjour,\n\nDernier message de ma part à ce sujet : si une collaboration "
            f"avec {CLIENT_FINAL} sur vos projets à venir ({commune}) vous intéresse un "
            f"jour, n'hésitez pas à revenir vers nous. Je ne vous solliciterai plus "
            f"dans l'intervalle.\n\nCordialement,\n{CLIENT_FINAL}"
        )
    return sujet, corps


def lancer_campagne_initiale(limite: int = 20) -> None:
    """Premier contact des acteurs jamais sollicités, par score décroissant."""
    acteurs = supabase_get(
        f"select=*&client_final=eq.{CLIENT_FINAL}&statut=eq.a_contacter&order=score_final.desc&limit={limite}"
    )
    log.info(f"{len(acteurs)} acteur(s) à contacter pour la première fois.")

    for acteur in acteurs:
        if not acteur.get("email"):
            continue
        sujet, corps = message_premier_contact(acteur)
        if envoyer_email(acteur["email"], sujet, corps, lead_id=acteur["id"]):
            supabase_patch(acteur["id"], {
                "statut": "contacte_attente_reponse",
                "contacted": True,
                "contacted_at": datetime.now(timezone.utc).isoformat(),
            })
            log.info(f"Premier contact envoyé : {acteur['nom_entreprise']}")
        time.sleep(random.uniform(PAUSE_MIN_SECONDES, PAUSE_MAX_SECONDES))


def _parser_horodatage(valeur: str) -> datetime:
    """Parse un horodatage ISO renvoyé par Supabase/Postgres. Postgres tronque
    parfois les zéros de fin des microsecondes (ex: '.88185' au lieu de
    '.881850'), ce que le parseur strict de Python 3.10
    (datetime.fromisoformat, qui n'accepte que 0, 3 ou 6 chiffres) rejette
    avec "Invalid isoformat string" — incident réel : a fait échouer
    lancer_relances() juste après un envoi initial réussi. On normalise la
    précision à 6 chiffres avant de parser."""
    valeur = valeur.replace("Z", "+00:00")
    valeur = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], valeur)
    return datetime.fromisoformat(valeur)


def lancer_relances() -> None:
    """Relance à J+DELAI_PREMIERE_RELANCE_JOURS puis J+DELAI_RELANCE_SUIVANTE_JOURS,
    abandon définitif après MAX_RELANCES — cadence demandée : J+3 puis J+7."""
    acteurs = supabase_get(f"select=*&client_final=eq.{CLIENT_FINAL}&statut=eq.contacte_attente_reponse")
    maintenant = datetime.now(timezone.utc)

    for acteur in acteurs:
        relance_count = acteur.get("relance_count") or 0
        if relance_count >= MAX_RELANCES:
            supabase_patch(acteur["id"], {"statut": "sans_reponse"})
            continue

        reference = acteur.get("last_relance_at") or acteur.get("contacted_at")
        if not reference:
            continue
        reference_dt = _parser_horodatage(reference)
        delai_requis = DELAI_PREMIERE_RELANCE_JOURS if relance_count == 0 else DELAI_RELANCE_SUIVANTE_JOURS

        if maintenant - reference_dt < timedelta(days=delai_requis):
            continue

        sujet, corps = message_relance(acteur, relance_count + 1)
        if envoyer_email(acteur["email"], sujet, corps, lead_id=acteur["id"]):
            supabase_patch(acteur["id"], {
                "relance_count": relance_count + 1,
                "last_relance_at": maintenant.isoformat(),
            })
            log.info(f"Relance {relance_count + 1} envoyée : {acteur['nom_entreprise']}")
        time.sleep(random.uniform(PAUSE_MIN_SECONDES, PAUSE_MAX_SECONDES))


if __name__ == "__main__":
    lancer_campagne_initiale()
    lancer_relances()
