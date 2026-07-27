import email
import imaplib
import logging
import os
import re
import unicodedata

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# URL publique (domaine réel, pas localhost/docker) sous laquelle l'API est
# joignable par un artisan externe. Tant qu'aucun domaine public n'est
# configuré, les liens envoyés dans l'email de suivi ne seront PAS
# accessibles depuis l'extérieur — voir avertissement dans envoyer_suivi_positif().
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", os.getenv("API_URL", "http://127.0.0.1:8000"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CRM] %(message)s")
log = logging.getLogger(__name__)

# Mots-clés de réponse POSITIVE. Volontairement larges : un simple "ok" ou
# "oui" doit être détecté (c'est l'exemple même donné pour ce pipeline), pas
# seulement des phrases explicites comme "je suis intéressé".
MOTS_POSITIFS = [
    "interesse", "disponible", "rendez-vous", "rdv", "tarif", "devis",
    "echange", "suite", "ok", "oui", "d'accord", "daccord", "banco",
    "ca marche", "ça marche", "allez-y", "vas-y", "partant", "yes",
    "go pour", "je veux bien", "avec plaisir", "pourquoi pas",
]

# Mots-clés de réponse NÉGATIVE / désinscription : à vérifier EN PREMIER
# (priorité sur les positifs) pour ne jamais recontacter quelqu'un qui a
# explicitement décliné ou demandé à être retiré.
MOTS_NEGATIFS = [
    "pas interesse", "pas intéressé", "non merci", "sans interet",
    "desinscri", "désinscri", "stop", "ne plus recevoir", "ne plus me contacter",
    "ne pas me contacter", "retirer de votre liste", "supprimer mes coordonnees",
    "supprimer mes coordonnées",
]


def normaliser(texte: str) -> str:
    """Minuscules + suppression des accents, pour une détection robuste aux
    variantes orthographiques ('intéressé' vs 'interesse')."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def contient_un_mot(texte_normalise: str, mots: list[str]) -> str | None:
    """Recherche en début de mot (limite de mot au début seulement, pas à la
    fin) : évite qu'un mot court comme 'ok' matche à l'intérieur d'un mot plus
    long sans rapport (ex: 'albat' dans 'albatros'), tout en acceptant les
    variantes conjuguées/dérivées d'un même radical (ex: 'desinscri' doit
    matcher 'désinscrire', 'désinscription', 'désinscrivez'...)."""
    for mot in mots:
        motif = r"\b" + re.escape(normaliser(mot))
        if re.search(motif, texte_normalise):
            return mot
    return None


def send_discord_alert(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL non configuré dans .env, alerte ignorée.")
        return
    try:
        data = {"content": f"🚨 **Alerte Lead Chaud !**\n{message}"}
        requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur lors de l'envoi de l'alerte Discord : {e}")


def extraire_email(champ_from: str) -> str:
    return champ_from.split("<")[1].split(">")[0].strip() if "<" in champ_from else champ_from.strip()


def recuperer_lead_par_email(email_address: str) -> dict | None:
    try:
        reponse = supabase.table("leads").select("*").eq("email", email_address).limit(1).execute()
        return reponse.data[0] if reponse.data else None
    except Exception as e:
        log.error(f"Erreur Supabase lors de la récupération du lead {email_address} : {e}")
        return None


def update_lead_status(email_address: str, new_status: str) -> None:
    try:
        response = supabase.table("leads").update({"status": new_status}).eq("email", email_address).execute()
        if response.data:
            log.info(f"🔥 Lead {email_address} mis à jour avec le statut : '{new_status}'")
        else:
            log.warning(f"Lead non trouvé dans la table pour l'email : {email_address}")
    except Exception as e:
        log.error(f"Erreur Supabase : {e}")


def update_lead_professionnel_status(email_address: str, new_status: str) -> None:
    """Même mécanisme que update_lead_status, mais pour la table
    leads_professionnels du département outbound_chantiers (acteurs pro :
    architectes, promoteurs, maîtres d'œuvre). Un désabonnement doit
    s'appliquer aux DEUX pipelines de prospection, pas seulement à celui des
    artisans — sans quoi le mot-clé 'stop' d'un architecte serait ignoré."""
    try:
        response = (
            supabase.table("leads_professionnels")
            .update({"statut": new_status})
            .eq("email", email_address)
            .execute()
        )
        if response.data:
            log.info(f"🔥 Acteur pro {email_address} mis à jour avec le statut : '{new_status}'")
    except Exception as e:
        log.error(f"Erreur Supabase (leads_professionnels) : {e}")


def envoyer_suivi_positif(lead: dict) -> None:
    """Envoie automatiquement (sans intervention manuelle) la présentation
    asynchrone + le lien du formulaire d'intake dès qu'une réponse positive
    est détectée — remplace l'étape jusqu'ici manuelle ("je lui balance...").

    ATTENTION : PUBLIC_APP_URL doit pointer vers un domaine réellement
    accessible depuis l'extérieur (pas http://127.0.0.1 ni un nom docker
    interne) pour que l'artisan puisse cliquer les liens depuis son email."""
    from ceo_agent import send_email_prospect  # import différé : évite un cycle au chargement du module

    lead_id = lead.get("id")
    company = lead.get("company") or "votre entreprise"
    to_email = lead.get("email")

    if not lead_id or not to_email:
        log.warning(f"Impossible d'envoyer le suivi automatique pour {company} : lead_id ou email manquant")
        return

    lien_presentation = f"{PUBLIC_APP_URL}/presentation/{lead_id}"
    lien_intake = f"{PUBLIC_APP_URL}/intake/{lead_id}"

    corps = (
        f"Bonjour,\n\n"
        f"Merci pour votre retour ! Voici la présentation détaillée de notre offre "
        f"clé en main pour {company} :\n{lien_presentation}\n\n"
        f"Pour qu'on puisse démarrer, merci de remplir ce court formulaire "
        f"(2 minutes, aucun appel nécessaire) :\n{lien_intake}\n\n"
        f"Dès réception, on prépare tout et on revient vers vous par email.\n\n"
        f"Cordialement"
    )

    succes = send_email_prospect(
        to_email, f"Votre projet {company} — présentation et prochaine étape", corps, lead_id=lead_id
    )
    if succes:
        log.info(f"Suivi automatique (présentation + intake) envoyé à {company} <{to_email}>")
        update_lead_status(to_email, "presentation_envoyee")
    else:
        log.error(f"Échec de l'envoi du suivi automatique à {company} <{to_email}>")


def check_for_replies() -> None:
    if not ZOHO_USER or not ZOHO_PASSWORD:
        log.error("Identifiants Zoho manquants dans .env")
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.zoho.eu", 993)
        mail.login(ZOHO_USER, ZOHO_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        email_ids = messages[0].split()

        if not email_ids:
            log.info("📬 Aucun nouveau message non lu.")
            mail.logout()
            return

        for num in email_ids:
            _, msg_data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender_brut = msg.get("From") or ""
            sender = extraire_email(sender_brut)

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            texte_normalise = normaliser(body)

            mot_negatif = contient_un_mot(texte_normalise, MOTS_NEGATIFS)
            if mot_negatif:
                log.info(f"🛑 Refus/désinscription détecté de {sender} (mot-clé : {mot_negatif!r})")
                update_lead_status(sender, "decline")
                update_lead_professionnel_status(sender, "decline")
                continue

            mot_positif = contient_un_mot(texte_normalise, MOTS_POSITIFS)
            if mot_positif:
                log.info(f"🎯 Réponse positive détectée de {sender} (mot-clé : {mot_positif!r})")
                update_lead_status(sender, "interested")
                update_lead_professionnel_status(sender, "interested")
                send_discord_alert(f"Le prospect {sender} est intéressé par vos services !")

                lead = recuperer_lead_par_email(sender)
                if lead:
                    envoyer_suivi_positif(lead)
                else:
                    log.warning(f"Lead introuvable en base pour {sender}, suivi automatique non envoyé")
            else:
                log.info(f"Message de {sender} reçu (aucun mot-clé détecté).")

        mail.logout()
    except Exception as e:
        log.error(f"Erreur lors du scan : {e}")


if __name__ == "__main__":
    check_for_replies()
