"""
MODULE 1b — Signal d'activité chantiers (agrégé, PAS de données nominatives).

Objectif : pondérer le score des acteurs professionnels (module 2/3) selon
le dynamisme réel de la construction/rénovation dans leur commune, à partir
de statistiques AGRÉGÉES de permis de construire (nombre de permis déposés
par commune sur une période) — jamais le détail nominatif d'un permis
individuel, qui ne doit jamais être exploité pour identifier un particulier.

Beaucoup de portails open data locaux français (dont, potentiellement,
data.grandlyon.com) utilisent la plateforme OpenDataSoft, dont l'API REST
suit un format stable et documenté :
    GET {base_url}?dataset={dataset_id}&facet=commune&rows=0
    (rows=0 : on ne demande que les facettes agrégées, pas les enregistrements
    individuels — c'est délibéré, on ne veut QUE l'agrégat)

À CONFIRMER avant mise en production (voir outbound_chantiers/config.py) :
- OUTBOUND_OPENDATA_BASE_URL et OUTBOUND_OPENDATA_DATASET_ID doivent pointer
  vers un vrai jeu de données "permis de construire / autorisations
  d'urbanisme" couvrant la zone ciblée. Sans ces variables configurées, ce
  module renvoie un score neutre (0.5 partout) plutôt que d'échouer — il ne
  bloque jamais le pipeline en attendant.
"""

import logging

import requests

from outbound_chantiers.config import OPENDATA_BASE_URL, OPENDATA_DATASET_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIGNAL-CHANTIERS] %(message)s")
log = logging.getLogger(__name__)

TIMEOUT_SECONDES = 15
SCORE_NEUTRE_PAR_DEFAUT = 0.5


def recuperer_activite_par_commune() -> dict[str, float]:
    """Renvoie un score d'activité chantiers normalisé (0 à 1) par commune,
    basé sur le volume de permis déposés récemment. Ne lève jamais
    d'exception : renvoie un dict vide si le jeu de données n'est pas
    configuré ou indisponible, à charge de l'appelant d'utiliser le score
    neutre par défaut."""
    if not OPENDATA_DATASET_ID:
        log.warning(
            "OUTBOUND_OPENDATA_DATASET_ID non configuré : signal d'activité "
            "chantiers désactivé, score neutre appliqué à toutes les communes."
        )
        return {}

    params = {"dataset": OPENDATA_DATASET_ID, "facet": "commune", "rows": 0}
    try:
        reponse = requests.get(OPENDATA_BASE_URL, params=params, timeout=TIMEOUT_SECONDES)
        reponse.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error(f"Signal d'activité chantiers indisponible ({e}) : score neutre appliqué.")
        return {}

    payload = reponse.json()
    facettes = payload.get("facet_groups", [])
    facette_commune = next((f for f in facettes if f.get("name") == "commune"), None)
    if not facette_commune:
        log.warning("Facette 'commune' absente de la réponse — vérifier le nom du champ dans le dataset réel.")
        return {}

    comptes = {f["name"]: f["count"] for f in facette_commune.get("facets", [])}
    if not comptes:
        return {}

    volume_max = max(comptes.values())
    return {commune: round(compte / volume_max, 3) for commune, compte in comptes.items()}


def score_pour_commune(commune: str, activite_par_commune: dict[str, float]) -> float:
    """Score neutre (0.5) si la commune est absente du signal — on ne
    pénalise jamais un acteur faute de donnée, on reste neutre."""
    return activite_par_commune.get(commune, SCORE_NEUTRE_PAR_DEFAUT)


if __name__ == "__main__":
    activite = recuperer_activite_par_commune()
    if activite:
        for commune, score in sorted(activite.items(), key=lambda x: -x[1]):
            log.info(f"{commune} : {score}")
    else:
        log.info("Aucun signal disponible — score neutre 0.5 sera appliqué partout.")
