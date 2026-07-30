import logging
import os
import random
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from supabase import create_client

from email_blacklist import emails_blacklistes
from email_validator import email_exploitable

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
CEO_EMAIL = os.getenv("CEO_EMAIL", "")
AGENCY_NAME = os.getenv("AGENCY_NAME", "AI Company")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger(__name__)

# Pause anti-spam entre deux ENVOIS RÉELS (alignée sur lead_worker.py,
# relance_prospects.py et outbound_pro_btp.py — tous partagent le même
# compte Zoho, donc la même limite anti-spam). Portée à 45-90s suite à un
# premier blocage Zoho ("Unusual sending activity detected"), puis à
# 60-120s après un second blocage malgré ce premier ajustement — configurable
# sans toucher au code si besoin de réajuster.
PAUSE_MIN_SEC = int(os.getenv("PAUSE_ENVOI_MIN_SEC", "60"))
PAUSE_MAX_SEC = int(os.getenv("PAUSE_ENVOI_MAX_SEC", "120"))


SOURCES_EMAIL_VERIFIEES = ("email_verifie_site", "domaine_verifie_sans_email")


def get_leads_from_supabase() -> list:
    """Récupère les leads non contactés dont l'email a été vérifié (domaine
    réel confirmé par scraper_batiment.py/email_enricher.py), via le
    marqueur posé dans `notes`. Exclut volontairement les leads dont l'email
    n'est qu'une génération structurelle non vérifiée
    ("aucun_domaine_trouve") : envoyer une campagne à grande échelle sur des
    adresses non confirmées ferait remonter le taux de rebond et dégraderait
    la réputation d'envoi (cf. incident NXDOMAIN corrigé précédemment).

    NOTE : ce filtre remplace celui, plus permissif, historiquement utilisé
    ici (contacted=False seul) — fusionné avec la logique auparavant dans le
    script séparé envoyer_campagne_verifiee.py, retiré car il faisait double
    emploi avec ce module."""
    try:
        filtre_or = ",".join(f"notes.ilike.*{s}*" for s in SOURCES_EMAIL_VERIFIEES)
        # NOTE : .or_() de postgrest-py ajoute lui-même les parenthèses
        # englobantes ; ne pas les dupliquer ici (sinon PGRST100 "failed to
        # parse logic tree").
        response = (
            supabase.table("leads")
            .select("*")
            .eq("contacted", False)
            .or_(filtre_or)
            .execute()
        )
        leads = response.data

        # Protection supplémentaire au statut du lead lui-même (qui suffit
        # déjà pour un lead déjà contacté) : un email ayant fait l'objet
        # d'un hard bounce peut réapparaître sur une NOUVELLE ligne lead
        # après un re-scraping (nouvel id, contacted=False) — voir
        # email_blacklist.py.
        blacklist = emails_blacklistes()
        if blacklist:
            avant = len(leads)
            leads = [l for l in leads if (l.get("email") or "").strip().lower() not in blacklist]
            if len(leads) < avant:
                log.info(f"{avant - len(leads)} lead(s) exclu(s) (adresse blacklistée pour hard bounce précédent).")

        return leads
    except Exception as e:
        log.error(f"Erreur lors de la récupération des leads depuis Supabase : {e}")
        return []


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


def send_email_prospect(to_email: str, subject: str, body: str, lead_id: str | None = None) -> bool:
    """Envoi SMTP Zoho en texte brut. `lead_id` n'est plus utilisé pour du
    tracking (pixel/liens redirigés retirés — dépendaient du backend FastAPI
    supprimé) ; conservé dans la signature pour ne pas casser les appelants."""
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = ZOHO_USER
        msg["To"] = to_email
        msg["Subject"] = f"[{AGENCY_NAME}] {subject}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

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

        # Filet de sécurité avant l'envoi (en plus du filtrage blacklist déjà
        # fait par get_leads_from_supabase, et de celui fait en amont par
        # email_enricher.py/lead_worker.py) : couvre un lead entré en base
        # avant ce correctif, ou par un autre chemin — voir email_validator.py.
        exploitable, raison = email_exploitable(target_email)
        if not exploitable:
            log.warning(f"Email écarté pour {company} ({raison}) : {target_email}")
            continue

        try:
            sujet = f"Une idée pour booster la visibilité de {company}"
            corps = lead.get("pitch_commercial") or (
                f"Bonjour, nous avons analysé {company} et avons quelques idées "
                f"pour améliorer votre présence en ligne."
            )
            success = send_email_prospect(target_email, sujet, corps, lead_id=lead["id"])

            if success:
                emails_sent_count += 1
                log.info(f"Email envoyé avec succès à {company}")

                # "status" doit refléter le même événement que "contacted" :
                # avant ce correctif, seul "contacted" était mis à jour ici,
                # ce qui désynchronisait les deux champs (cas réel observé :
                # 74 leads contacted=True mais 8 seulement en status
                # 'contacted') et empêchait tout reporting/relance fiable.
                # "contacted_at" alimente le mécanisme de relance
                # (relance_prospects.py).
                supabase.table("leads").update({
                    "contacted": True,
                    "status": "contacte_attente_reponse",
                    "contacted_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", lead["id"]).execute()

                sleep_time = random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC)
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
