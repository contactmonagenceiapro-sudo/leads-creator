"""
Client API pour communiquer avec le backend FastAPI de ai-company.

Toutes les routes appelées par le dashboard sont centralisées ici.
Si tes routes réelles ont un nom différent, il suffit de les adapter
dans ce fichier — le reste du dashboard n'a pas besoin de changer.
"""

import os
import requests
from typing import Any

API_BASE_URL = os.getenv("AI_COMPANY_API_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = 10  # secondes


class ApiError(Exception):
    """Erreur levée quand l'API répond mais avec un statut d'erreur,
    ou quand elle est injoignable."""
    pass


def _request(method: str, path: str, **kwargs) -> Any:
    url = f"{API_BASE_URL}{path}"
    try:
        response = requests.request(
            method, url, timeout=DEFAULT_TIMEOUT, **kwargs
        )
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
        raise ApiError(f"L'API n'a pas répondu à temps ({url}).") from exc
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text
        raise ApiError(
            f"Erreur API ({response.status_code}) sur {path} : {detail}"
        ) from exc


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


def get_contents(limit: int = 100) -> list:
    """Récupère les articles markdown locaux."""
    import glob
    import json
    contents = []
    for f in sorted(glob.glob("article*.md")):
        with open(f, "r", encoding="utf-8") as file:
            raw = file.read()
            # Si le fichier contient du JSON sérialisé, on essaie de l'extraire
            try:
                parsed = json.loads(raw)
                text = parsed.get("content", raw)
            except Exception:
                text = raw
            contents.append({"filename": f, "content": text})
    return contents

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
