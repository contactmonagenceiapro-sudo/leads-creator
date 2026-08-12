"""
Signature électronique interne (simple, article 1367 du Code civil) —
alternative à Yousign (dashboard/contrats_signature.py, conservé intact et
réactivable via SIGNATURE_PROVIDER_PAR_DEFAUT=yousign) pendant la phase sans
budget et avec un compte Yousign en sandbox non valable légalement. Voir
diagnostic + plan validés le 10/08.

Limites documentées à cette date (à relire avant d'étendre l'usage) :
suffisant pour un contrat de faible valeur/faible risque de litige (le
client corrobore ensuite l'accord par email/échange), mais ne fournit ni
attestation d'un tiers de confiance indépendant, ni vérification d'identité,
ni scellement cryptographique du document, ni horodatage qualifié — contrairement
à Yousign. Repasser sur Yousign dès que le montant ou le risque de contestation
augmente sensiblement.

Flux :
    envoyer_contrat_signature_interne(lead, intake)  -> crée le contrat, envoie le lien
    enregistrer_signature(contrat, nom_saisi)         -> capture le consentement + preuve,
                                                          fait passer contrat/lead à "signé",
                                                          envoie le récapitulatif par email
"""

import base64
import hashlib
import logging
import os
import secrets
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import streamlit as st
from fpdf import FPDF

from alertes import alerter_blocage_compte_zoho, est_blocage_compte_zoho
from contrats_signature import CONTRACT_AMOUNT_EUR, generer_pdf_devis
from generation_contrats import COULEUR_ACCENT, COULEUR_GRIS, COULEUR_PRIMAIRE, COULEUR_TEXTE_CLAIR
from supabase_client import supabase

log = logging.getLogger(__name__)

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")
AGENCY_EMAIL = os.getenv("AGENCY_EMAIL", "")
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
PUBLIC_DASHBOARD_URL = os.getenv("PUBLIC_DASHBOARD_URL", "http://localhost:8501")


# ---------------------------------------------------------------------
# Lecture (utilisée par contrats_signature ET pages_publiques)
# ---------------------------------------------------------------------

def get_contrat_par_token(token: str | None) -> dict | None:
    """Résout un contrat STRICTEMENT par son signature_token — jamais par
    lead_id/contract_id : le token est le seul contrôle d'accès à la page
    publique de signature (voir pages_publiques.afficher_signature). Ne
    lève jamais d'exception (page publique, pas de traceback visible)."""
    if not token:
        return None
    try:
        rows = (
            supabase.table("contracts").select("*")
            .eq("signature_token", token).limit(1).execute().data
        )
    except Exception:
        return None
    return rows[0] if rows else None


def _get_lead(lead_id: str) -> dict | None:
    try:
        rows = supabase.table("leads").select("*").eq("id", lead_id).execute().data
    except Exception:
        return None
    return rows[0] if rows else None


def _get_intake_pour_lead(lead_id: str) -> dict:
    """Renvoie {} si aucune réponse d'intake trouvée (jamais une exception) :
    generer_pdf_devis() tolère un intake vide (intake.get(...) partout)."""
    try:
        rows = (
            supabase.table("intake_responses").select("*")
            .eq("lead_id", lead_id).order("created_at", desc=True).limit(1).execute().data
        )
    except Exception:
        return {}
    return rows[0] if rows else {}


def _lien_signature(token: str) -> str:
    return f"{PUBLIC_DASHBOARD_URL}/?vue=signature&token={token}"


# ---------------------------------------------------------------------
# Création du contrat + envoi du lien (remplace l'appel Yousign)
# ---------------------------------------------------------------------

def envoyer_contrat_signature_interne(lead: dict, intake: dict) -> bool:
    """Équivalent interne de contrats_signature.envoyer_contrat_signature() :
    même PDF (generer_pdf_devis, réutilisé tel quel), mais un lien de
    signature maison à la place d'une demande Yousign. Ne lève jamais
    d'exception : appelée depuis un formulaire public (voir même contrat que
    la fonction Yousign qu'elle remplace)."""
    from ceo_agent import send_email_prospect  # import différé : évite un cycle au chargement du module

    lead_id = lead["id"]
    token = secrets.token_urlsafe(32)

    try:
        supabase.table("contracts").insert({
            "lead_id": lead_id,
            "signature_provider": "interne",
            "signature_token": token,
            "yousign_status": "a_envoyer",
            "montant_centimes": int(CONTRACT_AMOUNT_EUR * 100),
        }).execute()
    except Exception as e:
        log.error(f"Erreur création du contrat (signature interne) pour {lead_id} : {e}")
        return False

    corps = (
        f"Bonjour,\n\n"
        f"Voici votre devis pour {lead.get('company', 'votre entreprise')} — merci de le "
        f"consulter et de le valider en ligne (2 minutes, aucune installation requise) :\n\n"
        f"{_lien_signature(token)}\n\n"
        f"Dès validation, nous revenons vers vous avec le lien de paiement pour lancer la "
        f"production.\n\nCordialement"
    )
    if not send_email_prospect(lead["email"], f"Votre devis {lead.get('company', '')}", corps, lead_id=lead_id):
        log.error(f"Échec envoi du lien de signature interne à {lead['email']}")
        return False

    supabase.table("leads").update({"status": "contrat_envoye"}).eq("id", lead_id).execute()
    log.info(f"Lien de signature interne envoyé à {lead['email']} (contrat lead={lead_id}).")
    return True


# ---------------------------------------------------------------------
# Capture du consentement (page publique) + preuve
# ---------------------------------------------------------------------

def _envoyer_email_avec_pdf(destinataire: str, sujet: str, corps: str, pdf_bytes: bytes, nom_fichier: str) -> bool:
    """Envoi SMTP Zoho avec pièce jointe — ceo_agent.send_email_prospect ne
    gère pas les pièces jointes (jamais eu besoin jusqu'ici) : variante
    locale à ce module plutôt que d'alourdir la fonction partagée pour ce
    seul appelant."""
    try:
        msg = MIMEMultipart()
        msg["From"] = ZOHO_USER
        msg["To"] = destinataire
        msg["Subject"] = f"[{AGENCY_NAME}] {sujet}"
        msg.attach(MIMEText(corps, "plain", "utf-8"))
        piece_jointe = MIMEApplication(pdf_bytes, _subtype="pdf")
        piece_jointe.add_header("Content-Disposition", "attachment", filename=nom_fichier)
        msg.attach(piece_jointe)

        with smtplib.SMTP_SSL("smtp.zoho.eu", 465) as s:
            s.login(ZOHO_USER, ZOHO_PASSWORD)
            s.sendmail(ZOHO_USER, destinataire, msg.as_string())
        return True
    except Exception as e:
        log.error(f"Erreur envoi email avec pièce jointe vers {destinataire} : {e}")
        if est_blocage_compte_zoho(e):
            alerter_blocage_compte_zoho(f"détecté lors de l'envoi du récapitulatif signé à {destinataire}", e)
        return False


def construire_pdf_preuve(contrat: dict, lead: dict, intake: dict, signed_at_affichage: str) -> bytes:
    """PDF final auto-suffisant : le devis original + le bloc de preuve de
    signature (nom saisi, horodatage, IP, user-agent, empreinte SHA-256 du
    document accepté) — voir les limites documentées en tête de module avant
    de considérer ce document comme équivalent à une signature Yousign."""
    pdf = FPDF()
    # cp1252 (Windows-1252) plutôt que le strict latin-1 par défaut, pour
    # accepter tiret cadratin/demi-cadratin, guillemets courbes, points de
    # suspension ou "€" dans un champ saisi par l'utilisateur (description
    # de l'intake, user-agent...) sans faire planter la génération — même
    # correctif que generation_contrats.py::_DocumentPDF.
    pdf.core_fonts_encoding = "cp1252"
    pdf.add_page()

    pdf.set_fill_color(*COULEUR_PRIMAIRE)
    pdf.rect(0, 0, 210, 22, style="F")
    pdf.set_xy(10, 6)
    pdf.set_text_color(*COULEUR_TEXTE_CLAIR)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, f"Contrat signe - {AGENCY_NAME}", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 28)

    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0, 7,
        f"Client : {lead.get('company', '')}\n"
        f"Description du projet : {intake.get('description', '')}\n\n"
        f"Prestation : Creation de site vitrine + optimisation SEO local (Done For You)\n"
        f"Montant : {(contrat.get('montant_centimes') or 0) / 100:.2f} EUR TTC\n",
    )
    pdf.ln(4)

    pdf.set_fill_color(*COULEUR_ACCENT)
    pdf.rect(10, pdf.get_y() + 1, 3, 4.5, style="F")
    pdf.set_xy(16, pdf.get_y())
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*COULEUR_PRIMAIRE)
    pdf.cell(0, 6.5, "Preuve de signature electronique (Article 1367 du Code civil)", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_x(10)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.ln(1)
    pdf.multi_cell(
        0, 6,
        f"Signe par (nom saisi)           : {contrat.get('signature_nom_saisi') or '-'}\n"
        f"Date et heure                   : {signed_at_affichage}\n"
        f"Adresse IP                      : {contrat.get('signature_ip') or 'non disponible'}\n"
        f"Navigateur (user-agent)         : {contrat.get('signature_user_agent') or 'non disponible'}\n"
        f"Empreinte du document (SHA-256) : {contrat.get('signature_document_hash') or '-'}\n",
    )
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.multi_cell(
        0, 4.5,
        "Signature electronique simple au sens de l'article 1367 du Code civil : valable "
        "pour identifier le signataire et etablir le lien avec le present acte, sans "
        "intervention d'un prestataire de confiance tiers independant.",
    )
    return bytes(pdf.output(dest="S"))


def enregistrer_signature(contrat: dict, nom_saisi: str) -> tuple[bool, str]:
    """Capture le consentement (nom tapé, horodatage, IP, user-agent),
    persiste la preuve et le PDF signé, fait passer le contrat à "signe" et
    le lead à "contrat_signe" (automatique, plus de bouton manuel pour ce
    provider), puis envoie le récapitulatif avec preuve par email. Renvoie
    (succès, message_erreur — vide si succès)."""
    nom_saisi = (nom_saisi or "").strip()
    if not nom_saisi:
        return False, "Merci de taper votre nom avant de valider."

    lead = _get_lead(contrat["lead_id"])
    if not lead:
        return False, "Contrat introuvable (fiche client associée manquante)."
    intake = _get_intake_pour_lead(contrat["lead_id"])

    # Régénéré (pas relu depuis un stockage) : generer_pdf_devis() est une
    # fonction pure de (lead, intake), déterministe — mêmes octets que ceux
    # affichés au visiteur juste avant son clic (voir
    # pages_publiques.afficher_signature).
    pdf_devis = generer_pdf_devis(lead, intake)
    document_hash = hashlib.sha256(pdf_devis).hexdigest()

    try:
        ip_address = st.context.ip_address
    except Exception:
        ip_address = None
    try:
        user_agent = st.context.headers.get("User-Agent")
    except Exception:
        user_agent = None

    horodatage = datetime.now(timezone.utc)
    signed_at_iso = horodatage.isoformat()
    signed_at_affichage = horodatage.strftime("%d/%m/%Y a %H:%M:%S UTC")

    try:
        supabase.table("contracts").update({
            "yousign_status": "signe",
            "signed_at": signed_at_iso,
            "signature_nom_saisi": nom_saisi,
            "signature_ip": ip_address,
            "signature_user_agent": user_agent,
            "signature_document_hash": document_hash,
            "signature_document_pdf_base64": base64.b64encode(pdf_devis).decode("ascii"),
        }).eq("id", contrat["id"]).execute()
        supabase.table("leads").update({"status": "contrat_signe"}).eq("id", contrat["lead_id"]).execute()
    except Exception as e:
        log.error(f"Erreur enregistrement signature pour contrat {contrat['id']} : {e}")
        return False, "Erreur technique lors de l'enregistrement — réessayez, ou contactez-nous si ça persiste."

    contrat_pour_pdf = {
        **contrat,
        "signature_nom_saisi": nom_saisi,
        "signature_ip": ip_address,
        "signature_user_agent": user_agent,
        "signature_document_hash": document_hash,
    }
    pdf_preuve = construire_pdf_preuve(contrat_pour_pdf, lead, intake, signed_at_affichage)
    nom_fichier = f"contrat_signe_{contrat['id']}.pdf"

    if lead.get("email"):
        corps = (
            f"Bonjour,\n\nVotre contrat est bien signé électroniquement, merci !\n\n"
            f"Vous trouverez ci-joint le récapitulatif signé (avec les éléments de preuve "
            f"de cette signature électronique).\n\n"
            f"Dès réception, nous vous envoyons le lien de paiement pour lancer la production."
            f"\n\nCordialement"
        )
        _envoyer_email_avec_pdf(
            lead["email"], f"Contrat signé — {lead.get('company', '')}", corps, pdf_preuve, nom_fichier,
        )
        if AGENCY_EMAIL and AGENCY_EMAIL != lead["email"]:
            _envoyer_email_avec_pdf(
                AGENCY_EMAIL, f"[Archive] Contrat signé — {lead.get('company', '')}", corps, pdf_preuve, nom_fichier,
            )

    log.info(f"Signature interne enregistrée pour le contrat {contrat['id']} (lead {contrat['lead_id']}).")
    return True, ""
