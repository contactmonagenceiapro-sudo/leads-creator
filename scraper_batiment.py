#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_batiment.py
====================

Scraper de leads BtoB cible : PME du secteur du Bâtiment (gros œuvre, second
œuvre, rénovation, électricité, plomberie, isolation) sur deux zones :
Métropole de Lyon et région Grand Est (Reims, Strasbourg, Metz, Nancy,
Troyes, Mulhouse, Colmar, Charleville-Mézières) — voir VILLES_CIBLES.

Ce script s'insère dans le pipeline `ai-company` :
    scraper_batiment.py  -->  leads.json  -->  lead_worker.py (Ollama + Supabase)

Bibliothèques requises (à installer dans le venv du projet) :
    pip install requests beautifulsoup4

Utilisation :
    source venv/bin/activate
    python scraper_batiment.py
"""

import json
import logging
import os
import random
import re
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# CONFIGURATION GÉNÉRALE
# ---------------------------------------------------------------------------

# Le fichier tampon doit se trouver à la racine du projet, au même niveau
# que lead_worker.py, pour que ce dernier puisse le consommer directement.
OUTPUT_FILE = Path(__file__).resolve().parent / "leads.json"

# Ciblage géographique précis : Métropole de Lyon — campagne commerciale
# "Done For You" à fort potentiel (2e marché BTP de France). Lyon (commune
# 69123) n'a PAS de code INSEE unique exploitable par l'API Recherche
# d'Entreprises : comme Paris/Marseille, ses 9 arrondissements municipaux
# ont chacun leur propre code commune (693XX) et c'est CE code qui est
# rattaché aux établissements dans SIRENE (vérifié en direct : code_commune
# =69123 ne renvoie presque aucun résultat, alors que 69381-69389 en
# renvoient des centaines chacun).
VILLES_LYON = [
    "Lyon 1er", "Lyon 2e", "Lyon 3e", "Lyon 4e", "Lyon 5e",
    "Lyon 6e", "Lyon 7e", "Lyon 8e", "Lyon 9e",
    # Communes limitrophes à fort volume (Métropole de Lyon), ajoutées pour
    # atteindre un volume commercial exploitable : le taux de sites web/emails
    # réels détectables sur des artisans individuels SIRENE est structurellement
    # faible (~2-3% observé), donc le volume brut doit être élevé en amont.
    "Villeurbanne", "Vénissieux", "Saint-Priest", "Bron",
    "Vaulx-en-Velin", "Caluire-et-Cuire", "Oullins-Pierre-Bénite",
    "Rillieux-la-Pape", "Écully",
]

# Zone Grand Est — décision business du 10/08 : réactivée EN PLUS de Lyon
# (jamais à la place). Zone historique (présente dans la toute première
# version de ce script), remplacée par Lyon le 28/07 pour un motif de
# volume, pas un problème technique. Contrairement à Lyon, ces communes ont
# un code INSEE standard unique, pas d'arrondissements.
VILLES_GRAND_EST = [
    "Reims", "Strasbourg", "Metz", "Nancy", "Troyes",
    "Mulhouse", "Colmar", "Charleville-Mézières",
]

# Liste à plat consommée par les boucles de scraping ci-dessous. Découpée en
# deux sous-listes nommées (au lieu d'une seule liste plate) pour que le
# dashboard puisse classer chaque lead par zone (voir
# dashboard/data_access.py::get_stats_par_zone_artisans) sans dupliquer les
# noms de communes.
VILLES_CIBLES = VILLES_LYON + VILLES_GRAND_EST

# Code commune INSEE de chaque ville ciblée (identifie une commune de façon
# unique, contrairement au code postal qui peut regrouper plusieurs communes
# ou, inversement, être partagé par plusieurs pour une même ville). Utilisé
# par la source de données ouvertes (API Recherche d'Entreprises / SIRENE).
# Codes vérifiés en direct (API géo.api.gouv.fr + tests réels sur l'API
# Recherche d'Entreprises) : Lyon nécessite les codes par arrondissement
# (693XX), voir note ci-dessus ; les autres communes utilisent leur code
# commune standard directement.
VILLES_CODE_INSEE = {
    "Lyon 1er": "69381",
    "Lyon 2e": "69382",
    "Lyon 3e": "69383",
    "Lyon 4e": "69384",
    "Lyon 5e": "69385",
    "Lyon 6e": "69386",
    "Lyon 7e": "69387",
    "Lyon 8e": "69388",
    "Lyon 9e": "69389",
    "Villeurbanne": "69266",
    "Vénissieux": "69259",
    "Saint-Priest": "69290",
    "Bron": "69029",
    "Vaulx-en-Velin": "69256",
    "Caluire-et-Cuire": "69034",
    "Oullins-Pierre-Bénite": "69149",
    "Rillieux-la-Pape": "69286",
    "Écully": "69081",
    # Grand Est — codes vérifiés en direct (API géo.api.gouv.fr).
    "Reims": "51454",
    "Strasbourg": "67482",
    "Metz": "57463",
    "Nancy": "54395",
    "Troyes": "10387",
    "Mulhouse": "68224",
    "Colmar": "68066",
    "Charleville-Mézières": "08105",
}

# Ciblage métier : (mot-clé de recherche, libellé du sous-secteur)
# Utilisé par le scraping PagesJaunes (méthode secondaire, cf. plus bas).
# Niche élargie pour la campagne commerciale : cluster bâtiment/rénovation à
# forte demande sur l'agglomération lyonnaise (tickets de prestation élevés).
MOTS_CLES_METIER = [
    ("plâtrier plaquiste", "Bâtiment - Plâtrerie"),
    ("électricien bâtiment", "Bâtiment - Électricité"),
    ("isolation rénovation énergétique", "Bâtiment - Isolation / Rénovation Énergétique"),
    ("gros œuvre maçonnerie", "Bâtiment - Gros Œuvre"),
    ("second œuvre rénovation", "Bâtiment - Second Œuvre / Rénovation"),
    ("plombier chauffagiste", "Bâtiment - Plomberie / Chauffage"),
]

# Ciblage métier : (code NAF/APE, libellé du sous-secteur). Mêmes libellés
# que MOTS_CLES_METIER pour que les deux sources produisent des leads
# homogènes. Utilisé par la source de données ouvertes (SIRENE).
SECTEURS_NAF = [
    ("43.31Z", "Bâtiment - Plâtrerie"),
    ("43.21A", "Bâtiment - Électricité"),
    ("43.29A", "Bâtiment - Isolation / Rénovation Énergétique"),
    ("43.99C", "Bâtiment - Gros Œuvre"),
    ("43.39Z", "Bâtiment - Second Œuvre / Rénovation"),
    ("43.22A", "Bâtiment - Plomberie / Chauffage"),
]

# Nombre maximum de fiches conservées par recherche (ville x métier). Relevé
# de 5 à 25 (plafond per_page usuel de l'API) pour la campagne commerciale :
# le volume disponible à Lyon est très supérieur au Grand Est (ex: 207
# résultats en plâtrerie sur le seul 3e arrondissement).
MAX_LEADS_PAR_RECHERCHE = 25

# API Recherche d'Entreprises (data.gouv.fr / INSEE-SIRENE) : source de
# données ouvertes, publique, gratuite et sans authentification. Contrairement
# au scraping d'annuaire, elle est prévue pour un usage programmatique et ne
# bloque pas les requêtes automatisées. C'est désormais la méthode PRIMAIRE
# de récupération de leads (voir recuperer_leads_open_data ci-dessous).
API_GOUV_RECHERCHE_URL = "https://recherche-entreprises.api.gouv.fr/search"

# Modèle d'URL de l'annuaire public ciblé, conservé comme méthode SECONDAIRE
# (ex: si l'API ci-dessus est un jour indisponible). La structure HTML des
# annuaires publics évolue fréquemment et intègre des protections anti-bot
# (Datadome, reCAPTCHA...) — d'où le filet de sécurité supplémentaire plus bas.
BASE_URL_ANNUAIRE = "https://www.pagesjaunes.fr/recherche/{quoi}/{ou}"

# Pool de User-Agents modernes et réalistes (rotation à chaque requête)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

# Anti-blocage : délai humain aléatoire entre chaque requête (en secondes)
DELAI_MIN = 2
DELAI_MAX = 5

# Timeout strict sur toutes les requêtes réseau
TIMEOUT_REQUETE = 10

# Rotation de proxies (optionnelle) : liste séparée par des virgules dans la
# variable d'environnement PROXIES, ex: "http://ip1:port,http://ip2:port".
# Non configuré par défaut : le scraper fonctionne sans proxy (utilise l'IP
# locale), ce qui augmente le risque de blocage anti-bot sur des volumes
# importants — à activer si PagesJaunes bloque l'IP de la machine.
PROXIES_ENV = os.getenv("PROXIES", "")
PROXIES_DISPONIBLES = [p.strip() for p in PROXIES_ENV.split(",") if p.strip()]

# Nombre d'échecs consécutifs (recherche ville x métier) au-delà duquel on
# abandonne le scraping en direct plutôt que d'épuiser les 40 combinaisons :
# un blocage anti-bot systémique se détecte en quelques essais, pas en 40.
SEUIL_ECHECS_CONSECUTIFS = 6


def alerter_degradation(message: str) -> None:
    """Alerte Discord en cas de dégradation silencieuse du pipeline (bascule
    sur le jeu de données de secours fictif). Sans cette alerte, un tel
    incident ne serait visible qu'en relisant les logs manuellement — cas
    réel vécu : un crash de recuperer_leads_open_data() (bug DNS corrigé
    depuis) était passé inaperçu jusqu'à une vérification a posteriori."""
    if not DISCORD_WEBHOOK_URL:
        logging.warning("DISCORD_WEBHOOK_URL non configuré, alerte de dégradation ignorée.")
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": f"⚠️ **Scraper Bâtiment — dégradation** \n{message}"},
            timeout=10,
        )
    except requests.exceptions.RequestException as erreur:
        logging.error(f"Échec de l'envoi de l'alerte Discord : {erreur}")


# ---------------------------------------------------------------------------
# LOGGING & TRAÇABILITÉ (préfixes visuels [*] [+] [!] [x])
# ---------------------------------------------------------------------------

class FormatteurPrefixe(logging.Formatter):
    """Formate chaque ligne de log avec un préfixe visuel selon sa gravité."""

    PREFIXES = {
        logging.DEBUG: "[*]",
        logging.INFO: "[+]",
        logging.WARNING: "[!]",
        logging.ERROR: "[x]",
        logging.CRITICAL: "[x]",
    }

    def format(self, record):
        prefixe = self.PREFIXES.get(record.levelno, "[*]")
        return f"{prefixe} {record.getMessage()}"


def configurer_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatteurPrefixe())
    racine = logging.getLogger()
    racine.setLevel(logging.DEBUG)
    racine.handlers.clear()
    racine.addHandler(handler)


# ---------------------------------------------------------------------------
# OUTILS RÉSEAU (anti-blocage, résilience)
# ---------------------------------------------------------------------------

def creer_session_resiliente() -> requests.Session:
    """Crée une session requests avec retry automatique sur erreurs transitoires
    (5xx, coupures réseau), sans jamais retenter sur un blocage explicite (403/429
    qui doit basculer immédiatement vers le fallback)."""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adaptateur = HTTPAdapter(max_retries=retry)
    session.mount("https://", adaptateur)
    session.mount("http://", adaptateur)
    return session


def obtenir_headers_aleatoires() -> dict:
    """Retourne un jeu de headers HTTP réaliste avec un User-Agent tiré au sort."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def obtenir_proxy_aleatoire() -> dict | None:
    """Retourne un dict de proxy requests tiré au sort dans PROXIES_DISPONIBLES,
    ou None si aucun proxy n'est configuré (comportement par défaut)."""
    if not PROXIES_DISPONIBLES:
        return None
    proxy = random.choice(PROXIES_DISPONIBLES)
    return {"http": proxy, "https": proxy}


def verifier_disponibilite_annuaire(session: requests.Session) -> bool:
    """Sonde rapide de la page d'accueil de l'annuaire avant de lancer la
    boucle complète (8 villes x 5 métiers). Évite de perdre plusieurs minutes
    à enchaîner des requêtes vouées à l'échec si un blocage anti-bot (ou une
    panne du site) est déjà actif dès la première requête."""
    try:
        reponse = session.get(
            "https://www.pagesjaunes.fr/",
            headers=obtenir_headers_aleatoires(),
            proxies=obtenir_proxy_aleatoire(),
            timeout=TIMEOUT_REQUETE,
        )
        if reponse.status_code != 200:
            logging.warning(f"Sonde annuaire : code HTTP {reponse.status_code} (blocage probable)")
            return False
        return True
    except requests.exceptions.RequestException as erreur:
        logging.warning(f"Sonde annuaire : injoignable ({erreur})")
        return False


def pause_humaine():
    """Introduit un délai aléatoire pour simuler un comportement humain et
    limiter le risque de bannissement IP. Utilisé par le scraping PagesJaunes
    (méthode secondaire) ; la source de données ouvertes n'en a pas besoin
    (pas d'anti-bot), voir pause_courtoisie_api_gouv."""
    delai = random.uniform(DELAI_MIN, DELAI_MAX)
    logging.debug(f"Pause de {delai:.1f}s avant la prochaine requête...")
    time.sleep(delai)


# ---------------------------------------------------------------------------
# SOURCE DE DONNÉES OUVERTES (API Recherche d'Entreprises / SIRENE)
# MÉTHODE PRIMAIRE — publique, gratuite, sans clé, sans blocage anti-bot.
# ---------------------------------------------------------------------------

def pause_courtoisie_api_gouv():
    """Léger délai entre deux appels à l'API publique : pas nécessaire pour
    éviter un blocage (il n'y en a pas), mais évite de solliciter une API
    publique gratuite plus vite que nécessaire et limite le risque de
    rate-limiting en cas de rafale de requêtes."""
    time.sleep(random.uniform(0.3, 0.8))


def domaine_existe(domaine: str) -> bool:
    """Vérifie que le domaine résout réellement en DNS avant de l'utiliser
    pour construire une adresse email. Un domaine deviné à partir du seul nom
    de l'entreprise (ex: 'stengerplatresstaffwereystenger.fr' pour STENGER
    PLATRES & STAFF) peut ne correspondre à rien de réel : sans ce contrôle,
    l'adresse générée provoque un rejet NXDOMAIN lors de l'envoi (cas réel
    observé). Ne prouve pas que le domaine appartient à l'entreprise (ça,
    c'est le rôle plus poussé de email_enricher.py), juste qu'il existe."""
    if not domaine or "." not in domaine:
        # Garde-fou : un domaine vide résout parfois vers 0.0.0.0 (adresse
        # réservée, pas un vrai site) selon le résolveur système — jamais un
        # domaine exploitable, donc rejeté explicitement avant même d'appeler
        # le DNS.
        return False
    try:
        socket.gethostbyname(domaine)
        return True
    except (OSError, UnicodeError):
        # UnicodeError : encodage IDNA impossible (label vide ou > 63
        # caractères) quand le nom d'entreprise slugifié est vide ou très
        # long (ex: raisons sociales à rallonge) — pas un vrai domaine,
        # donc pas résolvable, mais Python lève une exception différente
        # d'un simple échec DNS. Cas réel observé en production (crash total
        # de recuperer_leads_open_data() sur "label empty or too long").
        return False


def generer_email_probable(nom_entreprise: str) -> str | None:
    """Aucune adresse e-mail n'est publiée dans le registre SIRENE (données
    d'identité légale uniquement, pas de coordonnées commerciales). Ne génère
    un format structurel standardisé que si le domaine deviné résout
    réellement en DNS ; sinon retourne None plutôt qu'une adresse fabriquée
    invalide (voir domaine_existe)."""
    slug = re.sub(r"[^a-z0-9]+", "", nom_entreprise.lower())
    domaine = f"{slug}.fr"
    return f"contact@{domaine}" if domaine_existe(domaine) else None


def rechercher_entreprises_ouvertes(
    code_insee: str, code_naf: str, session: requests.Session
) -> list[dict]:
    """Interroge l'API Recherche d'Entreprises (recherche-entreprises.api.gouv.fr)
    pour une commune et un secteur d'activité (code NAF) donnés. Filtre sur les
    PME actives et ne conserve que celles ayant un établissement réellement
    actif dans la commune ciblée (le paramètre code_commune seul peut
    remonter des groupes nationaux dont une ancienne agence locale est fermée).
    Retourne une liste de fiches brutes (nom + adresse), jamais d'exception :
    une erreur réseau/API renvoie une liste vide pour cette recherche precise."""
    params = {
        "code_commune": code_insee,
        "activite_principale": code_naf,
        "etat_administratif": "A",
        "categorie_entreprise": "PME",
        "per_page": MAX_LEADS_PAR_RECHERCHE,
    }
    try:
        reponse = session.get(API_GOUV_RECHERCHE_URL, params=params, timeout=TIMEOUT_REQUETE)
        if reponse.status_code != 200:
            logging.warning(f"API Recherche d'Entreprises : code HTTP {reponse.status_code}")
            return []
        donnees = reponse.json()
    except requests.exceptions.RequestException as erreur:
        logging.warning(f"API Recherche d'Entreprises injoignable : {erreur}")
        return []
    except ValueError:
        logging.warning("API Recherche d'Entreprises : réponse non-JSON")
        return []

    fiches = []
    for entreprise in donnees.get("results", []):
        etablissements_actifs = [
            e for e in entreprise.get("matching_etablissements", [])
            if e.get("etat_administratif") == "A"
        ]
        if not etablissements_actifs:
            continue
        adresse = etablissements_actifs[0].get("adresse", "")
        nom = entreprise.get("nom_complet") or entreprise.get("nom_raison_sociale")
        if not nom:
            continue
        fiches.append({
            "nom": nom,
            "adresse": adresse,
            "siren": entreprise.get("siren"),
            # Déjà présent dans CETTE MÊME réponse SIRENE (aucun appel
            # supplémentaire) — voir même logique côté pro,
            # outbound_chantiers/sourcing_acteurs_pro.py::extraire_champs_utiles.
            "tranche_effectif_salarie": etablissements_actifs[0].get("tranche_effectif_salarie"),
        })

    return fiches


def recuperer_leads_open_data() -> list[dict]:
    """Construit la liste de leads en interrogeant l'API Recherche
    d'Entreprises pour chaque combinaison ville x secteur. Lève une exception
    si aucun lead n'a pu être extrait (déclenche le filet de sécurité suivant :
    scraping PagesJaunes, puis fixture statique en dernier recours)."""
    logging.info("Démarrage de la récupération via données ouvertes (SIRENE) - Région Grand Est")
    session = creer_session_resiliente()
    leads = []

    for ville in VILLES_CIBLES:
        code_insee = VILLES_CODE_INSEE.get(ville)
        if not code_insee:
            logging.warning(f"Pas de code INSEE connu pour {ville}, ville ignorée")
            continue

        for code_naf, secteur in SECTEURS_NAF:
            logging.info(f"Recherche : NAF {code_naf} ({secteur}) à {ville}")
            pause_courtoisie_api_gouv()
            fiches = rechercher_entreprises_ouvertes(code_insee, code_naf, session)

            if not fiches:
                logging.warning(f"Aucune entreprise active trouvée pour {ville} / {secteur}")
                continue

            for fiche in fiches:
                email = generer_email_probable(fiche["nom"])
                leads.append({
                    "company_name": fiche["nom"],
                    "industry": secteur,
                    "ville": ville,
                    # Champs structurés (en plus de la mention en texte libre
                    # dans "weakness") : un SIREN réel et une adresse propre
                    # donnent de la crédibilité au lot pour un usage commercial
                    # (vérifiable sur annuaire-entreprises.data.gouv.fr).
                    "siren": fiche.get("siren"),
                    "adresse": fiche["adresse"],
                    "tranche_effectif_salarie": fiche.get("tranche_effectif_salarie"),
                    "weakness": (
                        "Aucun site web recensé dans les données publiques (SIRENE) : "
                        "visibilité digitale à qualifier manuellement avant prospection "
                        f"(siège : {fiche['adresse']})"
                    ),
                    "email": email,
                    "email_source": "domaine_verifie_sans_email" if email else "aucun_domaine_trouve",
                })
                logging.info(f"Lead extrait : {fiche['nom']} ({secteur}, {ville})")

    if not leads:
        raise RuntimeError("Échec total de la récupération via données ouvertes (0 lead obtenu)")

    return leads


# ---------------------------------------------------------------------------
# SCRAPING D'ANNUAIRE (PagesJaunes) — MÉTHODE SECONDAIRE
# Conservée comme filet de sécurité si l'API ci-dessus est indisponible.
# Sujette à blocage anti-bot (voir sonde + disjoncteur ci-dessous).
# ---------------------------------------------------------------------------

def telecharger_page(url: str, session: requests.Session) -> str | None:
    """Télécharge une page en gérant timeout, encodage et codes HTTP anormaux.
    Retourne None (sans jamais lever d'exception non gérée) en cas d'échec."""
    try:
        reponse = session.get(
            url,
            headers=obtenir_headers_aleatoires(),
            proxies=obtenir_proxy_aleatoire(),
            timeout=TIMEOUT_REQUETE,
        )
        if reponse.status_code != 200:
            logging.warning(f"Code HTTP {reponse.status_code} reçu pour {url} (blocage probable)")
            return None
        reponse.encoding = "utf-8"
        return reponse.text
    except requests.exceptions.Timeout:
        logging.warning(f"Timeout dépassé ({TIMEOUT_REQUETE}s) sur {url}")
        return None
    except requests.exceptions.RequestException as erreur:
        logging.warning(f"Échec réseau sur {url} : {erreur}")
        return None


# ---------------------------------------------------------------------------
# EXTRACTION & ENRICHISSEMENT
# ---------------------------------------------------------------------------

def extraire_fiches_entreprises(html: str) -> list[dict]:
    """Parse le HTML de la page de résultats et extrait les fiches brutes
    (nom + site web). Les sélecteurs CSS ciblent une structure d'annuaire
    généraliste et doivent être ajustés si la structure de la source change."""
    fiches = []
    soup = BeautifulSoup(html, "html.parser")
    blocs = (
        soup.select("div.bi-bloc")
        or soup.select("article")
        or soup.select("div[itemtype*='LocalBusiness']")
    )

    for bloc in blocs:
        try:
            nom_tag = bloc.select_one(".denomination-links, h2, h3, [itemprop='name']")
            nom = nom_tag.get_text(strip=True) if nom_tag else None
            if not nom:
                continue

            site_tag = bloc.select_one("a[href^='http']")
            site_web = site_tag["href"] if site_tag and site_tag.has_attr("href") else None

            fiches.append({"nom": nom, "site_web": site_web})
        except Exception as erreur:
            logging.debug(f"Fiche ignorée lors du parsing : {erreur}")
            continue

    return fiches


def analyser_point_faible(site_web: str | None, session: requests.Session) -> str:
    """Détermine un point de douleur commercial réaliste en inspectant le site
    web de l'entreprise (responsive mobile, devis en ligne, page de contact)."""
    if not site_web:
        return (
            "Aucun site web recensé dans l'annuaire, visibilité digitale quasi "
            "nulle et forte dépendance au bouche-à-oreille"
        )

    try:
        pause_humaine()
        reponse = session.get(
            site_web,
            headers=obtenir_headers_aleatoires(),
            proxies=obtenir_proxy_aleatoire(),
            timeout=TIMEOUT_REQUETE,
        )
        if reponse.status_code != 200:
            return "Site web inaccessible ou mal maintenu (erreur serveur détectée)"

        reponse.encoding = "utf-8"
        contenu = reponse.text.lower()

        a_viewport_mobile = 'name="viewport"' in contenu
        a_devis_en_ligne = "devis en ligne" in contenu or "formulaire de devis" in contenu
        a_page_contact = "contact" in contenu

        if not a_viewport_mobile:
            return "Site non optimisé mobile (absence de balise responsive), perte de leads sur smartphone"
        if not a_devis_en_ligne:
            return "Absence de module de devis en ligne et site non optimisé pour la conversion"
        if not a_page_contact:
            return "Pas de page de contact directe, visibilité locale faible"

        return "Présence digitale correcte mais aucune preuve sociale (avis clients) mise en avant"

    except requests.exceptions.RequestException:
        return "Site web injoignable (domaine expiré ou hébergement défaillant), image professionnelle dégradée en ligne"


def deduire_email(
    nom_entreprise: str, site_web: str | None, session: requests.Session
) -> tuple[str | None, str]:
    """Tente d'extraire une adresse e-mail réelle depuis le site web de
    l'entreprise. Ne retourne un format structurel (contact@domaine) que si
    ce domaine est confirmé réel — soit parce que le site a effectivement
    répondu, soit (à défaut de site) parce que le domaine deviné à partir du
    nom résout en DNS. Un domaine deviné qui ne répond pas ne doit jamais
    devenir une adresse email utilisée pour un envoi réel : c'est exactement
    ce qui causait des rejets NXDOMAIN (ex: STENGER PLATRES & STAFF).
    Retourne (email_ou_None, email_source)."""
    if site_web:
        try:
            pause_humaine()
            reponse = session.get(
                site_web,
                headers=obtenir_headers_aleatoires(),
                proxies=obtenir_proxy_aleatoire(),
                timeout=TIMEOUT_REQUETE,
            )
            if reponse.status_code == 200:
                reponse.encoding = "utf-8"
                correspondance = re.search(
                    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", reponse.text
                )
                if correspondance:
                    return correspondance.group(0), "email_verifie_site"

                # Le site répond bel et bien (200) : le domaine est réel,
                # même si aucun email n'y est visible.
                domaine = urlparse(site_web).netloc.replace("www.", "")
                if domaine:
                    return f"contact@{domaine}", "domaine_verifie_sans_email"
        except requests.exceptions.RequestException:
            # Site injoignable (domaine expiré, hors ligne...) : on ne doit
            # surtout pas construire une adresse à partir de ce domaine.
            pass

    # Aucun site exploitable : un format structurel deviné à partir du nom
    # n'est retenu que si le domaine résout réellement en DNS.
    slug = re.sub(r"[^a-z0-9]+", "", nom_entreprise.lower())
    domaine_devine = f"{slug}.fr"
    if domaine_existe(domaine_devine):
        return f"contact@{domaine_devine}", "domaine_verifie_sans_email"
    return None, "aucun_domaine_trouve"


# ---------------------------------------------------------------------------
# ORCHESTRATION DU SCRAPING EN DIRECT
# ---------------------------------------------------------------------------

def scraper_annuaire_batiment() -> list[dict]:
    """Boucle sur chaque ville x mot-clé métier, télécharge les résultats et
    construit la liste de leads au format attendu par lead_worker.py.
    Lève une exception si aucun lead n'a pu être extrait (déclenche le fallback)."""
    logging.info("Démarrage du scraping ciblé PME Bâtiment - Région Grand Est")
    session = creer_session_resiliente()

    if not verifier_disponibilite_annuaire(session):
        raise RuntimeError(
            "Annuaire injoignable ou bloquant dès la sonde initiale : "
            "scraping en direct abandonné avant de commencer (bascule fallback)"
        )

    leads = []
    echecs_consecutifs = 0

    for ville in VILLES_CIBLES:
        for mot_cle, secteur in MOTS_CLES_METIER:
            if echecs_consecutifs >= SEUIL_ECHECS_CONSECUTIFS:
                logging.error(
                    f"{echecs_consecutifs} échecs consécutifs : blocage anti-bot "
                    f"systémique probable, abandon du scraping en direct (bascule fallback)"
                )
                raise RuntimeError("Trop d'échecs consécutifs lors du scraping en direct")

            url = BASE_URL_ANNUAIRE.format(quoi=mot_cle.replace(" ", "+"), ou=ville)
            logging.info(f"Recherche : '{mot_cle}' à {ville}")

            pause_humaine()
            html = telecharger_page(url, session)
            if html is None:
                logging.warning(f"Aucune donnée récupérée pour {ville} / {mot_cle}")
                echecs_consecutifs += 1
                continue

            fiches = extraire_fiches_entreprises(html)
            if not fiches:
                logging.warning(
                    f"Parsing vide pour {ville} / {mot_cle} "
                    f"(structure HTML modifiée ou blocage anti-bot silencieux)"
                )
                echecs_consecutifs += 1
                continue

            echecs_consecutifs = 0

            for fiche in fiches[:MAX_LEADS_PAR_RECHERCHE]:
                weakness = analyser_point_faible(fiche["site_web"], session)
                email, email_source = deduire_email(fiche["nom"], fiche["site_web"], session)
                leads.append({
                    "company_name": fiche["nom"],
                    "industry": secteur,
                    "ville": ville,
                    "weakness": weakness,
                    "email": email,
                    "email_source": email_source,
                })
                logging.info(f"Lead extrait : {fiche['nom']} ({secteur})")

    if not leads:
        raise RuntimeError("Échec total du scraping en direct (0 lead obtenu)")

    return leads


# ---------------------------------------------------------------------------
# FILET DE SÉCURITÉ (FALLBACK) - jeu de données de secours qualifié
# ---------------------------------------------------------------------------
#
# NOTE IMPORTANTE : ces profils sont des exemples représentatifs et réalistes
# du type de PME visées (archétypes de dénomination, secteurs, points de
# douleur), fournis comme filet de sécurité pour garantir qu'un leads.json
# exploitable existe TOUJOURS. Avant tout envoi commercial réel, il est
# recommandé de vérifier/actualiser ces fiches avec une source vérifiée
# (annuaire officiel, Société.com, Infogreffe...).
#
# Les emails ne sont volontairement PAS renseignés ici : les domaines de ces
# exemples illustratifs (ex: "batirenovalsace.fr") sont fictifs et ne
# résolvent pas en DNS (vérifié) — les y mettre provoquerait exactement le
# type de rejet NXDOMAIN que ce pipeline doit désormais empêcher partout.
# email=None est filtré par le pipeline d'envoi (email=not.is.null côté
# api/main.py) tant qu'un email réel n'a pas été trouvé/vérifié.

def obtenir_leads_de_secours() -> list[dict]:
    logging.warning("Activation du jeu de données de secours (fallback qualifié Grand Est)")
    return [
        {
            "company_name": "Bâti Rénov Alsace",
            "industry": "Bâtiment - Rénovation Énergétique",
            "weakness": "Absence de module de devis en ligne et site non optimisé mobile",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Lorraine Toiture & Façades",
            "industry": "Bâtiment - Second Œuvre / Rénovation",
            "weakness": "Pas de page de contact directe, visibilité locale faible sur Nancy",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Champagne Élec Pro",
            "industry": "Bâtiment - Électricité",
            "weakness": "Site vitrine statique sans formulaire, aucune prise de rendez-vous en ligne",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Vosges Plomberie Chauffage",
            "industry": "Bâtiment - Plomberie / Chauffage",
            "weakness": "Aucun site web recensé, dépendance totale au bouche-à-oreille local",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Grand Est Isolation Pro",
            "industry": "Bâtiment - Isolation / Rénovation Énergétique",
            "weakness": "Site non responsive (absence de balise mobile), perte de leads sur smartphone",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Strasbourg Gros Œuvre & Maçonnerie",
            "industry": "Bâtiment - Gros Œuvre",
            "weakness": "Aucune preuve sociale (avis clients) mise en avant, crédibilité en ligne faible",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Metz Rénovation Habitat",
            "industry": "Bâtiment - Second Œuvre / Rénovation",
            "weakness": "Site web injoignable (domaine expiré), image professionnelle dégradée en ligne",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
        {
            "company_name": "Troyes Bâti Services",
            "industry": "Bâtiment - Gros Œuvre",
            "weakness": "Pas de module de devis en ligne, formulaire de contact non fonctionnel",
            "email": None,
            "email_source": "aucun_domaine_trouve",
        },
    ]


# ---------------------------------------------------------------------------
# PERSISTANCE (écriture du fichier tampon leads.json)
# ---------------------------------------------------------------------------

def sauvegarder_leads(leads: list[dict], chemin: Path = OUTPUT_FILE):
    """Écrit les leads au format JSON en forçant l'encodage UTF-8. Toute
    erreur d'I/O ou d'encodage est interceptée et relevée à l'appelant."""
    try:
        with open(chemin, "w", encoding="utf-8") as fichier:
            json.dump(leads, fichier, ensure_ascii=False, indent=2)
        logging.info(f"{len(leads)} leads sauvegardés avec succès dans {chemin}")
    except (IOError, OSError, UnicodeEncodeError) as erreur:
        logging.error(f"Échec critique de l'écriture du fichier JSON : {erreur}")
        raise


# ---------------------------------------------------------------------------
# POINT D'ENTRÉE PRINCIPAL
# ---------------------------------------------------------------------------

def main():
    configurer_logging()
    logging.info("=== Lancement du Scraper Bâtiment Grand Est ===")

    try:
        leads = recuperer_leads_open_data()
    except Exception as erreur_open_data:
        logging.error(f"Données ouvertes (SIRENE) indisponibles : {erreur_open_data}")
        try:
            leads = scraper_annuaire_batiment()
        except Exception as erreur_scraping:
            logging.error(f"Le scraping PagesJaunes a également échoué : {erreur_scraping}")
            alerter_degradation(
                f"Les deux sources réelles (SIRENE puis PagesJaunes) ont échoué "
                f"({erreur_open_data} / {erreur_scraping}) — bascule sur le jeu "
                f"de données fictif. Aucun nouveau vrai lead généré ce run."
            )
            leads = obtenir_leads_de_secours()

    # Filet de sécurité absolu : leads.json ne doit jamais rester vide
    if not leads:
        logging.error("Sécurité ultime : aucun lead disponible, activation forcée du fallback")
        alerter_degradation("Aucun lead récupéré par aucune source — fallback fictif forcé.")
        leads = obtenir_leads_de_secours()

    try:
        sauvegarder_leads(leads)
    except Exception as erreur_sauvegarde:
        logging.critical("Échec de sauvegarde, tentative finale avec le jeu de secours minimal...")
        alerter_degradation(f"Échec d'écriture de leads.json : {erreur_sauvegarde}")
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fichier:
                json.dump(obtenir_leads_de_secours(), fichier, ensure_ascii=False, indent=2)
        except Exception as erreur_finale:
            logging.critical(f"Impossible d'écrire leads.json : {erreur_finale}")
            alerter_degradation(f"CRITIQUE : impossible d'écrire leads.json : {erreur_finale}")
            sys.exit(1)

    logging.info("=== Terminé. Le fichier leads.json est prêt pour lead_worker.py ===")


if __name__ == "__main__":
    main()
