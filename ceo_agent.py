import logging
import os
import random
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from supabase import create_client

from alertes import CompteZohoBloqueError, alerter_blocage_compte_zoho, envoyer_smtp, est_blocage_compte_zoho
from email_blacklist import emails_blacklistes
from email_tracking import demarrer_tracking, verifier_budget_quotidien
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
            # Exclut les leads déjà flaggés invalides (email structurellement
            # invalide ou déjà blacklisté — voir lead_worker.py::inserer_lead)
            # : jamais renvoyés à une campagne, mais conservés en base.
            .neq("status", "invalide")
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
    """Envoi SMTP Zoho en texte brut. Si lead_id est fourni, journalise
    l'événement 'envoye' (voir email_tracking.py::demarrer_tracking) —
    nécessaire à verifier_budget_quotidien() pour compter les envois du
    jour, TOUS pipelines confondus (le pixel/liens trackés à des fins
    d'ouverture/clic, eux, ont été retirés : dépendaient du backend FastAPI
    supprimé).

    Lève CompteZohoBloqueError (au lieu de renvoyer False) si l'échec est un
    blocage du COMPTE Zoho lui-même (voir alertes.py) — à catcher dans la
    boucle appelante pour arrêter le run entier immédiatement, pas seulement
    ce destinataire : tous les envois suivants échoueraient de la même
    façon."""
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = ZOHO_USER
        msg["To"] = to_email
        msg["Subject"] = f"[{AGENCY_NAME}] {subject}"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        envoyer_smtp(msg, ZOHO_USER, to_email)
        if lead_id:
            demarrer_tracking("lead_artisan", lead_id)
        return True
    except Exception as e:
        log.error(f"Erreur envoi prospect vers {to_email} : {e}")
        if est_blocage_compte_zoho(e):
            alerter_blocage_compte_zoho(f"détecté lors d'un envoi vers {to_email}", e)
            raise CompteZohoBloqueError(str(e)) from e
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
        envoyer_smtp(msg, ZOHO_USER, CEO_EMAIL)
        log.info(f"Email envoyé à {CEO_EMAIL}")
        return True
    except Exception as e:
        log.error(f"Erreur email : {e}")
        return False


def run_ceo_analysis() -> None:
    """Campagne de prospection : envoie un email personnalisé à chaque lead
    non contacté (pitch RÉGÉNÉRÉ à chaque envoi via lead_worker.py::generer_pitch(),
    jamais lead["pitch_commercial"] réutilisé tel quel), puis journalise le
    rapport de campagne.

    Import différé de lead_worker (pas en tête de module) : lead_worker.py
    fait lui-même `from ceo_agent import send_email_prospect` — un import en
    tête de fichier ici créerait une boucle d'import qui échouerait au
    chargement (send_email_prospect n'existe pas encore dans ce module au
    moment où lead_worker essaierait de l'importer).

    Régénération ajoutée le 29/08/2026 suite à un incident constaté sur le
    backlog leads.pitch_commercial (264 leads jamais contactés, scrapés
    23-25/07/2026, avant le pivot de positionnement) : 95% de ces pitchs
    stockés en base parlaient encore de refonte de site web/SEO (offre
    précédente) et 87% contenaient un placeholder non rempli ("[Votre Nom]",
    signé "La Holding") — les envoyer tels quels aurait été à la fois
    hors-sujet et non professionnel. generer_pitch() reflète toujours le
    positionnement actuel et rejette désormais lui-même tout placeholder
    résiduel (voir RE_PLACEHOLDER_CROCHETS)."""
    start_time = time.time()
    log.info("Démarrage de la campagne de prospection")
    from lead_worker import generer_pitch  # import différé, voir docstring ci-dessus

    leads = get_leads_from_supabase()

    if not leads:
        log.warning("Aucun nouveau lead à contacter.")
        return

    emails_sent_count = 0
    total_processed = len(leads)

    # Budget quotidien PARTAGÉ avec le pipeline B2B (même boîte Zoho, même
    # réputation à protéger) — voir email_tracking.py::verifier_budget_quotidien.
    budget_restant = verifier_budget_quotidien()["budget_restant"]
    log.info(f"Budget d'envoi quotidien restant (partagé avec le B2B) : {budget_restant}")

    for lead in leads:
        company = lead.get("company") or "votre entreprise"
        target_email = lead.get("email")

        if budget_restant <= 0:
            log.warning("Plafond de warmup quotidien atteint (partagé avec le B2B) — campagne interrompue, reprise possible demain.")
            break

        # Filet de sécurité avant l'envoi (en plus du filtrage blacklist déjà
        # fait par get_leads_from_supabase, et de celui fait en amont par
        # email_enricher.py/lead_worker.py) : couvre un lead entré en base
        # avant ce correctif, ou par un autre chemin — voir email_validator.py.
        exploitable, raison = email_exploitable(target_email)
        if not exploitable:
            log.warning(f"Email écarté pour {company} ({raison}) : {target_email}")
            continue

        try:
            sujet = f"{company} — apport de clients qualifiés"
            corps = generer_pitch({
                "company_name": company,
                "industry": lead.get("industry") or "Bâtiment",
                "ville": lead.get("commune"),
                "score": lead.get("score"),
            })
            success = send_email_prospect(target_email, sujet, corps, lead_id=lead["id"])

            if success:
                emails_sent_count += 1
                budget_restant -= 1
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
                    # Écrase l'éventuel pitch_commercial obsolète stocké au
                    # scraping par le pitch RÉELLEMENT envoyé (voir docstring
                    # de run_ceo_analysis) — pour que toute relecture
                    # ultérieure de cette colonne reflète l'email reçu, pas
                    # un brouillon jamais envoyé.
                    "pitch_commercial": corps,
                }).eq("id", lead["id"]).execute()

                sleep_time = random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC)
                log.info(f"Pause de {sleep_time:.1f} secondes...")
                time.sleep(sleep_time)

        except CompteZohoBloqueError:
            # Déjà loggé + alerté dans send_email_prospect — inutile de
            # tenter les leads restants, ils échoueraient tous de la même
            # façon tant que le blocage n'est pas levé côté Zoho.
            log.error(f"Campagne interrompue après {emails_sent_count}/{total_processed} envois (compte Zoho bloqué).")
            break
        except Exception as e:
            log.error(f"Erreur lors du traitement de {company} : {e}")
            try:
                with open("erreurs_envoi.log", "a", encoding="utf-8") as f:
                    f.write(f"Échec envoi vers {target_email} : {e}\n")
            except OSError as e_log:
                # Une erreur disque/permission sur ce log ne doit pas, en
                # plus de l'échec déjà géré, faire planter toute la boucle.
                log.error(f"Impossible d'écrire dans erreurs_envoi.log : {e_log}")

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
