"""
Dashboard client pour ai-company — Portail Admin & Client.

Lancement :
    streamlit run app.py

Configuration :
    Variable d'environnement AI_COMPANY_API_URL (défaut http://localhost:8000)

Structure (multi-pages) :
    app.py                        -> point d'entrée : connexion + navigation par rôle
    auth.py                       -> écran de login + session_state
    app_pages/sourcing.py           -> [Admin] Sourcing / Scraping
    app_pages/gestion_clients.py    -> [Admin] Gestion & Réponse aux clients
    app_pages/administration_contrats.py -> [Admin] Administration & Contrats
    app_pages/portail_client.py     -> [Client] Vue restreinte à ses propres campagnes
                                        (également accessible à l'admin, en aperçu/test,
                                        via un sélecteur de campagne — voir portail_client.py)

Le dossier s'appelle "app_pages/" et NON "pages/" : Streamlit détecte
automatiquement tout dossier littéralement nommé "pages/" à côté du script
d'entrée et construit SA PROPRE navigation en plus de celle définie ici via
st.navigation()/st.Page() — les deux mécanismes entrent alors en conflit
("st.navigation was called in an app with a pages/ directory", comportement
indéterminé/reruns superflus). st.navigation() est le mécanisme voulu ici
(navigation conditionnée par le rôle) ; le dossier est donc renommé pour ne
plus jamais être auto-détecté.

Toute la page est bloquée par auth.exiger_connexion() tant qu'aucune session
valide n'existe : le rôle affiché (Admin/Client) et les campagnes accessibles
viennent uniquement du jeton JWT renvoyé par l'API au login, jamais d'un
choix fait dans le dashboard lui-même.
"""

import streamlit as st

from api_client import API_BASE_URL, get_health
from auth import campagnes_autorisees, deconnexion, est_admin, exiger_connexion
from common import safe_call

st.set_page_config(
    page_title="ai-company · Dashboard",
    page_icon="📊",
    layout="wide",
)

exiger_connexion()


# ---------------------------------------------------------------------
# Sidebar — commun à toutes les pages
# ---------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ ai-company")
    st.caption(f"API : `{API_BASE_URL}`")

    role_libelle = "🛠️ Admin" if est_admin() else "👤 Client"
    st.markdown(f"**{st.session_state.get('auth_email', '')}**  \n{role_libelle}")
    if not est_admin():
        for campagne in campagnes_autorisees():
            st.caption(f"📌 {campagne}")

    if st.button("🚪 Se déconnecter", use_container_width=True):
        deconnexion()
    if st.button("🔄 Rafraîchir les données", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()

    if est_admin():
        st.subheader("État des services")
        health, health_error = safe_call(get_health)
        if health_error:
            st.error(health_error)
        elif health:
            for cle in ("supabase", "ollama"):
                valeur = health.get(cle, "?")
                emoji = "🟢" if valeur == "ok" else ("🟡" if "degraded" in str(valeur) else "🔴")
                st.caption(f"{emoji} {cle.capitalize()} : {valeur}")
            st.caption("🟢 Zoho configuré" if health.get("zoho_configure") else "⚪ Zoho non configuré")
            st.caption("🟢 Discord configuré" if health.get("discord_configure") else "⚪ Discord non configuré")
        st.divider()

    st.caption("ai-company · dashboard")


# ---------------------------------------------------------------------
# Navigation — dépend uniquement du rôle du jeton, jamais d'un choix
# manuel : un compte client ne doit jamais pouvoir atteindre les pages admin.
# ---------------------------------------------------------------------

if est_admin():
    page_sourcing = st.Page("app_pages/sourcing.py", title="Sourcing / Scraping", icon="🔍")
    page_gestion = st.Page("app_pages/gestion_clients.py", title="Gestion & Réponse", icon="📇")
    page_administration = st.Page(
        "app_pages/administration_contrats.py", title="Administration & Contrats", icon="📑"
    )
    # Portail Client accessible en aperçu à l'admin (choix de la campagne à
    # prévisualiser géré dans portail_client.py) — pour pouvoir tester
    # directement depuis l'interface ce que voit un compte client, sans
    # changer ce qu'un compte client peut lui-même voir (toujours restreint
    # à ses seules campagnes, voir auth.py::campagnes_autorisees).
    page_portail_client = st.Page("app_pages/portail_client.py", title="Portail Client (aperçu)", icon="📊")
    pg = st.navigation([page_sourcing, page_gestion, page_administration, page_portail_client])
else:
    page_client = st.Page("app_pages/portail_client.py", title="Mon espace", icon="📊")
    pg = st.navigation([page_client])

pg.run()
