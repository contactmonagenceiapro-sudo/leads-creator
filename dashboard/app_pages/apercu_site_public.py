"""
Aperçu du site public (dashboard/pages_publiques.py — accueil/comment_ca_marche/
tarifs/a_propos/devenir_client) directement depuis le dashboard admin, sans
avoir à ouvrir un onglet séparé ni à se déconnecter pour vérifier ce qu'un
visiteur voit réellement (grilles tarifaires notamment).

Embarque la VRAIE page publique via iframe, pointée sur PUBLIC_DASHBOARD_URL
(même variable que mail_processor.py::envoyer_suivi_positif, ex:
https://mon-app.streamlit.app) — aucune logique de rendu dupliquée ici :
c'est exactement la page que voit un visiteur, thème "Chantier Ouvert" clair
inclus (voir dashboard/app.py, injecter_theme_vitrine()).
"""

import os

import streamlit as st
import streamlit.components.v1 as components

PUBLIC_DASHBOARD_URL = os.getenv("PUBLIC_DASHBOARD_URL", "http://localhost:8501")

st.title("👁️ Aperçu du site public")
st.caption(
    "Rendu en direct des pages publiques — exactement ce qu'un visiteur voit, sans connexion. "
    "URL de base configurée via PUBLIC_DASHBOARD_URL (.env en local, secret Streamlit en production)."
)

PAGES_VITRINE = {
    "Tarifs": "tarifs",
    "Accueil": "accueil",
    "Comment ça marche": "comment_ca_marche",
    "À propos": "a_propos",
    "Devenir client": "devenir_client",
}

libelle_page = st.selectbox("Page à prévisualiser", options=list(PAGES_VITRINE.keys()))
url_page = f"{PUBLIC_DASHBOARD_URL.rstrip('/')}/?vue={PAGES_VITRINE[libelle_page]}"

st.link_button("🔗 Ouvrir dans un nouvel onglet", url_page)

if PUBLIC_DASHBOARD_URL == "http://localhost:8501":
    st.warning(
        "PUBLIC_DASHBOARD_URL n'est pas configurée — l'aperçu ci-dessous pointe sur localhost "
        "et ne fonctionnera que si le dashboard tourne en local sur ce poste."
    )

components.iframe(url_page, height=900, scrolling=True)
