"""
Authentification & session du dashboard (Portail Client).

Un seul écran de connexion pour les deux rôles. Plus de JWT : la vérification
(bcrypt contre la table utilisateurs_dashboard) se fait directement ici,
dans le même process Streamlit qui affiche les pages — le rôle et les
campagnes autorisées sont stockés dans st.session_state, propre à chaque
session navigateur Streamlit (le rôle d'un jeton signé séparément n'a plus
d'utilité puisqu'il n'y a plus de frontière réseau à franchir : la seule
façon d'atteindre ce code est de passer par cette page elle-même).
"""

import re

import bcrypt
import streamlit as st

from data_access import DataAccessError
from supabase_client import supabase

_CLES_SESSION = ("auth_connecte", "auth_role", "auth_campagnes", "auth_email")

# Volontairement permissif (pas de RFC 5322 complet) : sert juste à
# rejeter les fautes de frappe évidentes ("test", "a@b") avant d'aller
# jusqu'à Supabase, pas à valider rigoureusement une adresse.
_MOTIF_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utilisateur_connecte() -> bool:
    return bool(st.session_state.get("auth_connecte"))


def est_admin() -> bool:
    return st.session_state.get("auth_role") == "admin"


def campagnes_autorisees() -> list[str]:
    """Campagnes accessibles au compte connecté. Vide pour un admin (aucune
    restriction)."""
    return st.session_state.get("auth_campagnes") or []


def deconnexion() -> None:
    for cle in _CLES_SESSION:
        st.session_state.pop(cle, None)
    st.rerun()


def _connecter(email: str, mot_de_passe: str) -> None:
    """Reprend la logique exacte de l'ancien POST /auth/login (api/main.py),
    sans émission de jeton : le résultat est stocké directement en session."""
    utilisateurs = (
        supabase.table("utilisateurs_dashboard")
        .select("*").eq("email", email).eq("actif", True).execute().data
    )
    if not utilisateurs:
        raise DataAccessError("Identifiants invalides")
    utilisateur = utilisateurs[0]

    try:
        mot_de_passe_valide = bcrypt.checkpw(
            mot_de_passe.encode("utf-8"), utilisateur["mot_de_passe_hash"].encode("utf-8")
        )
    except ValueError:
        # bcrypt >=4.0 lève ValueError (au lieu de renvoyer False) pour un
        # mot de passe > 72 octets ou un hash stocké malformé.
        mot_de_passe_valide = False
    if not mot_de_passe_valide:
        raise DataAccessError("Identifiants invalides")

    liens = (
        supabase.table("utilisateur_campagnes")
        .select("client_final").eq("utilisateur_id", utilisateur["id"]).execute().data
    )
    campagnes_autorisees_ = [l["client_final"] for l in liens]

    if utilisateur["role"] == "client" and not campagnes_autorisees_:
        raise DataAccessError("Aucune campagne associée à ce compte — contactez l'administrateur.")

    st.session_state["auth_connecte"] = True
    st.session_state["auth_role"] = utilisateur["role"]
    st.session_state["auth_campagnes"] = campagnes_autorisees_
    st.session_state["auth_email"] = utilisateur["email"]


def _creer_compte(email: str, mot_de_passe: str) -> str:
    """Reprend la logique exacte de l'ancien POST /auth/signup — crée
    TOUJOURS un compte de rôle 'client', jamais admin, sans campagne
    associée (un admin doit ensuite le rattacher via utilisateur_campagnes)."""
    if not _MOTIF_EMAIL.match(email):
        raise DataAccessError("Adresse e-mail invalide.")
    if len(mot_de_passe) < 8:
        raise DataAccessError("Le mot de passe doit contenir au moins 8 caractères")
    if len(mot_de_passe.encode("utf-8")) > 72:
        raise DataAccessError("Le mot de passe ne doit pas dépasser 72 caractères")

    existants = supabase.table("utilisateurs_dashboard").select("id").eq("email", email).execute().data
    if existants:
        raise DataAccessError("Un compte existe déjà avec cet e-mail")

    mot_de_passe_hash = bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        supabase.table("utilisateurs_dashboard").insert(
            {"email": email, "mot_de_passe_hash": mot_de_passe_hash, "role": "client", "actif": True}
        ).execute()
    except Exception as e:
        raise DataAccessError(f"Échec de la création du compte : {e}") from e

    return "Compte créé. Un administrateur doit encore vous rattacher à une campagne avant que vous puissiez vous connecter."


def _afficher_formulaire_login() -> None:
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
        _connecter(email.strip().lower(), mot_de_passe)
    except DataAccessError as e:
        st.error(str(e))
        return
    st.rerun()


def _afficher_formulaire_inscription() -> None:
    st.caption(
        "Crée ton compte Client. Un administrateur devra ensuite te rattacher "
        "à une campagne avant que la connexion soit possible."
    )
    with st.form("form_signup"):
        email = st.text_input("E-mail", key="signup_email")
        mot_de_passe = st.text_input("Mot de passe", type="password", key="signup_mdp")
        mot_de_passe_confirmation = st.text_input(
            "Confirmer le mot de passe", type="password", key="signup_mdp_confirmation"
        )
        creer = st.form_submit_button("Créer mon compte", type="primary", use_container_width=True)

    if not creer:
        return
    if not email or not mot_de_passe:
        st.warning("Merci de renseigner l'e-mail et le mot de passe.")
        return
    if mot_de_passe != mot_de_passe_confirmation:
        st.warning("Les deux mots de passe ne correspondent pas.")
        return
    if len(mot_de_passe) < 8:
        st.warning("Le mot de passe doit contenir au moins 8 caractères.")
        return

    try:
        message = _creer_compte(email.strip().lower(), mot_de_passe)
    except DataAccessError as e:
        st.error(str(e))
        return
    st.success(f"✅ {message}")


def _afficher_ecran_connexion() -> None:
    st.title("🔐 Portail ai-company")
    st.caption("ai-company · Portail Admin & Client")

    onglet_connexion, onglet_inscription = st.tabs(["Se connecter", "Créer un compte"])
    with onglet_connexion:
        _afficher_formulaire_login()
    with onglet_inscription:
        _afficher_formulaire_inscription()


def exiger_connexion() -> None:
    """À appeler en tout premier dans app.py : bloque le rendu de la page
    tant que l'utilisateur n'est pas authentifié."""
    if utilisateur_connecte():
        return
    _afficher_ecran_connexion()
    st.stop()
