"""
MODULE 1b — Signal d'activité chantiers (agrégé, PAS de données nominatives).

Objectif : pondérer le score des acteurs professionnels (module 2/3) selon
le dynamisme réel de la construction/rénovation dans leur commune, à partir
de statistiques AGRÉGÉES de permis de construire (nombre de permis déposés
par commune sur les 24 derniers mois) — jamais le détail nominatif d'un
permis individuel, qui ne doit jamais être exploité pour identifier un
particulier.

Source : SDES / base Sitadel3, jeu national "Liste des autorisations
d'urbanisme créant des logements" (permis de construire + déclarations
préalables), diffusé via l'API Dido : https://data.statistiques.developpement-durable.gouv.fr
Couverture NATIONALE (pas limitée à une métropole), mise à jour mensuelle,
aucune clé API requise — cohérent avec le principe multi-client de ce
département (voir outbound_chantiers/config.py).

data.grandlyon.com a été écarté après vérification : son ancienne API
OpenDataSoft (supposée par une version antérieure de ce module) a été
démantelée, et sa remplaçante ne publie les permis que commune par commune,
pour une poignée de communes seulement (pas Lyon, Villeurbanne ni Oullins) —
inutilisable pour ce signal.

L'API Dido renvoie un CSV (délimiteur ';') et permet de filtrer côté serveur
par commune (champ COMM = code INSEE) et par date. On ne demande ici que les
colonnes COMM et DATE_REELLE_AUTORISATION, filtrées sur la fenêtre récente
(FENETRE_ACTIVITE_JOURS) : le nombre de lignes obtenu par code INSEE est le
volume de permis récents de cette commune.

Les acteurs stockent un NOM de commune (ex: "LYON", "VILLEURBANNE"), pas un
code INSEE — la résolution nom -> code passe par l'API publique et gratuite
geo.api.gouv.fr (aucune ville n'est codée en dur ici). Les arrondissements de
Lyon/Marseille/Paris (ex: "LYON 1ER") ne sont pas reconnus tels quels par
cette API et sont de toute façon agrégés par Sitadel sous le code de la
ville-mère : on les y ramène avant résolution.
"""

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import requests

from outbound_chantiers.config import COMMUNES_CIBLES, OPENDATA_BASE_URL, OPENDATA_DATASET_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIGNAL-CHANTIERS] %(message)s")
log = logging.getLogger(__name__)

TIMEOUT_SECONDES = 30
SCORE_NEUTRE_PAR_DEFAUT = 0.5

# ~24 mois de recul : lisse les variations saisonnières du dépôt de permis
# tout en restant représentatif du dynamisme "actuel" d'une commune.
FENETRE_ACTIVITE_JOURS = 730

GEO_API_COMMUNES_URL = "https://geo.api.gouv.fr/communes"
_VILLES_A_ARRONDISSEMENTS = ("LYON", "MARSEILLE", "PARIS")

# Résolution commune -> code INSEE mise en cache pour tout le run (un même
# nom de commune revient pour de nombreux acteurs) : évite de réinterroger
# geo.api.gouv.fr à chaque acteur scoré.
_cache_codes_insee: dict[str, str | None] = {}


def _nom_commune_normalise(commune: str) -> str:
    commune = (commune or "").strip().upper()
    for ville in _VILLES_A_ARRONDISSEMENTS:
        if commune == ville or commune.startswith(ville + " "):
            return ville
    return commune


def _code_insee_pour_commune(commune: str) -> str | None:
    """Résout un nom de commune vers son code INSEE via geo.api.gouv.fr
    (générique, aucune ville en dur). Ne lève jamais d'exception : renvoie
    None si la résolution échoue, à charge de l'appelant de retomber sur le
    score neutre."""
    nom = _nom_commune_normalise(commune)
    if not nom:
        return None
    if nom in _cache_codes_insee:
        return _cache_codes_insee[nom]

    code = None
    try:
        reponse = requests.get(
            GEO_API_COMMUNES_URL,
            params={"nom": nom, "fields": "code", "boost": "population", "limit": 1},
            timeout=TIMEOUT_SECONDES,
        )
        reponse.raise_for_status()
        resultats = reponse.json()
        if resultats:
            code = resultats[0]["code"]
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        log.warning(f"Résolution du code INSEE impossible pour la commune '{commune}' ({e}).")

    _cache_codes_insee[nom] = code
    return code


def recuperer_activite_par_commune() -> dict[str, float]:
    """Renvoie un score d'activité chantiers normalisé (0 à 1) par CODE
    INSEE de commune, basé sur le volume de permis de construire/
    déclarations préalables déposés sur les FENETRE_ACTIVITE_JOURS derniers
    jours, PARMI LES COMMUNES CIBLÉES PAR LA CAMPAGNE ACTIVE (config.py ::
    COMMUNES_CIBLES — jamais une ville en dur ici). Normaliser sur ce seul
    périmètre (plutôt que sur la France entière) donne un score qui reflète
    le dynamisme relatif AU SEIN de la zone visée par le client — sans ça,
    la commune la plus active de France (souvent hors zone) écraserait
    l'échelle et tasserait tous les scores du client vers le bas.
    Ne lève jamais d'exception : renvoie un dict vide si le jeu de données
    n'est pas configuré, indisponible, ou si aucune commune ciblée n'est
    résolvable, à charge de l'appelant d'utiliser le score neutre par
    défaut."""
    if not OPENDATA_DATASET_ID:
        log.warning(
            "OUTBOUND_OPENDATA_DATASET_ID non configuré : signal d'activité "
            "chantiers désactivé, score neutre appliqué à toutes les communes."
        )
        return {}

    codes_cibles = sorted({c for c in (_code_insee_pour_commune(nom) for nom in COMMUNES_CIBLES) if c})
    if not codes_cibles:
        log.warning("Aucune commune ciblée n'a pu être résolue en code INSEE — score neutre appliqué.")
        return {}

    date_min = (datetime.now(timezone.utc) - timedelta(days=FENETRE_ACTIVITE_JOURS)).strftime("%Y-%m-%d")
    params = {
        "withColumnName": "true",
        "columns": "COMM,DATE_REELLE_AUTORISATION",
        "DATE_REELLE_AUTORISATION": f"gte:{date_min}",
        "COMM": f"in:{','.join(codes_cibles)}",
    }
    try:
        reponse = requests.get(
            f"{OPENDATA_BASE_URL}/{OPENDATA_DATASET_ID}/csv", params=params, timeout=TIMEOUT_SECONDES
        )
        reponse.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error(f"Signal d'activité chantiers indisponible ({e}) : score neutre appliqué.")
        return {}

    lecteur = csv.DictReader(io.StringIO(reponse.text), delimiter=";")
    comptes_par_code: dict[str, int] = {}
    for ligne in lecteur:
        code = (ligne.get("COMM") or "").strip()
        if not code:
            continue
        comptes_par_code[code] = comptes_par_code.get(code, 0) + 1

    if not comptes_par_code:
        log.warning("Aucun permis récent trouvé dans le jeu de données — score neutre appliqué partout.")
        return {}

    volume_max = max(comptes_par_code.values())
    return {code: round(compte / volume_max, 3) for code, compte in comptes_par_code.items()}


def score_pour_commune(commune: str, activite_par_commune: dict[str, float]) -> float:
    """Score neutre (0.5) si la commune est absente du signal, non résolvable
    en code INSEE, ou si le signal est désactivé — on ne pénalise jamais un
    acteur faute de donnée, on reste neutre."""
    if not activite_par_commune:
        return SCORE_NEUTRE_PAR_DEFAUT
    code = _code_insee_pour_commune(commune)
    if code is None:
        return SCORE_NEUTRE_PAR_DEFAUT
    return activite_par_commune.get(code, SCORE_NEUTRE_PAR_DEFAUT)


if __name__ == "__main__":
    activite = recuperer_activite_par_commune()
    if activite:
        for code, score in sorted(activite.items(), key=lambda x: -x[1]):
            log.info(f"{code} : {score}")
    else:
        log.info("Aucun signal disponible — score neutre 0.5 sera appliqué partout.")
