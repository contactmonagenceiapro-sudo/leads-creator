"""
Configuration du département "Outbound & Génération de Chantiers" —
générique multi-clients / multi-secteurs.

La configuration métier (client, secteur, zone géographique, types
d'acteurs ciblés avec leurs codes NAF et poids) n'est PLUS codée en dur
ici : elle est gérée dans la table Supabase `campagnes`
(sql/init_campagnes.sql), éditable depuis le dashboard, et transmise à ce
processus par l'API au moment du déclenchement via la variable
d'environnement OUTBOUND_CAMPAGNE_JSON (voir
api/main.py::_construire_env_campagne). Ce fichier ne garde qu'un repli
pour un lancement CLI autonome (sans passer par l'API) et les réglages
techniques génériques (cadence, identifiants de services externes).

Aucun script du département (sourcing, enrichissement, scoring, envoi) ne
doit plus référencer un secteur, une ville ou un type d'acteur en dur : ce
serait recréer la limitation à un seul client/secteur qu'on cherche
justement à éliminer.
"""

import json
import logging
import os

from dotenv import load_dotenv

# Sans cet appel, un lancement direct de ce module (ex: python3 -m
# outbound_chantiers.pipeline_outbound_chantiers, hors docker-compose) ne
# lit jamais le .env : les os.getenv() ci-dessous retombent alors sur leurs
# valeurs par défaut historiques (ex: OLLAMA_HOST=http://ai_ollama:11434,
# qui ne résout que sur le réseau docker-compose) même si le .env définit
# une valeur différente (ex: http://localhost:11434 pour un lancement sur
# l'hôte) — cf. ceo_agent.py/lead_worker.py qui suivent déjà ce pattern.
load_dotenv()

log = logging.getLogger(__name__)

# === Repli CLI (lancement direct sans passer par l'API) ===
# Conserve la configuration S.B.G Travaux d'origine comme valeur par défaut
# historique — mais dès que l'API déclenche le run, OUTBOUND_CAMPAGNE_JSON
# prime toujours sur ce repli.
_CAMPAGNE_PAR_DEFAUT = {
    "nom_client": "S.B.G Travaux",
    "secteur": "Bâtiment - Gros Œuvre",
    "description_services": (
        "Entreprise de gros œuvre : construction, rénovation lourde, extension. "
        "Intervient comme exécutant sur les chantiers apportés par des "
        "prescripteurs (architectes, promoteurs, maîtres d'œuvre)."
    ),
    "communes_cibles": [
        "Lyon 1er", "Lyon 2e", "Lyon 3e", "Lyon 4e", "Lyon 5e", "Lyon 6e",
        "Lyon 7e", "Lyon 8e", "Lyon 9e", "Villeurbanne", "Vénissieux",
        "Saint-Priest", "Bron", "Vaulx-en-Velin", "Caluire-et-Cuire",
        "Oullins", "Rillieux-la-Pape", "Écully",
    ],
    "types_acteur_cibles": [
        {"cle": "architecte", "libelle": "Architectes", "codes_naf": ["71.11Z"], "poids": 1.0},
        {"cle": "maitre_oeuvre", "libelle": "Maîtres d'œuvre", "codes_naf": ["71.12B"], "poids": 0.7},
        {
            "cle": "promoteur", "libelle": "Promoteurs immobiliers",
            "codes_naf": ["41.10A", "41.10C", "41.10D"], "poids": 0.9,
        },
    ],
}


def _charger_campagne() -> dict:
    """Charge la campagne active depuis OUTBOUND_CAMPAGNE_JSON (injecté par
    l'API depuis la table `campagnes`), avec repli sur la configuration par
    défaut si absent ou invalide (lancement CLI autonome, ou run sans
    campagne sélectionnée)."""
    brut = os.getenv("OUTBOUND_CAMPAGNE_JSON", "")
    if not brut:
        return _CAMPAGNE_PAR_DEFAUT
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        log.error("OUTBOUND_CAMPAGNE_JSON invalide (JSON malformé) — repli sur la configuration par défaut.")
        return _CAMPAGNE_PAR_DEFAUT


_CAMPAGNE = _charger_campagne()

# === Campagne active : identité, secteur, ciblage ===
CLIENT_FINAL = _CAMPAGNE.get("nom_client") or _CAMPAGNE_PAR_DEFAUT["nom_client"]
SECTEUR = _CAMPAGNE.get("secteur") or ""
DESCRIPTION_SERVICES = _CAMPAGNE.get("description_services") or ""
COMMUNES_CIBLES = _CAMPAGNE.get("communes_cibles") or _CAMPAGNE_PAR_DEFAUT["communes_cibles"]
TYPES_ACTEUR_CIBLES = _CAMPAGNE.get("types_acteur_cibles") or _CAMPAGNE_PAR_DEFAUT["types_acteur_cibles"]

# Dérivés de TYPES_ACTEUR_CIBLES pour le reste du code (sourcing/scoring) :
# {cle: [codes_naf]}, {cle: poids}, {cle: libellé} — jamais de clé en dur
# type "architecte"/"promoteur" dans la logique elle-même.
NAF_CODES_CIBLES = {t["cle"]: t.get("codes_naf", []) for t in TYPES_ACTEUR_CIBLES if t.get("cle")}
POIDS_TYPE_ACTEUR = {t["cle"]: t.get("poids", 0.5) for t in TYPES_ACTEUR_CIBLES if t.get("cle")}
LIBELLES_TYPE_ACTEUR = {t["cle"]: t.get("libelle", t["cle"]) for t in TYPES_ACTEUR_CIBLES if t.get("cle")}

# Conservé pour compatibilité (utilisé par les tests manuels / anciens scripts).
COMMUNES_CIBLES_PAR_DEFAUT = _CAMPAGNE_PAR_DEFAUT["communes_cibles"]

# === Source SIRENE (identique à scraper_batiment.py — API publique officielle) ===
SIRENE_API_URL = "https://recherche-entreprises.api.gouv.fr/search"

# === Signal d'activité "permis de construire" (agrégé, PAS de données nominatives) ===
# À CONFIRMER avant mise en production : visiter data.grandlyon.com, rechercher
# "permis de construire" / "autorisations d'urbanisme", ouvrir le jeu de
# données pertinent, section "API" -> copier l'identifiant de dataset et
# l'URL de base (portails OpenDataSoft : forme
# https://<portail>/api/records/1.0/search/?dataset=<id>).
# Alternative si rien de pertinent n'est publié pour la Métropole de Lyon :
# le jeu de données national Sitadel sur data.gouv.fr (moins granulaire,
# mais couvre tout le territoire).
OPENDATA_BASE_URL = os.getenv("OUTBOUND_OPENDATA_BASE_URL", "https://data.grandlyon.com/api/records/1.0/search/")
OPENDATA_DATASET_ID = os.getenv("OUTBOUND_OPENDATA_DATASET_ID", "")  # vide = signal désactivé, score neutre

# === Pondération du score final (module 2/3) ===
POIDS_ACTIVITE_CHANTIERS = 0.5  # poids du signal d'activité locale dans le score final

# Seuil (score_final, 0 à 1) au-delà duquel un acteur nouvellement publié
# déclenche une alerte temps réel (Discord + e-mail, voir scorer_et_publier.py
# et alertes.py). MÊME variable d'environnement que api/main.py (qui l'utilise
# pour le KPI "leads_ultra_qualifies" de /campagnes/{x}/stats) — les deux
# doivent rester synchronisés, d'où le nom partagé plutôt que deux réglages
# indépendants qui pourraient diverger.
SEUIL_LEAD_ULTRA_QUALIFIE = float(os.getenv("SEUIL_LEAD_ULTRA_QUALIFIE", "0.85"))

# === Cadence de relance (module 4) — J+3 puis J+7 par défaut ===
DELAI_PREMIERE_RELANCE_JOURS = int(os.getenv("OUTBOUND_DELAI_RELANCE_1", "3"))
DELAI_RELANCE_SUIVANTE_JOURS = int(os.getenv("OUTBOUND_DELAI_RELANCE_2", "7"))
MAX_RELANCES = int(os.getenv("OUTBOUND_MAX_RELANCES", "2"))

# === Montée en charge progressive du volume d'envoi (warmup, module 4) ===
# La boîte d'envoi (ZOHO_USER) est PARTAGÉE par toutes les campagnes B2B —
# ce plafond protège la réputation de cette boîte/ce domaine dans son
# ensemble, pas celle d'une seule campagne (voir
# outbound_pro_btp.py::statut_ramp_warmup, qui agrège tous les
# client_final). Paliers (jours écoulés depuis le tout premier envoi B2B
# jamais effectué, plafond quotidien) — valeurs de départ raisonnables pour
# un domaine encore jeune en prospection à froid, à ajuster selon les
# retours de délivrabilité réels (taux de bounce/plainte, cf. dashboard
# "Délivrabilité"). Complète un warmup tiers (Instantly/Lemwarm/...) — ne
# le remplace pas : un warmup actif construit la réputation, ce plafond
# évite d'envoyer un volume réel disproportionné pendant que cette
# réputation se construit encore.
PALIERS_RAMP_ENVOI_QUOTIDIEN = [
    (0, 5),    # jours 1-3
    (3, 10),   # jours 4-7
    (7, 20),   # semaine 2
    (14, 30),  # semaine 3
    (21, 50),  # semaine 4 et au-delà (plateau)
]


def plafond_envoi_du_jour(jours_ecoules: int) -> int:
    """Plafond d'e-mails de prospection B2B (premier contact + relances
    confondus) autorisé aujourd'hui, selon le palier de montée en charge
    atteint. jours_ecoules=0 le jour du tout premier envoi B2B jamais
    effectué (voir PALIERS_RAMP_ENVOI_QUOTIDIEN)."""
    plafond = PALIERS_RAMP_ENVOI_QUOTIDIEN[0][1]
    for seuil_jours, valeur in PALIERS_RAMP_ENVOI_QUOTIDIEN:
        if jours_ecoules >= seuil_jours:
            plafond = valeur
    return plafond

# === API interne (réutilise l'API existante : /leads_pro, /campagnes, clé partagée) ===
API_URL = os.getenv("API_URL", "http://localhost:8000")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

# === Sourcing SIRENE (module 1a) — pagination et objectif de nouveauté ===
# Nombre d'acteurs INÉDITS (jamais vus en base pour cette campagne) visés
# par segment (commune x code NAF) avant de passer au suivant — évite de
# épuiser inutilement le quota de pages sur un segment déjà "mûr" une fois
# l'objectif atteint, tout en creusant au-delà de la page 1 quand les
# résultats déjà connus dominent (cf. incident réel : sourcing systématique
# des mêmes ~2 acteurs, page 1 uniquement, jamais paginé).
NB_NOUVEAUX_SOUHAITES_PAR_SEGMENT = int(os.getenv("OUTBOUND_NB_NOUVEAUX_PAR_SEGMENT", "10"))
# Plafond de pages SIRENE interrogées par segment (25 résultats/page) — garde-fou
# de temps/quota, pas une limite métier : un segment avec peu de nouveauté
# s'arrête de lui-même bien avant (total_pages atteint ou objectif rempli).
MAX_PAGES_PAR_SEGMENT = int(os.getenv("OUTBOUND_MAX_PAGES_PAR_SEGMENT", "5"))

# === Envoi (réutilise le compte Zoho existant, cf. ceo_agent.py) ===
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# === IA locale (réutilise Ollama, cf. lead_worker.py/ceo_agent.py) — pour la
# génération de pitch personnalisé par secteur (module 4) ===
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ai_ollama:11434")
OLLAMA_MODEL_MAIN = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "90"))
