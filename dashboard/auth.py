"""
Authentification & session du dashboard (Portail Client).

Un seul écran de connexion pour les deux rôles : le rôle ('admin' ou
'client') et les campagnes autorisées viennent EXCLUSIVEMENT du jeton JWT
renvoyé par POST /auth/login (api/main.py) — jamais choisis ou modifiables
côté dashboard. L'état de session vit dans st.session_state, propre à
chaque session navigateur Streamlit.
"""

import streamlit as st

from api_client import ApiError, login as api_login

_CLES_SESSION = ("auth_token", "auth_role", "auth_campagnes", "auth_email")


def utilisateur_connecte() -> bool:
    return bool(st.session_state.get("auth_token"))


def est_admin() -> bool:
    return st.session_state.get("auth_role") == "admin"


def campagnes_autorisees() -> list[str]:
    """Campagnes accessibles au compte connecté. Vide pour un admin (aucune
    restriction côté API — voir api/main.py::obtenir_identite_dashboard)."""
    return st.session_state.get("auth_campagnes") or []


def deconnexion() -> None:
    for cle in _CLES_SESSION:
        st.session_state.pop(cle, None)
    st.rerun()


def _afficher_ecran_connexion() -> None:
    st.title("🔐 Connexion")
    st.caption("ai-company · Portail Admin & Client")

    with st.form("form_login"):
        email = st.text_input("E-mail")
        mot_de_passe = st.text_input("Mot de passe", type="password")
        connecter = st.form_submit_button("Se connecter", type="primary", use_container_width=True)

    if not connecter:
        return
    if not email or not mot_de_passe:
        st.warning("Merci de renseigner l'e-mail et le mot de passe.")
        return

    try:
        resultat = api_login(email.strip(), mot_de_passe)
    except ApiError as e:
        st.error(str(e))
        return

    st.session_state["auth_token"] = resultat["token"]
    st.session_state["auth_role"] = resultat["role"]
    st.session_state["auth_campagnes"] = resultat["campagnes"]
    st.session_state["auth_email"] = resultat["email"]
    st.rerun()


def exiger_connexion() -> None:
    """À appeler en tout premier dans app.py : bloque le rendu de la page
    tant que l'utilisateur n'est pas authentifié."""
    if utilisateur_connecte():
        return
    _afficher_ecran_connexion()
    st.stop()
