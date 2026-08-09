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
import os
import time
from pathlib import Path

import requests

from outbound_chantiers.config import (
    CLIENT_FINAL,
    COMMUNES_CIBLES,
    MAX_PAGES_PAR_SEGMENT,
    NAF_CODES_CIBLES,
    NB_NOUVEAUX_SOUHAITES_PAR_SEGMENT,
    SIRENE_API_URL,
)
from outbound_chantiers.signal_activite_chantiers import recuperer_activite_par_commune, score_pour_commune

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SOURCING-PRO] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

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
        # Déjà présent dans CETTE MÊME réponse SIRENE (aucun appel
        # supplémentaire) — code INSEE de tranche d'effectif salarié de
        # l'établissement (ex: "11" = 10 à 19 salariés), utilisé comme
        # critère de score "taille d'entreprise" (voir
        # scorer_et_publier.py::score_taille_entreprise).
        "tranche_effectif_salarie": siege.get("tranche_effectif_salarie"),
    }


def recuperer_sirens_deja_connus(client_final: str) -> set[str]:
    """SIREN déjà présents en base (table leads_professionnels, tous statuts
    confondus) pour cette campagne — permet au sourcing de les exclure des
    "nouveaux" et de creuser au-delà de la page 1 tant que les résultats
    restent dominés par des acteurs déjà connus, plutôt que de re-remonter
    indéfiniment les mêmes têtes de classement SIRENE (page 1 uniquement,
    déterministe d'un run à l'autre). Échec réseau -> ensemble vide : le run
    continue sans historique plutôt que d'échouer entièrement."""
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads_professionnels",
            params={"select": "siren", "client_final": f"eq.{client_final}"},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            timeout=10,
        )
        if reponse.status_code == 200:
            return {a["siren"] for a in reponse.json() if a.get("siren")}
        log.warning(f"Impossible de récupérer l'historique SIREN ({client_final}) : HTTP {reponse.status_code}")
    except requests.exceptions.RequestException as e:
        log.warning(f"Impossible de récupérer l'historique SIREN ({client_final}) : {e}")
    return set()


def sourcer_acteurs_pro() -> list[dict]:
    """Parcourt chaque commune cible x chaque code NAF professionnel
    ("segment") et agrège les résultats bruts (avant filtrage/enrichissement,
    module 2). Pagine réellement chaque segment (jusqu'à MAX_PAGES_PAR_SEGMENT)
    et exclut les SIREN déjà en base pour cette campagne, en s'arrêtant dès
    que NB_NOUVEAUX_SOUHAITES_PAR_SEGMENT nouveaux acteurs ont été trouvés
    (ou que la dernière page SIRENE du segment est atteinte) — sans ça, un
    run répété ne fait que re-confirmer la même page 1, déjà toute
    entièrement connue, sans jamais aller chercher de nouveaux prospects."""
    acteurs = []
    vus = set()  # dédoublonnage par SIREN au sein d'un même run
    deja_connus = recuperer_sirens_deja_connus(CLIENT_FINAL)
    log.info(f"{len(deja_connus)} acteur(s) déjà en base pour « {CLIENT_FINAL} » — exclus des nouveaux résultats")

    # Priorise les communes les plus dynamiques (signal module 1b, déjà
    # nécessaire pour le scoring) : à quota de pages/requêtes égal, on veut
    # que les communes à fort potentiel soient explorées EN PREMIER, plutôt
    # que de scraper toutes les communes de façon uniforme peu importe leur
    # dynamisme réel. Sans signal configuré, score_pour_commune() renvoie
    # 0.5 partout : le tri est alors stable et ne change rien à l'ordre
    # d'origine (comportement identique à avant).
    activite_par_commune = recuperer_activite_par_commune()
    communes_triees = sorted(COMMUNES_CIBLES, key=lambda c: -score_pour_commune(c, activite_par_commune))

    nb_requetes = 0
    nb_echecs = 0
    nb_reexclus_deja_connus = 0

    for type_acteur, codes_naf in NAF_CODES_CIBLES.items():
        for code_naf in codes_naf:
            for commune in communes_triees:
                nouveaux_ce_segment = 0

                for page in range(1, MAX_PAGES_PAR_SEGMENT + 1):
                    nb_requetes += 1
                    data = interroger_sirene(commune, code_naf, page=page)
                    time.sleep(PAUSE_ENTRE_REQUETES_SECONDES)
                    if not data:
                        nb_echecs += 1
                        break

                    resultats = data.get("results", [])
                    if not resultats:
                        break

                    for resultat in resultats:
                        siren = resultat.get("siren")
                        if not siren or siren in vus:
                            continue
                        vus.add(siren)
                        if siren in deja_connus:
                            nb_reexclus_deja_connus += 1
                            continue
                        acteurs.append(extraire_champs_utiles(resultat, type_acteur))
                        nouveaux_ce_segment += 1

                    log.info(
                        f"{type_acteur} / {code_naf} / {commune} / page {page} : "
                        f"{len(resultats)} résultat(s) bruts, {nouveaux_ce_segment} nouveau(x) cumulé(s)"
                    )

                    if nouveaux_ce_segment >= NB_NOUVEAUX_SOUHAITES_PAR_SEGMENT:
                        break  # objectif de nouveauté atteint pour ce segment
                    if page >= data.get("total_pages", 1):
                        break  # plus aucune page disponible côté SIRENE

    if nb_reexclus_deja_connus:
        log.info(
            f"{nb_reexclus_deja_connus} résultat(s) déjà connu(s) en base réexclus "
            "(non comptés comme nouveaux, mais comptabilisés dans les requêtes ci-dessus)"
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
