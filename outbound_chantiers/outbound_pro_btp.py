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

from alertes import CompteZohoBloqueError, alerter_blocage_compte_zoho, est_blocage_compte_zoho
from email_blacklist import emails_blacklistes
from email_tracking import demarrer_tracking, verifier_budget_quotidien
from email_validator import email_exploitable
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
# Anti-spam, alignée sur lead_worker.py/ceo_agent.py/relance_prospects.py
# (même compte Zoho, même limite) — portée à 45-90s suite à un premier
# blocage Zoho, puis à 60-120s après un second blocage malgré ce premier
# ajustement.
PAUSE_MIN_SECONDES = int(os.getenv("PAUSE_ENVOI_MIN_SEC", "60"))
PAUSE_MAX_SECONDES = int(os.getenv("PAUSE_ENVOI_MAX_SEC", "120"))


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
    """Envoi SMTP Zoho en texte brut. Si lead_id est fourni, journalise
    l'événement 'envoye' (voir email_tracking.py::demarrer_tracking) —
    nécessaire à statut_ramp_warmup() ci-dessous pour compter les envois du
    jour. Le pixel/liens trackés ont été retirés (dépendaient du backend
    FastAPI supprimé).

    Lève CompteZohoBloqueError (au lieu de renvoyer False) si l'échec est un
    blocage du COMPTE Zoho lui-même (voir alertes.py) — à catcher dans la
    boucle appelante pour arrêter le run entier immédiatement, pas
    seulement ce destinataire."""
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
            demarrer_tracking("lead_professionnel", lead_id, client_final=CLIENT_FINAL)

        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as serveur:
            serveur.login(ZOHO_USER, ZOHO_PASSWORD)
            serveur.sendmail(ZOHO_USER, [destinataire], message.as_string())
        return True
    except smtplib.SMTPException as e:
        log.error(f"Échec d'envoi à {destinataire} : {e}")
        if est_blocage_compte_zoho(e):
            alerter_blocage_compte_zoho(f"détecté lors d'un envoi B2B vers {destinataire}", e)
            raise CompteZohoBloqueError(str(e)) from e
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


def statut_ramp_warmup() -> dict:
    """Où en est la montée en charge progressive du domaine d'envoi
    (warmup) — voir email_tracking.py::verifier_budget_quotidien(), dont
    cette fonction n'est plus qu'un alias (conservé pour ne pas casser les
    appelants existants, ex: dashboard/data_access.py::get_warmup_status).
    Agrège maintenant TOUS les envois de prospection, B2B ET artisans/B2C
    confondus — la boîte d'envoi est partagée, sa réputation ne se découpe
    ni par campagne ni par pipeline (ancienne limitation : le B2C n'était
    pas compté alors qu'il partage la même boîte Zoho)."""
    return verifier_budget_quotidien()


def lancer_campagne_initiale(limite: int = 20) -> None:
    """Premier contact des acteurs jamais sollicités, par score décroissant.
    `limite` reste un plafond technique haut ; le plafond RÉEL du jour vient
    de statut_ramp_warmup() (montée en charge progressive du domaine
    d'envoi, voir config.py) et peut être bien plus bas tant que le domaine
    est jeune en prospection à froid."""
    ramp = statut_ramp_warmup()
    if ramp["budget_restant"] <= 0:
        log.info(
            f"Plafond de warmup atteint pour aujourd'hui (jour {ramp['jour_ramp']}, "
            f"{ramp['envoyes_aujourdhui']}/{ramp['plafond_jour']}) — aucun premier contact envoyé."
        )
        return

    limite_effective = min(limite, ramp["budget_restant"])
    acteurs = supabase_get(
        f"select=*&client_final=eq.{CLIENT_FINAL}&statut=eq.a_contacter&order=score_final.desc&limit={limite_effective}"
    )

    # Protection supplémentaire au statut lui-même : un email ayant fait
    # l'objet d'un hard bounce peut réapparaître sur une NOUVELLE ligne
    # (nouveau sourcing), statut='a_contacter' à nouveau — voir email_blacklist.py.
    blacklist = emails_blacklistes()
    if blacklist:
        avant = len(acteurs)
        acteurs = [a for a in acteurs if (a.get("email") or "").strip().lower() not in blacklist]
        if len(acteurs) < avant:
            log.info(f"{avant - len(acteurs)} acteur(s) exclu(s) (adresse blacklistée pour hard bounce précédent).")

    log.info(
        f"{len(acteurs)} acteur(s) à contacter pour la première fois "
        f"(warmup jour {ramp['jour_ramp']}, budget restant aujourd'hui : {ramp['budget_restant']})."
    )

    for acteur in acteurs:
        if not acteur.get("email"):
            continue
        # Filet de sécurité avant l'envoi (le filtrage blacklist a déjà eu
        # lieu ci-dessus) : couvre un acteur entré en base avant ce
        # correctif, ou dont l'email n'a pas transité par
        # enrichir_acteurs_pro.py::email_valide (voir email_validator.py).
        exploitable, raison = email_exploitable(acteur["email"])
        if not exploitable:
            log.warning(f"Premier contact annulé pour {acteur['nom_entreprise']} ({raison}) : {acteur['email']}")
            continue
        sujet, corps = message_premier_contact(acteur)
        try:
            envoi_reussi = envoyer_email(acteur["email"], sujet, corps, lead_id=acteur["id"])
        except CompteZohoBloqueError:
            # Déjà loggé + alerté dans envoyer_email — inutile de tenter les
            # acteurs restants, ils échoueraient tous de la même façon tant
            # que le blocage n'est pas levé côté Zoho.
            log.error("Campagne B2B interrompue (compte Zoho bloqué).")
            break
        if envoi_reussi:
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
    abandon définitif après MAX_RELANCES — cadence demandée : J+3 puis J+7.

    Partage le MÊME budget quotidien de warmup que lancer_campagne_initiale
    (voir statut_ramp_warmup) — recalculé ici en direct, donc reflète déjà
    ce que lancer_campagne_initiale a consommé plus tôt dans le même run
    (pipeline_outbound_chantiers.py appelle les deux en séquence)."""
    ramp = statut_ramp_warmup()
    if ramp["budget_restant"] <= 0:
        log.info(
            f"Plafond de warmup atteint pour aujourd'hui (jour {ramp['jour_ramp']}, "
            f"{ramp['envoyes_aujourdhui']}/{ramp['plafond_jour']}) — aucune relance envoyée."
        )
        return

    acteurs = supabase_get(f"select=*&client_final=eq.{CLIENT_FINAL}&statut=eq.contacte_attente_reponse")
    blacklist = emails_blacklistes()
    maintenant = datetime.now(timezone.utc)
    budget_restant = ramp["budget_restant"]

    for acteur in acteurs:
        if blacklist and (acteur.get("email") or "").strip().lower() in blacklist:
            continue

        # Voir la même vérification dans lancer_campagne_initiale ci-dessus.
        exploitable, raison = email_exploitable(acteur.get("email") or "")
        if not exploitable:
            log.warning(f"Relance annulée pour {acteur['nom_entreprise']} ({raison}) : {acteur.get('email')}")
            continue

        if budget_restant <= 0:
            log.info("Plafond de warmup atteint en cours de relances — arrêt, reprise au prochain run.")
            break

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
        try:
            envoi_reussi = envoyer_email(acteur["email"], sujet, corps, lead_id=acteur["id"])
        except CompteZohoBloqueError:
            # Voir la même gestion dans lancer_campagne_initiale ci-dessus.
            log.error("Relances B2B interrompues (compte Zoho bloqué).")
            break
        if envoi_reussi:
            supabase_patch(acteur["id"], {
                "relance_count": relance_count + 1,
                "last_relance_at": maintenant.isoformat(),
            })
            budget_restant -= 1
            log.info(f"Relance {relance_count + 1} envoyée : {acteur['nom_entreprise']}")
        time.sleep(random.uniform(PAUSE_MIN_SECONDES, PAUSE_MAX_SECONDES))


if __name__ == "__main__":
    lancer_campagne_initiale()
    lancer_relances()
