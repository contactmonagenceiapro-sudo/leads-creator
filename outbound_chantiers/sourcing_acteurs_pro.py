"""
MODULE 1a — Data Sourcing : acteurs professionnels du BTP (architectes,
promoteurs, maîtres d'œuvre) via l'API SIRENE publique officielle.

Source : recherche-entreprises.api.gouv.fr — même source, publique, gratuite,
sans risque de ToS, que celle déjà utilisée par scraper_batiment.py pour les
artisans. Seule la cible change (professionnels du projet, pas particuliers).

Usage :
    python3 -m outbound_chantiers.sourcing_acteurs_pro
"""

import json
import logging
import time
from pathlib import Path

import requests

from outbound_chantiers.config import COMMUNES_CIBLES, NAF_CODES_CIBLES, SIRENE_API_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SOURCING-PRO] %(message)s")
log = logging.getLogger(__name__)

FICHIER_SORTIE = Path(__file__).parent / "acteurs_pro_bruts.json"

# Backoff/rate-limiting : l'API est publique et gratuite mais reste soumise
# à une limite de requêtes par minute (documentée par api.gouv.fr) — on reste
# volontairement en dessous plutôt que de la découvrir en production.
PAUSE_ENTRE_REQUETES_SECONDES = 0.6
NB_TENTATIVES_MAX = 3
TIMEOUT_SECONDES = 15


def interroger_sirene(commune: str, code_naf: str, page: int = 1) -> dict | None:
    """Interroge l'API SIRENE avec retries + backoff exponentiel. Ne lève
    jamais d'exception vers l'appelant : renvoie None en cas d'échec définitif
    pour que le scraping des autres communes/codes NAF puisse continuer."""
    params = {
        "q": commune,
        "activite_principale": code_naf,
        "etat_administratif": "A",  # entreprises actives uniquement
        "page": page,
        "per_page": 25,
    }
    for tentative in range(1, NB_TENTATIVES_MAX + 1):
        try:
            reponse = requests.get(SIRENE_API_URL, params=params, timeout=TIMEOUT_SECONDES)
            if reponse.status_code == 200:
                return reponse.json()
            if reponse.status_code == 429:
                attente = 2 ** tentative
                log.warning(f"Rate-limit atteint (429) sur {commune}/{code_naf}, pause {attente}s")
                time.sleep(attente)
                continue
            log.error(f"Réponse inattendue {reponse.status_code} pour {commune}/{code_naf}")
            return None
        except requests.exceptions.RequestException as e:
            attente = 2 ** tentative
            log.error(f"Erreur réseau ({tentative}/{NB_TENTATIVES_MAX}) sur {commune}/{code_naf} : {e}")
            time.sleep(attente)
    log.error(f"Échec définitif pour {commune}/{code_naf} après {NB_TENTATIVES_MAX} tentatives")
    return None


def extraire_champs_utiles(resultat: dict, type_acteur: str) -> dict:
    siege = resultat.get("siege", {}) or {}
    return {
        "type_acteur": type_acteur,
        "nom_entreprise": resultat.get("nom_complet") or resultat.get("nom_raison_sociale"),
        "siren": resultat.get("siren"),
        "code_naf": resultat.get("activite_principale"),
        "commune": siege.get("libelle_commune"),
        "code_postal": siege.get("code_postal"),
        "adresse": siege.get("adresse"),
        "date_creation": resultat.get("date_creation"),
    }


def sourcer_acteurs_pro() -> list[dict]:
    """Parcourt chaque commune cible × chaque code NAF professionnel et
    agrège les résultats bruts (avant filtrage/enrichissement, module 2)."""
    acteurs = []
    vus = set()  # dédoublonnage par SIREN au sein d'un même run
    nb_requetes = 0
    nb_echecs = 0

    for type_acteur, codes_naf in NAF_CODES_CIBLES.items():
        for code_naf in codes_naf:
            for commune in COMMUNES_CIBLES:
                nb_requetes += 1
                data = interroger_sirene(commune, code_naf)
                time.sleep(PAUSE_ENTRE_REQUETES_SECONDES)
                if not data:
                    nb_echecs += 1
                    continue

                for resultat in data.get("results", []):
                    siren = resultat.get("siren")
                    if not siren or siren in vus:
                        continue
                    vus.add(siren)
                    acteurs.append(extraire_champs_utiles(resultat, type_acteur))

                log.info(
                    f"{type_acteur} / {code_naf} / {commune} : "
                    f"{len(data.get('results', []))} résultat(s) bruts"
                )

    # Garde-fou : si (quasi-)toutes les requêtes échouent, c'est un problème
    # systémique (mauvais format de code NAF, API indisponible, quota...),
    # pas juste "aucun résultat" — sans ce signal explicite, ce cas passe
    # inaperçu (chaque étape en aval se termine "avec succès" sur zéro
    # donnée, cf. incident réel : codes NAF envoyés sans le point exigé par
    # l'API, 100% de 400 pendant tout le run, jamais détecté avant de
    # vérifier le dashboard).
    if nb_requetes and nb_echecs / nb_requetes > 0.5:
        log.warning(
            f"{nb_echecs}/{nb_requetes} requêtes SIRENE ont échoué durant ce run — "
            "vérifier le format des codes NAF (config.py, format pointé ex: '71.11Z') "
            "et la disponibilité de l'API avant de considérer ce run comme fiable."
        )

    log.info(f"Sourcing terminé : {len(acteurs)} acteurs professionnels uniques collectés")
    return acteurs


if __name__ == "__main__":
    acteurs = sourcer_acteurs_pro()
    FICHIER_SORTIE.write_text(json.dumps(acteurs, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Résultats bruts écrits dans {FICHIER_SORTIE}")
