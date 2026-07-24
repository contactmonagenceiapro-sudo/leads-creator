import imaplib
import email
from email.header import decode_header
import os
import logging
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

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CRM] %(message)s")
log = logging.getLogger(__name__)

def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL non configuré dans .env, alerte ignorée.")
        return
    try:
        data = {"content": f"🚨 **Alerte Lead Chaud !**\n{message}"}
        requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur lors de l'envoi de l'alerte Discord : {e}")

def update_lead_status(email_address: str, new_status: str):
    try:
        clean_email = email_address.split("<")[1].split(">")[0].strip() if "<" in email_address else email_address.strip()
        response = supabase.table('leads').update({'status': new_status}).eq('email', clean_email).execute()
        if response.data:
            log.info(f"🔥 Lead {clean_email} mis à jour avec le statut : '{new_status}'")
        else:
            log.warning(f"Lead non trouvé dans la table pour l'email : {clean_email}")
    except Exception as e:
        log.error(f"Erreur Supabase : {e}")

def check_for_replies():
    if not ZOHO_USER or not ZOHO_PASSWORD:
        log.error("Identifiants Zoho manquants dans .env")
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.zoho.eu", 993)
        mail.login(ZOHO_USER, ZOHO_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, 'UNSEEN')
        email_ids = messages[0].split()

        if not email_ids:
            log.info("📬 Aucun nouveau message non lu.")
            mail.logout()
            return

        positive_keywords = ["intéressé", "interesse", "disponible", "appel", "rendez-vous", "rdv", "tarifs", "devis", "échange", "suite"]

        for num in email_ids:
            _, msg_data = mail.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            sender = msg.get("From")

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors='ignore')
            else:
                body = msg.get_payload(decode=True).decode(errors='ignore')

            if any(keyword in body.lower() for keyword in positive_keywords):
                log.info(f"🎯 Réponse positive détectée de : {sender}")
                update_lead_status(sender, "interested")
                send_discord_alert(f"Le prospect {sender} est intéressé par vos services !")
            else:
                log.info(f"Message de {sender} reçu.")

        mail.logout()
    except Exception as e:
        log.error(f"Erreur lors du scan : {e}")

if __name__ == "__main__":
    check_for_replies()
