"""
Pages PUBLIQUES du dashboard — accessibles SANS connexion, contrairement à
toutes les autres pages (voir app.py, qui court-circuite exiger_connexion()
pour ces trois vues via un paramètre d'URL ?vue=...). Remplacent les
anciennes routes HTML de api/main.py :
- GET/POST /presentation/{lead_id}  -> ?vue=presentation&lead_id=...
- GET/POST /intake/{lead_id}        -> ?vue=intake&lead_id=...
- GET/POST /devis/{client_slug}     -> ?vue=devis&slug=...

Contenu et logique métier repris tels quels (mêmes champs de formulaire,
même comportement), seulement traduits en composants Streamlit natifs au
lieu de HTML/CSS brut.
"""

import os

import streamlit as st

from contrats_signature import envoyer_contrat_signature
from alertes import alerter_discord
from supabase_client import supabase

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# Tout statut atteint APRÈS une soumission d'intake réussie (voir
# envoyer_contrat_signature / marquer_contrat_signe / marquer_contrat_paye
# dans contrats_signature.py / data_access.py) — sert de garde-fou CÔTÉ
# SERVEUR contre une resoumission (st.session_state seul ne suffit pas : il
# est propre à une session navigateur, un lien réouvert ailleurs le
# contournerait entièrement).
STATUTS_INTAKE_DEJA_ENVOYE = {
    "intake_recu", "contrat_envoye", "contrat_signe", "lien_paiement_envoye", "paye",
}


def _get_lead(lead_id: str) -> dict | None:
    """None aussi bien si le lead n'existe pas que si la lecture échoue
    (ex: lead_id malformé — pas un UUID valide — ou Supabase injoignable) :
    ces pages sont publiques et sans authentification, une erreur ne doit
    jamais remonter un traceback brut à un visiteur externe."""
    try:
        leads = supabase.table("leads").select("*").eq("id", lead_id).execute().data
    except Exception:
        return None
    return leads[0] if leads else None


def _get_campagne_active_par_slug(slug: str) -> str | None:
    """Même principe que _get_lead() : toute erreur devient un simple
    'page introuvable', jamais un crash visible publiquement."""
    try:
        resultats = (
            supabase.table("campagnes").select("nom_client")
            .eq("slug", slug).eq("statut", "active").limit(1).execute().data
        )
    except Exception:
        return None
    return resultats[0]["nom_client"] if resultats else None


def afficher_presentation(lead_id: str | None) -> None:
    """Page publique consultée directement par l'artisan depuis son email —
    présente l'offre Done For You personnalisée."""
    if not lead_id:
        st.error("Lien invalide : identifiant manquant.")
        return

    lead = _get_lead(lead_id)
    if not lead:
        st.error("Présentation introuvable.")
        return

    company = lead.get("company") or "votre entreprise"
    pitch = lead.get("pitch_commercial") or lead.get("weakness") or ""

    st.title(f"{AGENCY_NAME} — Votre projet clé en main")
    with st.container(border=True):
        st.markdown(f"Bonjour **{company}**,")
        st.write(pitch)

    st.subheader("Ce qui est inclus, sans aucune action technique de votre part :")
    with st.container(border=True):
        st.markdown(
            "✅ Refonte complète de votre site vitrine  \n"
            "✅ Optimisation de votre fiche Google Maps / SEO local  \n"
            "✅ Capture des demandes de devis directement sur le site  \n"
            "✅ Aucun appel, aucune compétence technique requise de votre côté"
        )

    st.link_button("Démarrer mon projet (2 minutes) →", f"?vue=intake&lead_id={lead_id}", type="primary")
    st.caption(f"{AGENCY_NAME} — réponse par email uniquement.")


def afficher_intake(lead_id: str | None) -> None:
    """Formulaire asynchrone de collecte du contenu nécessaire à la
    production du site (aucun appel : tout est déclaratif, par écrit).
    Déclenche la génération du devis PDF + l'envoi en signature Yousign dès
    la soumission."""
    if not lead_id:
        st.error("Lien invalide : identifiant manquant.")
        return

    lead = _get_lead(lead_id)
    if not lead:
        st.error("Formulaire introuvable.")
        return

    cle_envoye = f"intake_envoye_{lead_id}"
    deja_recu = lead.get("status") in STATUTS_INTAKE_DEJA_ENVOYE
    if st.session_state.get(cle_envoye) or deja_recu:
        st.title("Merci, c'est enregistré !")
        with st.container(border=True):
            st.write(
                "Nous avons bien reçu toutes les informations. On prépare votre site et "
                "on revient vers vous par email dès qu'il y a du nouveau — toujours sans appel."
            )
        return

    company = lead.get("company") or "votre entreprise"
    st.title(f"Démarrons votre projet, {company}")
    st.write("Ce formulaire remplace tout appel téléphonique : remplissez-le à votre rythme.")

    # max_chars sur chaque champ : ce formulaire est public et non authentifié,
    # rien n'empêche un abus (texte massif envoyé en boucle) sans une limite
    # basique — appliquée au niveau du widget, la plus simple à maintenir.
    with st.form("form_intake_public"):
        description = st.text_area(
            "Décrivez votre activité en quelques phrases", height=120, max_chars=3000,
        )
        zone_activite = st.text_input(
            "Zone d'intervention (villes/rayon)", placeholder="Ex : Reims et 30km alentour", max_chars=300,
        )
        lien_photos = st.text_input(
            "Lien vers vos photos (Google Drive, WeTransfer...)", placeholder="https://...", max_chars=500,
        )
        lien_site_actuel = st.text_input(
            "Lien de votre site actuel (si vous en avez un)", placeholder="https://...", max_chars=500,
        )
        lien_gbp = st.text_input(
            "Lien de votre fiche Google Maps / Google Business Profile", placeholder="https://...", max_chars=500,
        )
        telephone_public = st.text_input(
            "Téléphone à afficher publiquement sur le site (pas pour vous appeler)",
            placeholder="0X XX XX XX XX", max_chars=50,
        )
        envoyer = st.form_submit_button("Envoyer", type="primary", use_container_width=True)

    if not envoyer:
        return
    if not description.strip():
        st.warning("Merci de décrire votre activité avant d'envoyer.")
        return

    payload = {
        "lead_id": lead_id,
        "description": description,
        "zone_activite": zone_activite,
        "lien_photos": lien_photos,
        "lien_site_actuel": lien_site_actuel,
        "lien_gbp": lien_gbp,
        "telephone_public": telephone_public,
    }
    try:
        supabase.table("intake_responses").insert(payload).execute()
        supabase.table("leads").update({"status": "intake_recu"}).eq("id", lead_id).execute()
    except Exception:
        st.error("Erreur lors de l'enregistrement, réessayez plus tard.")
        return

    with st.spinner("Génération de votre devis et envoi en signature électronique..."):
        envoyer_contrat_signature(lead, payload)

    st.session_state[cle_envoye] = True
    st.rerun()


def afficher_devis(slug: str | None) -> None:
    """Formulaire public de demande de devis pour un particulier — seul
    canal par lequel un maître d'ouvrage privé entre dans le système. Le
    slug doit correspondre à une campagne ACTIVE, jamais une URL arbitraire."""
    if not slug:
        st.error("Page introuvable.")
        return

    client_final = _get_campagne_active_par_slug(slug)
    if not client_final:
        st.error("Page introuvable.")
        return

    cle_envoye = f"devis_envoye_{slug}"
    if st.session_state.get(cle_envoye):
        st.title("Merci pour votre demande !")
        with st.container(border=True):
            st.write("Nous avons bien reçu votre demande et revenons vers vous très rapidement.")
        return

    st.title(f"Demande de devis — {client_final}")
    st.write("Décrivez votre projet, nous revenons vers vous rapidement.")

    with st.form("form_devis_public"):
        nom = st.text_input("Votre nom", max_chars=200)
        email = st.text_input("E-mail", max_chars=200)
        telephone = st.text_input("Téléphone", placeholder="0X XX XX XX XX", max_chars=50)
        type_projet = st.text_input(
            "Type de projet", placeholder="Construction, rénovation lourde, extension...", max_chars=300,
        )
        commune = st.text_input("Commune du projet", max_chars=200)
        budget_estime = st.text_input("Budget estimé (optionnel)", max_chars=100)
        message = st.text_area("Votre message", height=120, max_chars=3000)
        consentement = st.checkbox(f"J'accepte d'être recontacté(e) par {client_final} au sujet de ma demande.")
        envoyer = st.form_submit_button("Envoyer ma demande", type="primary", use_container_width=True)

    if envoyer:
        if not nom.strip():
            st.warning("Merci de renseigner votre nom.")
        elif not consentement:
            st.warning("Merci de cocher la case de consentement pour continuer.")
        else:
            payload = {
                "client_final": client_final,
                "nom": nom,
                "email": email or None,
                "telephone": telephone or None,
                "type_projet": type_projet or None,
                "commune": commune or None,
                "budget_estime": budget_estime or None,
                "message": message or None,
                "consentement": True,
            }
            try:
                supabase.table("demandes_devis_particuliers").insert(payload).execute()
            except Exception:
                st.error("Erreur lors de l'enregistrement, réessayez plus tard.")
            else:
                # Lead entrant chaud (le particulier vient de faire la démarche
                # lui-même) : alerte immédiate, contrairement aux prospects
                # sortants qui suivent la veille des réponses e-mail habituelle.
                alerter_discord(f"🔥 Nouvelle demande de devis entrante pour {client_final} : {nom}")
                st.session_state[cle_envoye] = True
                st.rerun()

    st.caption("Vos informations ne sont utilisées que pour traiter cette demande de devis.")
