"""
Client API pour communiquer avec le backend FastAPI de ai-company.

Toutes les routes appelées par le dashboard sont centralisées ici.
Si tes routes réelles ont un nom différent, il suffit de les adapter
dans ce fichier — le reste du dashboard n'a pas besoin de changer.

Portail Client (voir dashboard/auth.py) : toutes les requêtes authentifiées
utilisent le jeton JWT stocké dans st.session_state["auth_token"] (obtenu via
login()), envoyé en Authorization: Bearer. Il n'y a plus de clé API statique
côté dashboard — admin comme client passent par le même écran de connexion.
"""

import os
import requests
from typing import Any

try:
    import streamlit as st
except ImportError:
    st = None

# Charge .env si présent (utile quand le dashboard tourne hors Docker, via
# `streamlit run app.py` directement sur le poste).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_BASE_URL = os.getenv("AI_COMPANY_API_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = 10  # secondes


class ApiError(Exception):
    """Erreur levée quand l'API répond mais avec un statut d'erreur,
    ou quand elle est injoignable."""
    pass


def login(email: str, mot_de_passe: str) -> dict:
    """POST /auth/login -> {token, role, campagnes, email, expire_a}.

    Seul appel de ce module qui ne requiert aucune authentification
    préalable (c'est justement lui qui l'établit) — voir dashboard/auth.py."""
    url = f"{API_BASE_URL}/auth/login"
    try:
        response = requests.post(
            url, json={"email": email, "mot_de_passe": mot_de_passe}, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError as exc:
        raise ApiError(
            f"Impossible de joindre l'API sur {API_BASE_URL}. Vérifie qu'elle tourne bien."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ApiError(f"L'API n'a pas répondu à temps ({url}).") from exc
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text
        raise ApiError(detail or "Identifiants invalides")


def _obtenir_jeton() -> str:
    """Lit le jeton de session posé par dashboard/auth.py après login().
    Ne lève jamais d'exception (retourne "" si aucune session Streamlit)."""
    if st is None:
        return ""
    try:
        return st.session_state.get("auth_token", "")
    except Exception:
        return ""


def _request(method: str, path: str, **kwargs) -> Any:
    url = f"{API_BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    # Chaque appel peut demander un délai plus long que DEFAULT_TIMEOUT (ex:
    # enrichir_lead_pro, qui fait de vraies requêtes HTTP vers des sites
    # tiers côté API) — sans ça, le dashboard couperait la connexion et
    # afficherait une fausse erreur alors que le traitement continue et
    # réussit côté serveur. DEFAULT_TIMEOUT reste la valeur stricte par
    # défaut pour tous les autres appels (déclenchement d'actions en tâche
    # de fond, lectures, écritures courtes).
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    jeton = _obtenir_jeton()
    if not jeton:
        raise ApiError("Aucune session active — reconnecte-toi.")
    headers["Authorization"] = f"Bearer {jeton}"
    try:
        response = requests.request(
            method, url, timeout=timeout, headers=headers, **kwargs
        )
        if response.status_code == 401 and st is not None:
            # Session expirée ou invalidée côté serveur : on efface le jeton
            # pour que la prochaine page affiche à nouveau l'écran de
            # connexion (voir dashboard/auth.py::exiger_connexion), plutôt
            # que de continuer à renvoyer des erreurs 401 en boucle.
            st.session_state.pop("auth_token", None)
        response.raise_for_status()
        if response.content:
            return response.json()
        return None
    except requests.exceptions.ConnectionError as exc:
        raise ApiError(
            f"Impossible de joindre l'API sur {API_BASE_URL}. "
            "Vérifie qu'elle tourne bien (uvicorn ...)."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ApiError(
            f"L'API n'a pas répondu à temps ({url}, délai {timeout}s dépassé)."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text
        raise ApiError(
            f"Erreur API ({response.status_code}) sur {path} : {detail}"
        ) from exc
    except ApiError:
        raise
    except Exception as exc:
        # Filet de sécurité final : une réponse inattendue (JSON malformé,
        # etc.) ne doit jamais faire planter toute la page dashboard —
        # transformée en ApiError comme le reste, gérée uniformément par
        # safe_call()/executer_avec_spinner() côté pages.
        raise ApiError(f"Réponse inattendue de l'API sur {path} : {exc}") from exc


# ---------------------------------------------------------------------
# Endpoints — adapte les chemins ici si tes routes réelles diffèrent
# ---------------------------------------------------------------------

def get_stats() -> dict:
    """GET /stats -> statistiques globales de la compagnie.

    Format attendu (adapte selon ta réponse réelle) :
    {
        "leads_count": int,
        "articles_count": int,
        "last_ceo_report": {"date": "...", "summary": "..."} | str | null
    }
    """
    return _request("GET", "/stats")


def get_leads(limit: int = 100) -> list:
    """GET /leads -> liste des leads générés."""
    return _request("GET", "/leads", params={"limit": limit})


def get_leads_pro(client_final: str = "S.B.G Travaux") -> list:
    """GET /leads_pro -> acteurs professionnels (architectes, promoteurs,
    maîtres d'œuvre) sourcés pour ce client final (département outbound_chantiers/)."""
    return _request("GET", "/leads_pro", params={"client_final": client_final})


def get_contents(limit: int = 100) -> list:
    """GET /contents -> articles générés, via l'API (et non le disque local :
    le dashboard peut tourner sur une autre machine que le conteneur api)."""
    return _request("GET", "/contents")


def get_health() -> dict:
    """GET /health -> état réel des dépendances (Supabase, Ollama, Zoho, Discord)."""
    return _request("GET", "/health")


def get_campagnes() -> dict:
    """GET /campagnes -> liste des campagnes/clients configurés (nom_client,
    secteur, description_services, communes_cibles, types_acteur_cibles,
    statut) — plateforme multi-clients/multi-secteurs, département
    outbound_chantiers/."""
    return _request("GET", "/campagnes")


def creer_ou_modifier_campagne(campagne: dict) -> dict:
    """POST /campagnes -> crée ou met à jour (upsert sur nom_client) une
    campagne. Champs attendus : nom_client, secteur, description_services,
    communes_cibles (liste), types_acteur_cibles (liste de
    {cle, libelle, codes_naf, poids}), statut."""
    return _request("POST", "/campagnes", json=campagne)


def get_campagne_stats(nom_client: str) -> dict:
    """GET /campagnes/{nom_client}/stats -> leads_total, contactes,
    taux_contact, opportunites pour cette campagne."""
    return _request("GET", f"/campagnes/{nom_client}/stats")


def get_agent_status(action: str, campagne: str | None = None) -> dict:
    """GET /agent/status -> état d'une tâche lancée via trigger_agent_action
    ("jamais_lance" | "en_cours" | "termine" | "erreur"), pour afficher une
    vraie progression pendant l'exécution en arrière-plan. `campagne`
    distingue les runs concurrents de plusieurs clients sous la même action."""
    params = {"action": action}
    if campagne:
        params["campagne"] = campagne
    return _request("GET", "/agent/status", params=params)


def trigger_agent_action(action: str, payload: dict | None = None) -> dict:
    """POST /agent/trigger -> déclenche une action de l'agent.

    `action` identifie l'action à lancer côté backend (ex: "run_pipeline",
    "generate_report", ...). Adapte le nom du champ / de la route à ton API.
    """
    return _request(
        "POST",
        "/agent/trigger",
        json={"action": action, "payload": payload or {}},
    )


def get_contracts() -> dict:
    """GET /contracts -> contrats artisans (devis site vitrine) avec
    l'entreprise/email du lead embarqués, pour le module Remboursements."""
    return _request("GET", "/contracts")


def get_remboursements(statut: str | None = None, client_final: str | None = None) -> dict:
    """GET /remboursements -> liste des demandes de remboursement (Stripe
    réel pour les contrats artisans, avoir commercial pour les leads B2B),
    filtrable par statut et/ou client."""
    params = {}
    if statut:
        params["statut"] = statut
    if client_final:
        params["client_final"] = client_final
    return _request("GET", "/remboursements", params=params)


def creer_remboursement(demande: dict) -> dict:
    """POST /remboursements -> crée une demande de remboursement. La
    validation d'éligibilité est automatique côté API ; l'exécution réelle
    (Stripe) reste une action séparée et délibérée (executer_remboursement)."""
    return _request("POST", "/remboursements", json=demande)


def executer_remboursement(remboursement_id: str) -> dict:
    """POST /remboursements/{id}/executer -> déclenche RÉELLEMENT le
    remboursement Stripe (mouvement d'argent réel, irréversible)."""
    return _request("POST", f"/remboursements/{remboursement_id}/executer")


def signaler_lead_pro_invalide(lead_pro_id: str, motif: str, montant_credit_centimes: int = 0) -> dict:
    """POST /leads_pro/{id}/signaler_invalide -> signale un lead B2B
    invalide/non qualifié et crée automatiquement l'avoir commercial
    correspondant (aucun paiement réel en jeu côté B2B à ce jour)."""
    return _request(
        "POST",
        f"/leads_pro/{lead_pro_id}/signaler_invalide",
        json={"motif": motif, "montant_credit_centimes": montant_credit_centimes},
    )


TIMEOUT_ENRICHISSEMENT = 45  # secondes — cet appel est SYNCHRONE côté API
# (voir api/main.py::relancer_enrichissement_lead_pro) et fait de vraies
# requêtes HTTP vers le site du lead (jusqu'à 2 pages, 5s de timeout chacune
# côté serveur, cf. outbound_chantiers/enrichir_acteurs_pro.py) : le
# DEFAULT_TIMEOUT de 10s couperait la connexion et afficherait une fausse
# erreur alors que l'enrichissement continue et réussit côté API.


def enrichir_lead_pro(lead_pro_id: str) -> dict:
    """POST /leads_pro/{id}/enrichir -> relance la recherche de contact
    (site/e-mail/téléphone/réseaux sociaux) pour ce lead précis, sans
    attendre le prochain passage du pipeline (admin uniquement)."""
    return _request("POST", f"/leads_pro/{lead_pro_id}/enrichir", timeout=TIMEOUT_ENRICHISSEMENT)


def get_email_events(type_evenement: str | None = None, client_final: str | None = None, limit: int = 50) -> dict:
    """GET /email_events -> flux d'activité e-mail (envoyé/ouvert/cliqué),
    filtrable par type d'événement et/ou campagne."""
    params: dict = {"limit": limit}
    if type_evenement:
        params["type_evenement"] = type_evenement
    if client_final:
        params["client_final"] = client_final
    return _request("GET", "/email_events", params=params)
