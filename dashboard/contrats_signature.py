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
from fpdf.enums import XPos, YPos

from generation_contrats import (
    COULEUR_ACCENT,
    COULEUR_PRIMAIRE,
    COULEUR_TEXTE_CLAIR,
    DEFAULTS_AGENCE,
    LIBELLE_PALIER,
    TYPE_OFFRE_ABONNEMENT,
    TYPE_OFFRE_UNITE,
    construire_articles_cgv_b2c,
    formule_abonnement_par_id,
    fourchette_prix_unite_eur,
    palier_pour_score,
    prix_lead_unite_eur,
)
from supabase_client import supabase

log = logging.getLogger(__name__)

YOUSIGN_API_KEY = os.getenv("YOUSIGN_API_KEY", "")
YOUSIGN_API_URL = os.getenv("YOUSIGN_API_URL", "https://api-sandbox.yousign.app/v3")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


def normaliser_type_offre(type_offre: str | None) -> str:
    """Jamais None/inconnu au-delà de ce point : toute valeur absente ou
    corrompue retombe sur TYPE_OFFRE_UNITE plutôt que de faire échouer la
    génération du devis/contrat (même principe que
    construire_articles_cgv_b2c côté generation_contrats.py)."""
    return type_offre if type_offre == TYPE_OFFRE_ABONNEMENT else TYPE_OFFRE_UNITE


def montant_centimes_a_la_signature(type_offre: str | None, formule_abonnement: str | None) -> int | None:
    """Montant à enregistrer sur le contrat au moment de la signature.
    - abonnement : connu immédiatement (formule de volume choisie à
      l'intake, voir generation_contrats.py::FORMULES_ABONNEMENT).
    - à l'unité : PAS connu à ce stade — dépend du score du lead qui sera
      réellement livré (voir generation_contrats.py::palier_pour_score),
      calculé plus tard au moment de la facturation (voir
      creer_et_envoyer_lien_paiement ci-dessous). Renvoie None : le champ
      contracts.montant_centimes reste NULL jusqu'à la livraison, ce n'est
      pas une donnée manquante par erreur."""
    if normaliser_type_offre(type_offre) == TYPE_OFFRE_ABONNEMENT:
        return int(formule_abonnement_par_id(formule_abonnement)["prix_eur"] * 100)
    return None


def libelle_prestation(
    type_offre: str | None, formule_abonnement: str | None = None, palier: str | None = None,
) -> str:
    """Libellé humain de la prestation — utilisé dans le devis PDF ET comme
    nom de produit Stripe (voir creer_et_envoyer_lien_paiement), pour qu'un
    même contrat affiche toujours un intitulé cohérent du devis jusqu'au
    paiement.

    `palier` (PALIER_PREMIUM/STANDARD/BASIQUE) n'est connu qu'au moment de
    la livraison du lead — absent (devis initial, avant livraison), le
    libellé "à l'unité" affiche la fourchette de prix plutôt qu'un palier
    précis pas encore déterminé."""
    if normaliser_type_offre(type_offre) == TYPE_OFFRE_ABONNEMENT:
        formule = formule_abonnement_par_id(formule_abonnement)
        return f"Abonnement leads qualifiés — {formule['label']}"
    if palier:
        return f"Lead qualifié à l'unité — {LIBELLE_PALIER[palier]}"
    mini, maxi = fourchette_prix_unite_eur()
    return f"Lead qualifié à l'unité (prix selon la qualité du lead livré, {mini:.0f}-{maxi:.0f} € TTC)"


def _charger_config_agence() -> dict:
    """Copie locale de administration_contrats.py::charger_config_agence
    plutôt qu'un import cross-page (ce module est appelé depuis un
    formulaire public, voir docstring en tête de fichier — jamais depuis une
    page Streamlit elle-même) : même table agence_config, même repli sur
    DEFAULTS_AGENCE si la table est vide/absente ou Supabase injoignable, ne
    lève jamais d'exception."""
    try:
        rows = supabase.table("agence_config").select("*").eq("cle", "agence").limit(1).execute().data
    except Exception:
        return dict(DEFAULTS_AGENCE)
    if not rows:
        return dict(DEFAULTS_AGENCE)
    return {**DEFAULTS_AGENCE, **{k: v for k, v in rows[0].items() if k in DEFAULTS_AGENCE}}


def generer_pdf_devis(lead: dict, intake: dict) -> bytes:
    """Génère le devis PDF à partir des infos du lead et de l'intake, CGV B2C
    incluses (voir generation_contrats.py::construire_articles_cgv_b2c) —
    pour transmission en signature électronique (aucune saisie manuelle).
    Vente de leads qualifiés (à l'unité ou abonnement, voir intake["type_offre"]),
    pas la prestation "site vitrine" de l'ancienne version de ce module (voir
    diagnostic du 2026-08-14). Même style de mise en page (bandeau d'en-tête,
    sections) que le bon de commande B2B (generation_contrats.py::construire_pdf),
    pour une identité visuelle cohérente entre les deux tunnels.

    Signature inchangée (lead, intake) -> bytes : la config agence est
    rechargée en interne (_charger_config_agence) plutôt qu'ajoutée en
    paramètre, pour ne casser aucun des 3 appelants existants
    (contrats_signature.envoyer_contrat_signature, signature_interne, et
    pages_publiques.afficher_signature)."""
    agence = _charger_config_agence()
    nom_agence = agence.get("nom") or DEFAULTS_AGENCE["nom"]
    type_offre = normaliser_type_offre(intake.get("type_offre"))

    if type_offre == TYPE_OFFRE_ABONNEMENT:
        formule = formule_abonnement_par_id(intake.get("formule_abonnement"))
        libelle = libelle_prestation(type_offre, formule_abonnement=formule["id"])
        ligne_montant = f"Montant : {formule['prix_eur']:.2f} EUR TTC / mois ({formule['label']})"
    else:
        # Palier (donc prix exact) inconnu à ce stade : seul le lead
        # réellement livré déterminera le tarif applicable — voir
        # creer_et_envoyer_lien_paiement. Le devis affiche la fourchette.
        libelle = libelle_prestation(type_offre)
        mini, maxi = fourchette_prix_unite_eur()
        ligne_montant = (
            f"Montant : entre {mini:.2f} EUR et {maxi:.2f} EUR TTC / lead, selon la "
            f"qualité du lead livré (facturé à chaque livraison)"
        )

    pdf = FPDF(format="A4")
    # cp1252 (Windows-1252) plutôt que le strict latin-1 par défaut, pour
    # accepter tiret cadratin/demi-cadratin, guillemets courbes, points de
    # suspension ou "€" dans un champ saisi par l'utilisateur (description
    # de l'intake notamment) sans faire planter la génération — même
    # correctif que generation_contrats.py::_DocumentPDF.
    pdf.core_fonts_encoding = "cp1252"
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # --- Bandeau d'en-tête : même style que generation_contrats.py::construire_pdf ---
    pdf.set_fill_color(*COULEUR_PRIMAIRE)
    pdf.rect(0, 0, 210, 26, style="F")
    pdf.set_xy(10, 6)
    pdf.set_text_color(*COULEUR_TEXTE_CLAIR)
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 9, nom_agence, ln=True)
    pdf.set_x(10)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, "Devis - Fourniture de leads qualifies")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 31)

    def titre_section(texte: str) -> None:
        pdf.set_fill_color(*COULEUR_ACCENT)
        pdf.rect(10, pdf.get_y() + 1, 3, 4.5, style="F")
        pdf.set_xy(16, pdf.get_y())
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*COULEUR_PRIMAIRE)
        pdf.cell(0, 6.5, texte, ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.ln(0.5)

    # --- Objet du devis ---
    titre_section("Devis")
    pdf.set_x(10)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.multi_cell(
        0, 6,
        f"Client : {lead.get('company', '')}\n"
        f"Corps de métier : {intake.get('corps_metier', '') or '—'}\n"
        f"Zone d'intervention : {intake.get('zone_activite', '') or '—'}\n"
        f"Description de l'activité : {intake.get('description', '')}\n\n"
        f"Prestation : {libelle}\n"
        f"{ligne_montant}",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.ln(2)

    # --- Corps des CGV B2C ---
    titre_section("Conditions générales de prestation")
    for titre, corps in construire_articles_cgv_b2c(agence, type_offre):
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.multi_cell(0, 5, titre, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(10)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.multi_cell(0, 4.3, corps, align="J", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.5)
    pdf.ln(1)

    pdf.set_x(10)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(
        0, 5,
        "En signant électroniquement ce document, le client accepte les conditions "
        "générales de prestation ci-dessus et le lancement de la prestation.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
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

    try:
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
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur réseau Yousign pour {lead_id} : {e}")
        return False
    except (ValueError, KeyError) as e:
        log.error(f"Réponse Yousign inattendue (JSON invalide/champ manquant) pour {lead_id} : {e}")
        return False

    type_offre = normaliser_type_offre(intake.get("type_offre"))
    formule_abonnement = intake.get("formule_abonnement") if type_offre == TYPE_OFFRE_ABONNEMENT else None
    supabase.table("contracts").insert({
        "lead_id": lead_id,
        "yousign_request_id": signature_request_id,
        "yousign_status": "envoye",
        "type_offre": type_offre,
        "formule_abonnement": formule_abonnement,
        "montant_centimes": montant_centimes_a_la_signature(type_offre, formule_abonnement),
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
    type_offre = normaliser_type_offre(contrat.get("type_offre"))

    champs_maj = {}
    if type_offre == TYPE_OFFRE_ABONNEMENT:
        formule = formule_abonnement_par_id(contrat.get("formule_abonnement"))
        montant_centimes = int(formule["prix_eur"] * 100)
        nom_produit = f"{libelle_prestation(type_offre, formule_abonnement=formule['id'])} — {lead.get('company', '')}"
    else:
        # À l'unité : le prix n'est connu qu'à la livraison — c'est ICI,
        # au moment où le paiement est réellement déclenché, que le score
        # actuel du lead (leads.score) détermine le palier facturé. Voir
        # generation_contrats.py::palier_pour_score/prix_lead_unite_eur.
        palier = palier_pour_score(lead.get("score"))
        montant_centimes = int(prix_lead_unite_eur(lead.get("score")) * 100)
        nom_produit = f"{libelle_prestation(type_offre, palier=palier)} — {lead.get('company', '')}"
        # Le montant n'était pas encore connu à la signature (voir
        # montant_centimes_a_la_signature) : on le fixe maintenant, pour que
        # le contrat reflète fidèlement ce qui a été réellement facturé.
        champs_maj["montant_centimes"] = montant_centimes

    try:
        parametres_prix = {
            "unit_amount": montant_centimes,
            "currency": "eur",
            "product_data": {"name": nom_produit},
        }
        if type_offre == TYPE_OFFRE_ABONNEMENT:
            # Abonnement mensuel à volume inclus : prix Stripe récurrent —
            # le lien de paiement devient alors une souscription (facturée
            # chaque mois), pas un paiement unique comme pour la formule
            # "à l'unité" ci-dessous.
            parametres_prix["recurring"] = {"interval": "month"}
        prix = stripe.Price.create(**parametres_prix)
        lien = stripe.PaymentLink.create(
            line_items=[{"price": prix.id, "quantity": 1}],
            metadata={"lead_id": lead_id, "contract_id": contract_id, "type_offre": type_offre},
            # Lien à usage unique : sans cette restriction, un double-clic,
            # un retour navigateur + re-soumission, ou une réutilisation du
            # lien transféré déclenche un second paiement Stripe RÉEL (ou une
            # seconde souscription pour la formule abonnement) — invisible
            # côté application (idempotente sur l'écriture métier, mais rien
            # ne détecte le double paiement Stripe lui-même).
            restrictions={"completed_sessions": {"limit": 1}},
        )
    except stripe.error.StripeError as e:
        log.error(f"Erreur création lien de paiement Stripe pour {lead_id} : {e}")
        return False

    champs_maj.update({"stripe_payment_link_id": lien.id, "stripe_payment_url": lien.url})
    supabase.table("contracts").update(champs_maj).eq("id", contract_id).execute()
    supabase.table("leads").update({"status": "lien_paiement_envoye"}).eq("id", lead_id).execute()

    corps = (
        f"Bonjour,\n\nVotre contrat est bien signé, merci !\n\n"
        f"Pour activer votre offre, voici votre lien de paiement sécurisé :\n{lien.url}\n\n"
        f"Dès le paiement reçu, l'envoi de vos leads démarre immédiatement.\n\nCordialement"
    )
    if send_email_prospect(
        lead["email"], f"Contrat signé — lien de paiement {lead.get('company', '')}", corps, lead_id=lead_id
    ):
        log.info(f"Lien de paiement envoyé à {lead['email']}")
        return True
    log.error(f"Échec envoi du lien de paiement à {lead['email']}")
    return False
