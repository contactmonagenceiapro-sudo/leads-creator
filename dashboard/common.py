"""
Fonctions partagées entre les pages du dashboard ai-company
(app.py, app_pages/sourcing.py, app_pages/gestion_clients.py).
"""

import time

import pandas as pd
import streamlit as st

from api_client import ApiError, get_agent_status

# Durée maximale pendant laquelle afficher_suivi() bloque le script Streamlit
# (donc toute l'interface, le temps est partagé par session) en interrogeant
# /agent/status en boucle. Au-delà, elle rend systématiquement la main à
# l'utilisateur avec un message clair + un bouton pour reprendre le suivi,
# plutôt que de le laisser face à une page figée pendant tout timeout_secondes
# (jusqu'à 900s / 15 min pour le Pipeline Automatique).
DUREE_MAX_BLOCAGE_SECONDES = 60


def safe_call(fn, *args, **kwargs):
    """Exécute un appel API et renvoie (résultat, erreur) sans faire
    planter la page en cas de souci côté API."""
    try:
        return fn(*args, **kwargs), None
    except ApiError as e:
        return None, str(e)


def executer_avec_spinner(libelle_spinner: str, fn, *args, **kwargs):
    """Comme safe_call(), avec un spinner Streamlit affiché pendant l'appel
    (retour visuel immédiat) ET un filet de sécurité total : au-delà des
    erreurs API connues (ApiError, déjà gérées par safe_call), toute
    exception réellement inattendue est elle aussi transformée en message
    d'erreur plutôt que de faire planter la page — aucun bouton d'action ne
    doit pouvoir laisser l'utilisateur bloqué sans retour ni explication."""
    try:
        with st.spinner(libelle_spinner):
            return safe_call(fn, *args, **kwargs)
    except Exception as e:
        return None, f"Erreur inattendue : {e}"


def _cle_suivi(action: str, campagne: str | None) -> str:
    return f"_suivi_tache::{action}::{campagne or '_default_'}"


def demarrer_suivi(action: str, campagne: str | None = None) -> None:
    """À appeler juste après un déclenchement réussi de trigger_agent_action
    (bouton cliqué) : marque la tâche comme 'à suivre'. La progression
    elle-même s'affiche ensuite via afficher_suivi(), appelée SANS condition
    sur le clic du bouton — y compris après un rerun déclenché par le bouton
    "Vérifier l'avancement" d'afficher_suivi elle-même, ou par n'importe
    quelle autre interaction sur la page pendant que la tâche tourne."""
    st.session_state[_cle_suivi(action, campagne)] = time.monotonic()


def afficher_suivi(
    action: str, estimation_secondes: int, libelle: str, timeout_secondes: int = 600, campagne: str | None = None
) -> None:
    """Affiche une barre de progression + un statut mis à jour en direct
    tant qu'une tâche lancée via trigger_agent_action() (subprocess côté
    API) est en cours, en interrogeant /agent/status toutes les 2 secondes.
    N'affiche rien si demarrer_suivi() n'a pas été appelé pour cette
    action/campagne (permet de placer cet appel de façon inconditionnelle
    dans le script, indépendamment du bloc `if st.button(...)` qui a pu
    déclencher la tâche).

    La progression est une ESTIMATION (on ne connaît pas la durée exacte à
    l'avance) plafonnée à 95% tant que l'API n'a pas confirmé que la tâche
    est réellement terminée — elle ne saute à 100% qu'à ce moment-là, pour
    ne jamais afficher "terminé" avant que ce soit vraiment le cas.

    Le blocage effectif du script est plafonné à DUREE_MAX_BLOCAGE_SECONDES
    par exécution : au-delà, la main est rendue à l'utilisateur (message +
    bouton "Vérifier l'avancement") même si la tâche continue côté serveur —
    jamais une page figée pendant tout timeout_secondes."""
    cle = _cle_suivi(action, campagne)
    debut = st.session_state.get(cle)
    if debut is None:
        return

    barre = st.progress(0, text=f"{libelle} — démarrage...")
    zone_statut = st.empty()
    intervalle_secondes = 2
    fin_tranche = time.monotonic() + DUREE_MAX_BLOCAGE_SECONDES

    while True:
        ecoule = time.monotonic() - debut

        try:
            statut = get_agent_status(action, campagne=campagne)
        except Exception as e:
            # Filet de sécurité total (pas seulement ApiError) : une panne
            # de suivi ne doit jamais bloquer la page ni la faire planter —
            # seulement arrêter le suivi avec un message clair.
            barre.empty()
            zone_statut.error(f"Impossible de suivre l'avancement : {e}")
            st.session_state.pop(cle, None)
            return

        etat = statut.get("state") if isinstance(statut, dict) else None

        if etat == "termine":
            barre.progress(100, text=f"{libelle} — terminé")
            zone_statut.success(f"✅ {libelle} terminé avec succès en {int(ecoule)} secondes.")
            st.session_state.pop(cle, None)
            return

        if etat == "erreur":
            barre.progress(100, text=f"{libelle} — erreur")
            zone_statut.error(
                f"❌ {libelle} s'est arrêté avec une erreur (code {statut.get('returncode')}) "
                f"après {int(ecoule)} secondes. Consulte `docker logs ai_api` pour le détail."
            )
            st.session_state.pop(cle, None)
            return

        if ecoule > timeout_secondes:
            barre.empty()
            zone_statut.warning(
                f"⏳ {libelle} tourne depuis plus de {timeout_secondes // 60} minutes — "
                "plus long que prévu, mais pas forcément anormal sur un premier run avec "
                "beaucoup de résultats. Reviens vérifier plus tard (les données apparaîtront "
                "automatiquement une fois le traitement terminé)."
            )
            st.session_state.pop(cle, None)
            return

        if time.monotonic() > fin_tranche:
            # Le script Streamlit (donc l'interface, le temps de cette
            # exécution) ne reste JAMAIS bloqué plus de DUREE_MAX_BLOCAGE_SECONDES
            # d'affilée : la tâche continue côté serveur, mais l'utilisateur
            # reprend la main ici plutôt que de fixer une barre de progression
            # pendant plusieurs minutes.
            barre.empty()
            zone_statut.info(
                f"⏳ {libelle} continue en arrière-plan côté serveur (déjà {int(ecoule)}s, "
                f"estimation habituelle ~{estimation_secondes}s) — l'interface n'est pas "
                "bloquée : navigue ailleurs si besoin, ou clique ci-dessous pour vérifier "
                "l'avancement."
            )
            st.button(f"🔄 Vérifier l'avancement — {libelle}", key=f"verif_{cle}")
            return

        pourcentage = min(int((ecoule / estimation_secondes) * 95), 95)
        barre.progress(pourcentage, text=f"{libelle} — en cours...")
        zone_statut.caption(
            f"⏱️ En cours depuis {int(ecoule)}s (estimation habituelle : ~{estimation_secondes}s)..."
        )
        time.sleep(intervalle_secondes)


def to_dataframe(data) -> pd.DataFrame:
    if not data:
        return pd.DataFrame()
    if isinstance(data, dict):
        # Les endpoints qui renvoient une liste l'enveloppent sous une seule
        # clé (ex: {"leads": [...]}, {"leads_pro": [...]}) — on la déballe
        # quel que soit son nom, plutôt que de ne gérer que "items".
        if "items" in data:
            data = data["items"]
        elif len(data) == 1:
            data = next(iter(data.values()))
    return pd.DataFrame(data)
