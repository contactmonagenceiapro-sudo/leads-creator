"""
Récupération dynamique de l'URL publique ngrok active.

Problème résolu : PUBLIC_APP_URL (mail_processor.py, email_tracking.py) est
une valeur figée dans .env, alors que l'URL d'un tunnel ngrok gratuit change
à CHAQUE redémarrage du tunnel — sans ce module, .env devient obsolète et les
liens (présentation, intake, pixel de tracking) envoyés aux prospects
cassent silencieusement jusqu'à ce qu'un humain remette .env à jour à la main.

Fonctionnement : interroge l'API locale exposée par l'agent ngrok lui-même
(http://127.0.0.1:4040/api/tunnels par défaut, tant qu'il tourne) pour lire
l'URL https ACTIVE du tunnel. Si l'agent n'est pas joignable (tunnel arrêté,
production sans ngrok où seul un vrai domaine doit être utilisé), on retombe
silencieusement sur la valeur statique fournie en repli par l'appelant
(PUBLIC_APP_URL) — jamais d'erreur, jamais de lien cassé faute de mieux.

DOCKER : ngrok tourne sur la machine hôte (aucun service ngrok dans
docker-compose.yml) ; depuis le conteneur "api", 127.0.0.1:4040 désignerait
le conteneur lui-même, jamais l'hôte (même piège que OLLAMA_HOST — voir
docker-compose.yml). NGROK_API_URL y est donc surchargée vers
http://host.docker.internal:4040/api/tunnels (extra_hosts déjà ajouté).
"""

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

NGROK_API_URL = os.getenv("NGROK_API_URL", "http://127.0.0.1:4040/api/tunnels")

# Rafraîchi au plus une fois toutes les _CACHE_TTL_SECONDES : un envoi en
# lot (relances, campagne CEO) ne doit pas interroger l'agent ngrok à chaque
# e-mail — le tunnel ne change de toute façon jamais en cours de route.
_CACHE_TTL_SECONDES = 30

_cache_url: str | None = None
_cache_expire_a: float = 0.0


def _interroger_ngrok() -> str | None:
    """Lit le premier tunnel https actif (ou, à défaut, le premier tunnel
    quel que soit son protocole) depuis l'API locale ngrok. None si l'agent
    n'est pas joignable, répond mal, ou n'expose aucun tunnel."""
    try:
        reponse = requests.get(NGROK_API_URL, timeout=2)
        reponse.raise_for_status()
        tunnels = reponse.json().get("tunnels", [])
    except (requests.exceptions.RequestException, ValueError):
        return None

    if not tunnels:
        return None

    tunnel_https = next((t for t in tunnels if t.get("proto") == "https"), None)
    tunnel = tunnel_https or tunnels[0]
    return (tunnel.get("public_url") or "").rstrip("/") or None


def obtenir_url_publique(repli: str) -> str:
    """URL publique à utiliser dans un lien envoyé à un prospect : l'URL
    ngrok active si l'agent ngrok est joignable, sinon `repli` (typiquement
    PUBLIC_APP_URL — seule option valable en production, où aucun agent
    ngrok ne tourne et où un vrai domaine doit être configuré)."""
    global _cache_url, _cache_expire_a

    maintenant = time.monotonic()
    if maintenant >= _cache_expire_a:
        nouvelle_url = _interroger_ngrok()
        if nouvelle_url != _cache_url:
            if nouvelle_url:
                log.info(f"URL publique ngrok active détectée : {nouvelle_url}")
            elif _cache_url:
                log.info(f"Agent ngrok injoignable sur {NGROK_API_URL} — repli sur l'URL statique.")
        _cache_url = nouvelle_url
        _cache_expire_a = maintenant + _CACHE_TTL_SECONDES

    return _cache_url or repli
