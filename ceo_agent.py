import logging
import os
import random
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
API_URL = os.getenv("API_URL", "http://localhost:8000")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
CEO_EMAIL = os.getenv("CEO_EMAIL", "")
AGENCY_NAME = os.getenv("AGENCY_NAME", "AI Company")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger(__name__)


def get_stats() -> dict:
    try:
        res = requests.get(f"{API_URL}/stats", timeout=10)
        return res.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur stats : {e}")
        return {}


def get_leads_from_supabase() -> list:
    """Récupère uniquement les leads qui n'ont pas encore été contactés."""
    try:
        response = supabase.table("leads").select("*").eq("contacted", False).execute()
        return response.data
    except Exception as e:
        log.error(f"Erreur lors de la récupération des leads depuis Supabase : {e}")
        return []


def analyze_with_ollama(stats: dict, leads: list) -> str:
    """Génère un rapport d'opportunités marché (une section par secteur)."""
    secteurs = list(set(lead["industry"] for lead in leads))
    full_report = "### RAPPORT D'OPPORTUNITÉS (MARKET SCOUT)\n\n"

    for secteur in secteurs:
        full_report += f"\n--- SECTEUR : {secteur} ---\n"
        leads_secteur = [l for l in leads if l["industry"] == secteur]

        prompt = f"""
        Agis comme un consultant expert en stratégie d'entreprise.
        Voici des entreprises du secteur {secteur} avec leurs faiblesses identifiées :
        {str(leads_secteur)}

        Pour chaque entreprise, suggère une solution technologique simple, automatisable et rentable.
        Sois concis, factuel et pragmatique.
        """

        try:
            res = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=90,
            )
            res.raise_for_status()
            reponse = res.json().get("response", "Pas de réponse.")
        except requests.exceptions.RequestException as e:
            log.error(f"Erreur Ollama pour le secteur {secteur} : {e}")
            reponse = "Analyse indisponible (échec de l'appel IA)."

        full_report += reponse + "\n"

    return full_report


def save_report_to_supabase(rapport: str, stats: dict) -> bool:
    try:
        data = {
            "date": datetime.now(timezone.utc).isoformat(),
            "content": rapport,
            "stats": stats,
        }
        supabase.table("ceo_reports").insert(data).execute()
        log.info("Rapport de campagne enregistré dans Supabase.")
        return True
    except Exception as e:
        log.error(f"Erreur lors de l'enregistrement du rapport : {e}")
        return False


def send_email_prospect(to_email: str, subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg["From"] = ZOHO_USER
        msg["To"] = to_email
        msg["Subject"] = f"[{AGENCY_NAME}] {subject}"
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as s:
            s.login(ZOHO_USER, ZOHO_PASSWORD)
            s.sendmail(ZOHO_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        log.error(f"Erreur envoi prospect vers {to_email} : {e}")
        return False


def send_email_internal(rapport: str, stats: dict) -> bool:
    if not ZOHO_USER or not ZOHO_PASSWORD or not CEO_EMAIL:
        return False
    try:
        semaine = datetime.now().strftime("Semaine %V - %d/%m/%Y")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{AGENCY_NAME}] Rapport CEO - {semaine}"
        msg["From"] = ZOHO_USER
        msg["To"] = CEO_EMAIL
        html = (
            f"<html><body><h2>Rapport CEO Agent</h2><h3>{semaine}</h3>"
            f"<p>Leads: {stats.get('leads_total', 0)} | MRR: {stats.get('mrr_estime', 0)}€</p>"
            f"<pre>{rapport}</pre></body></html>"
        )
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as s:
            s.login(ZOHO_USER, ZOHO_PASSWORD)
            s.sendmail(ZOHO_USER, CEO_EMAIL, msg.as_string())
        log.info(f"Email envoyé à {CEO_EMAIL}")
        return True
    except Exception as e:
        log.error(f"Erreur email : {e}")
        return False


def run_ceo_analysis() -> None:
    """Campagne de prospection : envoie un email personnalisé à chaque lead
    non contacté (en priorité le pitch généré par lead_worker.py via Ollama,
    à défaut un message générique), puis journalise le rapport de campagne."""
    start_time = time.time()
    log.info("Démarrage de la campagne de prospection")
    leads = get_leads_from_supabase()

    if not leads:
        log.warning("Aucun nouveau lead à contacter.")
        return

    emails_sent_count = 0
    total_processed = len(leads)

    for lead in leads:
        company = lead.get("company") or "votre entreprise"
        target_email = lead.get("email")

        if not target_email or "@" not in target_email or "." not in target_email:
            log.warning(f"Email invalide ignoré pour {company} : {target_email}")
            continue

        try:
            sujet = f"Une idée pour booster la visibilité de {company}"
            corps = lead.get("pitch_commercial") or (
                f"Bonjour, nous avons analysé {company} et avons quelques idées "
                f"pour améliorer votre présence en ligne."
            )
            success = send_email_prospect(target_email, sujet, corps)

            if success:
                emails_sent_count += 1
                log.info(f"Email envoyé avec succès à {company}")

                supabase.table("leads").update({"contacted": True}).eq("id", lead["id"]).execute()

                sleep_time = random.uniform(20, 45)
                log.info(f"Pause de {sleep_time:.1f} secondes...")
                time.sleep(sleep_time)

        except Exception as e:
            log.error(f"Erreur lors du traitement de {company} : {e}")
            with open("erreurs_envoi.log", "a", encoding="utf-8") as f:
                f.write(f"Échec envoi vers {target_email} : {e}\n")

    duration = round((time.time() - start_time) / 60, 2)
    stats = {
        "leads_processed": total_processed,
        "emails_sent": emails_sent_count,
        "duration_minutes": duration,
    }
    rapport = (
        f"Campagne terminée : {emails_sent_count} emails envoyés sur "
        f"{total_processed} leads en {duration} minutes."
    )
    save_report_to_supabase(rapport, stats)


if __name__ == "__main__":
    run_ceo_analysis()
