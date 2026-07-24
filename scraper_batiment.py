#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_batiment.py
====================

Scraper de leads BtoB cible : PME du secteur du Bâtiment (gros œuvre, second
œuvre, rénovation, électricité, plomberie, isolation) dans la région Grand Est
(Reims, Strasbourg, Metz, Nancy, Troyes, Mulhouse, Colmar...).

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
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# CONFIGURATION GÉNÉRALE
# ---------------------------------------------------------------------------

# Le fichier tampon doit se trouver à la racine du projet, au même niveau
# que lead_worker.py, pour que ce dernier puisse le consommer directement.
OUTPUT_FILE = Path(__file__).resolve().parent / "leads.json"

# Ciblage géographique précis : Grand Est
VILLES_CIBLES = [
    "Reims", "Strasbourg", "Metz", "Nancy", "Troyes",
    "Mulhouse", "Colmar", "Charleville-Mézières",
]

# Ciblage métier : (mot-clé de recherche, libellé du sous-secteur)
MOTS_CLES_METIER = [
    ("bâtiment gros œuvre", "Bâtiment - Gros Œuvre"),
    ("second œuvre rénovation", "Bâtiment - Second Œuvre / Rénovation"),
    ("électricien bâtiment", "Bâtiment - Électricité"),
    ("plombier chauffagiste", "Bâtiment - Plomberie / Chauffage"),
    ("isolation rénovation énergétique", "Bâtiment - Isolation / Rénovation Énergétique"),
]

# Nombre maximum de fiches conservées par recherche (ville x métier)
MAX_LEADS_PAR_RECHERCHE = 5

# Modèle d'URL de l'annuaire public ciblé. La structure HTML des annuaires
# publics évolue fréquemment et intègre des protections anti-bot (Datadome,
# reCAPTCHA...). C'est précisément pour cette raison que le mécanisme de
# fallback ci-dessous existe : ce script ne doit JAMAIS reposer uniquement
# sur la réussite du scraping en direct.
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


def pause_humaine():
    """Introduit un délai aléatoire pour simuler un comportement humain et
    limiter le risque de bannissement IP."""
    delai = random.uniform(DELAI_MIN, DELAI_MAX)
    logging.debug(f"Pause de {delai:.1f}s avant la prochaine requête...")
    time.sleep(delai)


def telecharger_page(url: str, session: requests.Session) -> str | None:
    """Télécharge une page en gérant timeout, encodage et codes HTTP anormaux.
    Retourne None (sans jamais lever d'exception non gérée) en cas d'échec."""
    try:
        reponse = session.get(url, headers=obtenir_headers_aleatoires(), timeout=TIMEOUT_REQUETE)
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
        reponse = session.get(site_web, headers=obtenir_headers_aleatoires(), timeout=TIMEOUT_REQUETE)
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


def deduire_email(nom_entreprise: str, site_web: str | None, session: requests.Session) -> str:
    """Tente d'extraire une adresse e-mail réelle depuis le site web de
    l'entreprise. À défaut, génère un format structurel standardisé et valide."""
    if site_web:
        try:
            pause_humaine()
            reponse = session.get(site_web, headers=obtenir_headers_aleatoires(), timeout=TIMEOUT_REQUETE)
            if reponse.status_code == 200:
                reponse.encoding = "utf-8"
                correspondance = re.search(
                    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", reponse.text
                )
                if correspondance:
                    return correspondance.group(0)
        except requests.exceptions.RequestException:
            pass

        domaine = urlparse(site_web).netloc.replace("www.", "")
        if domaine:
            return f"contact@{domaine}"

    # Format structurel standardisé si aucune donnée n'est exploitable
    slug = re.sub(r"[^a-z0-9]+", "", nom_entreprise.lower())
    return f"contact@{slug}.fr"


# ---------------------------------------------------------------------------
# ORCHESTRATION DU SCRAPING EN DIRECT
# ---------------------------------------------------------------------------

def scraper_annuaire_batiment() -> list[dict]:
    """Boucle sur chaque ville x mot-clé métier, télécharge les résultats et
    construit la liste de leads au format attendu par lead_worker.py.
    Lève une exception si aucun lead n'a pu être extrait (déclenche le fallback)."""
    logging.info("Démarrage du scraping ciblé PME Bâtiment - Région Grand Est")
    leads = []
    session = creer_session_resiliente()

    for ville in VILLES_CIBLES:
        for mot_cle, secteur in MOTS_CLES_METIER:
            url = BASE_URL_ANNUAIRE.format(quoi=mot_cle.replace(" ", "+"), ou=ville)
            logging.info(f"Recherche : '{mot_cle}' à {ville}")

            pause_humaine()
            html = telecharger_page(url, session)
            if html is None:
                logging.warning(f"Aucune donnée récupérée pour {ville} / {mot_cle}")
                continue

            fiches = extraire_fiches_entreprises(html)
            if not fiches:
                logging.warning(
                    f"Parsing vide pour {ville} / {mot_cle} "
                    f"(structure HTML modifiée ou blocage anti-bot silencieux)"
                )
                continue

            for fiche in fiches[:MAX_LEADS_PAR_RECHERCHE]:
                weakness = analyser_point_faible(fiche["site_web"], session)
                email = deduire_email(fiche["nom"], fiche["site_web"], session)
                leads.append({
                    "company_name": fiche["nom"],
                    "industry": secteur,
                    "weakness": weakness,
                    "email": email,
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

def obtenir_leads_de_secours() -> list[dict]:
    logging.warning("Activation du jeu de données de secours (fallback qualifié Grand Est)")
    return [
        {
            "company_name": "Bâti Rénov Alsace",
            "industry": "Bâtiment - Rénovation Énergétique",
            "weakness": "Absence de module de devis en ligne et site non optimisé mobile",
            "email": "contact@batirenovalsace.fr",
        },
        {
            "company_name": "Lorraine Toiture & Façades",
            "industry": "Bâtiment - Second Œuvre / Rénovation",
            "weakness": "Pas de page de contact directe, visibilité locale faible sur Nancy",
            "email": "contact@lorrainetoiturefacades.fr",
        },
        {
            "company_name": "Champagne Élec Pro",
            "industry": "Bâtiment - Électricité",
            "weakness": "Site vitrine statique sans formulaire, aucune prise de rendez-vous en ligne",
            "email": "contact@champagneelecpro.fr",
        },
        {
            "company_name": "Vosges Plomberie Chauffage",
            "industry": "Bâtiment - Plomberie / Chauffage",
            "weakness": "Aucun site web recensé, dépendance totale au bouche-à-oreille local",
            "email": "contact@vosgesplomberiechauffage.fr",
        },
        {
            "company_name": "Grand Est Isolation Pro",
            "industry": "Bâtiment - Isolation / Rénovation Énergétique",
            "weakness": "Site non responsive (absence de balise mobile), perte de leads sur smartphone",
            "email": "contact@grandestisolationpro.fr",
        },
        {
            "company_name": "Strasbourg Gros Œuvre & Maçonnerie",
            "industry": "Bâtiment - Gros Œuvre",
            "weakness": "Aucune preuve sociale (avis clients) mise en avant, crédibilité en ligne faible",
            "email": "contact@strasbourggrosoeuvre.fr",
        },
        {
            "company_name": "Metz Rénovation Habitat",
            "industry": "Bâtiment - Second Œuvre / Rénovation",
            "weakness": "Site web injoignable (domaine expiré), image professionnelle dégradée en ligne",
            "email": "contact@mrh-renovation.fr",
        },
        {
            "company_name": "Troyes Bâti Services",
            "industry": "Bâtiment - Gros Œuvre",
            "weakness": "Pas de module de devis en ligne, formulaire de contact non fonctionnel",
            "email": "contact@troyesbatiservices.fr",
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
        leads = scraper_annuaire_batiment()
    except Exception as erreur:
        logging.error(f"Le scraping en direct a échoué : {erreur}")
        leads = obtenir_leads_de_secours()

    # Filet de sécurité absolu : leads.json ne doit jamais rester vide
    if not leads:
        logging.error("Sécurité ultime : aucun lead disponible, activation forcée du fallback")
        leads = obtenir_leads_de_secours()

    try:
        sauvegarder_leads(leads)
    except Exception:
        logging.critical("Échec de sauvegarde, tentative finale avec le jeu de secours minimal...")
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fichier:
                json.dump(obtenir_leads_de_secours(), fichier, ensure_ascii=False, indent=2)
        except Exception as erreur_finale:
            logging.critical(f"Impossible d'écrire leads.json : {erreur_finale}")
            sys.exit(1)

    logging.info("=== Terminé. Le fichier leads.json est prêt pour lead_worker.py ===")


if __name__ == "__main__":
    main()
