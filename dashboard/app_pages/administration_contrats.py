"""
Interface "Administration & Contrats" — génération d'un document contractuel
(bon de commande / contrat de prestation) pré-rempli avec les informations
d'un client de l'agence, quel qu'il soit, prêt à copier ou télécharger
en PDF. Le PDF intègre directement le corps des Conditions Générales de
Prestation (CGV) de l'agence : le document généré est prêt à être envoyé
tel quel à un client (ex. S.B.G Travaux) avec le devis ou le bon de commande.

Les informations de l'agence elle-même (nom, adresse, e-mail, statut SIRET)
se configurent une seule fois via la section "Configuration de l'agence"
ci-dessous, et sont ensuite injectées automatiquement dans l'en-tête et le
bloc "Pour l'agence" de chaque document généré (voir charger_config_agence).

Espace admin uniquement : ces documents engagent l'agence auprès de tiers,
jamais accessible depuis le portail client (voir dashboard/app.py).

Ce générateur ne fait QUE produire un document à partir de la saisie du
formulaire — rien n'est enregistré côté Supabase pour les bons de commande
eux-mêmes (chaque génération repart d'une saisie vierge) ; seule la
configuration de l'agence est persistée (table agence_config, voir
sql/init_agence_config.sql — remplace un ancien fichier JSON local qui ne
survivait pas à un redémarrage sur Streamlit Community Cloud).

La logique de mise en page/juridique (CGV, PDF) vit dans generation_contrats.py
(racine du dépôt) — ce fichier ne contient que la couche de présentation
Streamlit.
"""

from datetime import date, datetime, timezone

import streamlit as st

from common import liste_noms_campagnes, safe_call
from data_access import get_campagnes
from generation_contrats import (
    DEFAULTS_AGENCE,
    construire_articles_cgv,
    construire_pdf,
    construire_texte,
    slugifier_reference,
)
from supabase_client import supabase


# ---------------------------------------------------------------------
# Configuration de l'agence — persistée dans Supabase (table agence_config,
# une seule ligne clé='agence') pour survivre aux redémarrages/redéploiements
# du conteneur Streamlit Cloud, contrairement à un fichier local.
# ---------------------------------------------------------------------

def charger_config_agence() -> dict:
    try:
        rows = supabase.table("agence_config").select("*").eq("cle", "agence").limit(1).execute().data
    except Exception:
        # Table pas encore créée (migration sql/init_agence_config.sql non
        # appliquée) ou Supabase injoignable : repli sur les valeurs par
        # défaut plutôt qu'un crash — cette page reste utilisable, seule la
        # persistance de la config est indisponible.
        return dict(DEFAULTS_AGENCE)
    if not rows:
        return dict(DEFAULTS_AGENCE)
    return {**DEFAULTS_AGENCE, **{k: v for k, v in rows[0].items() if k in DEFAULTS_AGENCE}}


def sauvegarder_config_agence(config: dict) -> None:
    corps = {"cle": "agence", **config, "updated_at": datetime.now(timezone.utc).isoformat()}
    supabase.table("agence_config").upsert(corps, on_conflict="cle").execute()


st.title("📑 Administration & Contrats")
st.caption(
    "Génère un bon de commande / contrat de prestation pré-rempli pour n'importe quel client "
    "de l'agence, CGV incluses, prêt à copier ou télécharger en PDF."
)
st.info(
    "Le PDF généré inclut le bon de commande ET le corps des Conditions Générales de "
    "Prestation de l'agence — il est prêt à être envoyé tel quel à un client. Rien n'est "
    "enregistré côté serveur pour les bons de commande : chaque génération repart d'une "
    "saisie vierge (seule la configuration de l'agence ci-dessous est mémorisée).",
    icon="ℹ️",
)

agence_config = charger_config_agence()

with st.expander(
    "⚙️ Configuration de l'agence — à renseigner une seule fois (auto-remplit le bloc « Pour l'agence »)",
    expanded=(agence_config == DEFAULTS_AGENCE),
):
    with st.form("form_config_agence"):
        nom_agence_saisi = st.text_input("Nom de l'agence", value=agence_config["nom"])
        adresse_agence_saisie = st.text_area(
            "Adresse du siège", value=agence_config["adresse"], height=80,
            placeholder="ex. 1 rue de l'Exemple, 75000 Paris",
        )
        email_agence_saisi = st.text_input(
            "E-mail de contact", value=agence_config["email"], placeholder="ex. contact@expertise-digitale.fr",
        )
        statut_siret_libelle = st.radio(
            "Statut SIRET",
            options=["En cours d'immatriculation", "Immatriculé (SIRET définitif)"],
            index=1 if agence_config["siret_statut"] == "definitif" else 0,
            horizontal=True,
        )
        siret_agence_saisi = st.text_input(
            "Numéro SIRET (si immatriculé)", value=agence_config["siret_numero"],
            placeholder="ex. 123 456 789 00012",
        )
        enregistrer_agence = st.form_submit_button(
            "💾 Enregistrer la configuration de l'agence", type="primary", use_container_width=True,
        )

    if enregistrer_agence:
        try:
            sauvegarder_config_agence({
                "nom": nom_agence_saisi.strip() or DEFAULTS_AGENCE["nom"],
                "adresse": adresse_agence_saisie.strip(),
                "email": email_agence_saisi.strip(),
                "siret_statut": "definitif" if statut_siret_libelle.startswith("Immatriculé") else "en_cours",
                "siret_numero": siret_agence_saisi.strip(),
            })
        except Exception as e:
            st.error(
                f"Impossible d'enregistrer la configuration : {e}. La table `agence_config` "
                "existe-t-elle en base (voir sql/init_agence_config.sql) ?"
            )
        else:
            st.success("Configuration de l'agence enregistrée — elle sera utilisée pour tous les prochains documents.")
            st.rerun()

campagnes_data, campagnes_error = safe_call(get_campagnes)
if campagnes_error:
    st.error(campagnes_error)
    st.stop()
noms_campagnes = liste_noms_campagnes(campagnes_data)

st.subheader("1. Client")

mode_client = st.radio(
    "Sélection du client",
    options=["Nouveau client (saisie libre)", "Client existant (campagne configurée)"],
    horizontal=True,
    index=0,
    disabled=not noms_campagnes,
    label_visibility="collapsed",
)

if mode_client == "Client existant (campagne configurée)" and noms_campagnes:
    nom_entreprise = st.selectbox("Entreprise cliente", options=noms_campagnes)
else:
    nom_entreprise = st.text_input("Nom de l'entreprise cliente", placeholder="ex. ACME Bâtiment SARL")

col1, col2 = st.columns(2)
with col1:
    siret_client = st.text_input("Numéro SIRET du client", placeholder="ex. 123 456 789 00012")
    nom_representant = st.text_input("Nom du représentant", placeholder="ex. Jean Dupont")
with col2:
    adresse_siege = st.text_area(
        "Adresse du siège social",
        placeholder="ex. 1 rue de l'Exemple, 75000 Paris",
        height=100,
    )

st.subheader("2. Objet de la commande")

col_obj1, col_obj2 = st.columns(2)
with col_obj1:
    volume_prestation = st.text_area(
        "Volume de la prestation / du test",
        placeholder="ex. Phase de test limitée à 2 leads qualifiés, livrés sous 15 jours.",
        height=80,
    )
with col_obj2:
    prix_prestation = st.text_area(
        "Prix de la prestation",
        placeholder="ex. 990 € HT, ou « Phase de test gratuite »",
        height=80,
    )

conditions_particulieres = st.text_area(
    "Conditions particulières (facultatif, une par ligne — en complément des CGV ci-dessous)",
    placeholder="ex. Livraison des leads sous 15 jours ouvrés.",
    height=90,
)

with st.expander("📜 Conditions générales de prestation incluses dans le document"):
    for titre, corps in construire_articles_cgv(agence_config):
        st.markdown(f"**{titre}**")
        st.caption(corps)

date_document = st.date_input("Date du document", value=date.today())

st.divider()
st.subheader("3. Génération")

if st.button("📄 Générer le document", type="primary", use_container_width=True):
    if not nom_entreprise.strip():
        st.warning("Le nom de l'entreprise cliente est requis.")
    else:
        donnees = {
            "nom_entreprise": nom_entreprise.strip(),
            "siret_client": siret_client.strip(),
            "adresse_siege": adresse_siege.strip(),
            "nom_representant": nom_representant.strip(),
            "volume_prestation": volume_prestation.strip(),
            "prix_prestation": prix_prestation.strip(),
            "conditions_particulieres": conditions_particulieres,
            "date_document": date_document.strftime("%d/%m/%Y"),
            "reference": f"{slugifier_reference(agence_config['nom'], 'ED')}-{date_document:%Y%m%d}-{slugifier_reference(nom_entreprise)}",
        }
        st.session_state["contrat_donnees"] = donnees

donnees = st.session_state.get("contrat_donnees")
if donnees:
    try:
        with st.spinner("Génération du document..."):
            texte = construire_texte(donnees, agence_config)
            pdf_bytes = construire_pdf(donnees, agence_config)
    except Exception as e:
        # Filet de sécurité total : génération purement locale (pas d'appel
        # réseau) mais un caractère non pris en charge dans un champ saisi
        # (ou tout autre bug) ne doit jamais faire planter la page — un
        # message d'erreur clair, jamais une page figée ou un traceback brut.
        texte, pdf_bytes = None, None
        st.error(
            f"Impossible de générer le document (probablement un caractère non pris en charge "
            f"dans un des champs) : {e}"
        )

    if texte and pdf_bytes:
        st.text_area("Aperçu", value=texte, height=380)

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "⬇️ Télécharger en PDF",
                data=pdf_bytes,
                file_name=f"{donnees['reference']}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with col_dl2:
            st.download_button(
                "⬇️ Télécharger en texte (.txt)",
                data=texte,
                file_name=f"{donnees['reference']}.txt",
                mime="text/plain",
                use_container_width=True,
            )
