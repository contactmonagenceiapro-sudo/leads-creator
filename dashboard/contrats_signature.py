"""
Génération de devis PDF + envoi en signature électronique (Yousign) + lien de
paiement (Stripe) — reprend telle quelle la logique métier de api/main.py
(avant suppression du backend FastAPI), rendue SYNCHRONE (plus de
BackgroundTasks : ces appels sont maintenant faits directement dans le
handler du formulaire d'intake public, avec un st.spinner autour, ou depuis
un bouton admin après vérification manuelle — voir dashboard/gestion_clients.py).

Confirmation manuelle (webhooks supprimés) :
- signature Yousign : l'admin vérifie lui-même dans Yousign, puis appelle
  data_access.marquer_contrat_signe() puis creer_et_envoyer_lien_paiement()
  ci-dessous (au lieu de webhook_yousign -> creer_et_envoyer_lien_paiement).
- paiement Stripe : l'admin vérifie lui-même dans Stripe, puis appelle
  data_access.marquer_contrat_paye() (au lieu de webhook_stripe).
"""

import logging
import os
import re

import requests
import stripe
from fpdf import FPDF

from supabase_client import supabase

log = logging.getLogger(__name__)

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")
YOUSIGN_API_KEY = os.getenv("YOUSIGN_API_KEY", "")
YOUSIGN_API_URL = os.getenv("YOUSIGN_API_URL", "https://api-sandbox.yousign.app/v3")
CONTRACT_AMOUNT_EUR = float(os.getenv("CONTRACT_AMOUNT_EUR", "990"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


def generer_pdf_devis(lead: dict, intake: dict) -> bytes:
    """Génère un devis PDF minimal à partir des infos du lead et de l'intake,
    pour transmission en signature électronique (aucune saisie manuelle)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Devis - {AGENCY_NAME}", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(6)
    pdf.multi_cell(0, 7,
        f"Client : {lead.get('company', '')}\n"
        f"Description du projet : {intake.get('description', '')}\n\n"
        f"Prestation : Création de site vitrine + optimisation SEO local (Done For You)\n"
        f"Montant : {CONTRACT_AMOUNT_EUR:.2f} EUR TTC\n\n"
        f"En signant électroniquement ce document, le client accepte les "
        f"conditions ci-dessus et le lancement de la prestation."
    )
    return bytes(pdf.output(dest="S"))


def yousign_headers() -> dict:
    return {"Authorization": f"Bearer {YOUSIGN_API_KEY}"}


def nettoyer_nom_yousign(nom: str, repli: str = "Client") -> str:
    """Yousign rejette les prénoms/noms contenant certains caractères (ex:
    underscore) avec l'erreur 'This value has unauthorized chars.' — cas réel
    rencontré avec une raison sociale brute utilisée telle quelle comme
    prénom. Ne garde que lettres/espaces/tirets/apostrophes, jamais vide."""
    nettoye = re.sub(r"[^a-zA-ZÀ-ÿ\s'-]", " ", nom).strip()
    nettoye = re.sub(r"\s+", " ", nettoye)
    return nettoye or repli


def envoyer_contrat_signature(lead: dict, intake: dict) -> bool:
    """Génère le devis et l'envoie en signature électronique via Yousign.
    Yousign se charge lui-même de l'email de signature envoyé au client.
    Renvoie False (et journalise l'étape en échec) sans lever d'exception :
    appelée depuis un formulaire public, elle ne doit jamais faire planter
    la page de confirmation affichée au prospect."""
    lead_id = lead["id"]
    pdf_bytes = generer_pdf_devis(lead, intake)

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests",
        headers=yousign_headers(),
        json={"name": f"Devis {lead.get('company', lead_id)}", "delivery_mode": "email"},
        timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign création demande échouée pour {lead_id} : {r.status_code} {r.text}")
        return False
    signature_request_id = r.json()["id"]

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/documents",
        headers=yousign_headers(),
        files={"file": (f"devis_{lead_id}.pdf", pdf_bytes, "application/pdf")},
        data={"nature": "signable_document"},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign upload document échoué pour {lead_id} : {r.status_code} {r.text}")
        return False
    document_id = r.json()["id"]

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/signers",
        headers=yousign_headers(),
        json={
            "info": {
                "first_name": nettoyer_nom_yousign(lead.get("name") or lead.get("company") or "Client"),
                "last_name": "Client",
                "email": lead["email"],
                "locale": "fr",
            },
            "signature_level": "electronic_signature",
            "signature_authentication_mode": "no_otp",
            "fields": [{"document_id": document_id, "type": "signature", "page": 1, "x": 100, "y": 700}],
        },
        timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign ajout signataire échoué pour {lead_id} : {r.status_code} {r.text}")
        return False

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/activate",
        headers=yousign_headers(), timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign activation échouée pour {lead_id} : {r.status_code} {r.text}")
        return False

    supabase.table("contracts").insert({
        "lead_id": lead_id,
        "yousign_request_id": signature_request_id,
        "yousign_status": "envoye",
        "montant_centimes": int(CONTRACT_AMOUNT_EUR * 100),
    }).execute()
    supabase.table("leads").update({"status": "contrat_envoye"}).eq("id", lead_id).execute()
    log.info(f"Contrat envoyé en signature à {lead['email']} (Yousign id={signature_request_id})")
    return True


def creer_et_envoyer_lien_paiement(lead_id: str, contract_id: str) -> bool:
    """Dès le contrat marqué signé (vérification manuelle admin, voir
    data_access.marquer_contrat_signe), génère un lien de paiement Stripe et
    l'envoie par email à l'artisan."""
    from ceo_agent import send_email_prospect  # import différé : évite un cycle au chargement du module

    leads = supabase.table("leads").select("*").eq("id", lead_id).execute().data
    contrats = supabase.table("contracts").select("*").eq("id", contract_id).execute().data
    if not leads or not contrats:
        log.error(f"Lien de paiement impossible : lead ou contrat introuvable ({lead_id})")
        return False
    lead = leads[0]
    contrat = contrats[0]

    try:
        prix = stripe.Price.create(
            unit_amount=contrat["montant_centimes"],
            currency="eur",
            product_data={"name": f"Prestation Done For You — {lead.get('company', '')}"},
        )
        lien = stripe.PaymentLink.create(
            line_items=[{"price": prix.id, "quantity": 1}],
            metadata={"lead_id": lead_id, "contract_id": contract_id},
        )
    except stripe.error.StripeError as e:
        log.error(f"Erreur création lien de paiement Stripe pour {lead_id} : {e}")
        return False

    supabase.table("contracts").update(
        {"stripe_payment_link_id": lien.id, "stripe_payment_url": lien.url}
    ).eq("id", contract_id).execute()
    supabase.table("leads").update({"status": "lien_paiement_envoye"}).eq("id", lead_id).execute()

    corps = (
        f"Bonjour,\n\nVotre contrat est bien signé, merci !\n\n"
        f"Pour lancer la production, voici votre lien de paiement sécurisé :\n{lien.url}\n\n"
        f"Dès le paiement reçu, on démarre immédiatement.\n\nCordialement"
    )
    if send_email_prospect(
        lead["email"], f"Contrat signé — lien de paiement {lead.get('company', '')}", corps, lead_id=lead_id
    ):
        log.info(f"Lien de paiement envoyé à {lead['email']}")
        return True
    log.error(f"Échec envoi du lien de paiement à {lead['email']}")
    return False
