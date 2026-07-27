"""
MODULE 2 — Filtrage & enrichissement des acteurs professionnels.

1. Filtre les acteurs bruts (module 1a) sur la zone cible et un nom
   d'entreprise exploitable.
2. Enrichit avec l'e-mail, le téléphone et les liens réseaux sociaux
   PROFESSIONNELS réellement publiés sur le site de l'entreprise (page
   d'accueil, contact, mentions légales) — jamais de donnée personnelle,
   jamais de déduction/fabrication d'adresse. Reste volontairement
   "best-effort/gratuit" (pas d'API d'enrichissement payante) : impossible
   d'obtenir de façon fiable un e-mail nominatif par cette méthode, seulement
   les contacts génériques publiés par l'entreprise elle-même.

Le site est localisé par une VRAIE recherche web (DuckDuckGo, version HTML
sans JS, aucune clé API) plutôt qu'en devinant un nom de domaine dérivé de
la raison sociale — l'ancienne méthode ({nom}.fr/.com/.eu) manquait la
majorité des entreprises dont le site n'est pas nommé exactement comme la
raison sociale (cas très fréquent chez les petits cabinets d'architecture/
MOE), ce qui limitait fortement le volume de leads réellement exploitables
en sortie du pipeline.

Même garde-fou anti-homonyme que email_enricher.py existant : on ne retient
un résultat de recherche que si la page cite bien le nom de l'entreprise ET
la commune — jamais de confiance aveugle en un résultat de recherche, pour
éviter de faire remonter un site sans rapport (homonyme, annuaire, domaine
revendu).

Un acteur dont AUCUN contact n'a pu être trouvé (ou dont l'enrichissement a
échoué techniquement) n'est plus jamais perdu silencieusement : il reste
dans le lot avec enrichissement_statut='echec', tracé en base pour un
traitement manuel plutôt que de disparaître (voir enrichir_un_acteur()).
"""

import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from outbound_chantiers.config import COMMUNES_CIBLES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENRICHISSEMENT-PRO] %(message)s")
log = logging.getLogger(__name__)

FICHIER_ENTREE = Path(__file__).parent / "acteurs_pro_bruts.json"
FICHIER_SORTIE = Path(__file__).parent / "acteurs_pro_enrichis.json"

TIMEOUT_SECONDES = 5
PAUSE_ENTRE_REQUETES_SECONDES = 1.0

# Domaines d'annuaires/réseaux sociaux/agrégateurs jamais retenus comme
# "site de l'entreprise" même s'ils apparaissent en tête de résultats — ce
# sont des tiers, pas l'entreprise elle-même, et n'exposent jamais son
# contact direct de la même façon qu'un site propre.
DOMAINES_A_IGNORER = (
    "societe.com", "pagesjaunes.fr", "linkedin.com", "facebook.com",
    "instagram.com", "annuaire-entreprises.data.gouv.fr", "verif.com",
    "infogreffe.fr", "kompass.com", "pappers.fr", "wikipedia.org",
    "indeed.com", "google.",
)
NOMBRE_MAX_RESULTATS_EXAMINES = 3

HEADERS_RECHERCHE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Motifs d'adresses/numéros placeholder à rejeter (pages de démo, CMS non
# configuré, Sentry, etc.) — même précaution que email_enricher.py.
MOTIFS_EMAIL_INVALIDES = [
    r"exemple@", r"test@", r"@sentry\.", r"@wixpress\.", r"noreply@",
    r"@wordpress\.", r"votreadresse@",
]
MOTIF_TELEPHONE_PLACEHOLDER = re.compile(r"^(\d)\1{8,9}$")  # ex: 0000000000, 1111111111


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def page_correspond_bien(html: str, nom_entreprise: str, commune: str) -> bool:
    """Garde-fou anti-homonyme : la page doit citer le nom de l'entreprise
    ET la commune, sinon on écarte le domaine (site sans rapport, revendu,
    ou parké)."""
    html_normalise = normaliser(html)
    return normaliser(nom_entreprise)[:15] in html_normalise and normaliser(commune) in html_normalise


def email_valide(adresse: str) -> bool:
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", adresse or ""):
        return False
    return not any(re.search(motif, adresse, re.IGNORECASE) for motif in MOTIFS_EMAIL_INVALIDES)


def telephone_valide(numero: str) -> bool:
    chiffres = re.sub(r"[^\d]", "", numero or "")
    if not re.match(r"^0[1-9]\d{8}$", chiffres):
        return False
    return not MOTIF_TELEPHONE_PLACEHOLDER.match(chiffres)


def _url_reelle_depuis_lien_resultat(href: str) -> str | None:
    """Les liens de résultats DuckDuckGo (version HTML) passent par une
    redirection interne (/l/?uddg=<url encodée>&...) plutôt que l'URL finale
    directement — on décode l'URL réelle plutôt que de suivre la
    redirection (évite une requête HTTP supplémentaire par résultat)."""
    if not href:
        return None
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        parametres = parse_qs(urlparse(href).query)
        cibles = parametres.get("uddg")
        return cibles[0] if cibles else None
    if href.startswith("http"):
        return href
    return None


def chercher_site_via_recherche_web(nom_entreprise: str, commune: str) -> str | None:
    """Cherche le vrai site de l'entreprise via une recherche web plutôt que
    de deviner un nom de domaine — examine les premiers résultats pertinents
    (hors annuaires/réseaux sociaux) et ne retient le premier qui passe
    page_correspond_bien() (anti-homonyme). Ne lève jamais d'exception :
    une recherche indisponible/bloquée renvoie simplement None, traité comme
    "site introuvable" par l'appelant."""
    try:
        reponse = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{nom_entreprise} {commune}"},
            headers=HEADERS_RECHERCHE,
            timeout=TIMEOUT_SECONDES,
        )
        reponse.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.warning(f"Recherche web indisponible pour « {nom_entreprise} » ({commune}) : {e}")
        return None

    soup = BeautifulSoup(reponse.text, "html.parser")
    liens_resultats = soup.select("a.result__a") or soup.select("a.result__url")

    examines = 0
    for lien in liens_resultats:
        if examines >= NOMBRE_MAX_RESULTATS_EXAMINES:
            break
        url_candidate = _url_reelle_depuis_lien_resultat(lien.get("href", ""))
        if not url_candidate:
            continue
        domaine = urlparse(url_candidate).netloc.lower()
        if any(annuaire in domaine for annuaire in DOMAINES_A_IGNORER):
            continue

        examines += 1
        try:
            reponse_site = requests.get(url_candidate, timeout=TIMEOUT_SECONDES)
            if reponse_site.status_code == 200 and page_correspond_bien(reponse_site.text, nom_entreprise, commune):
                return url_candidate
        except requests.exceptions.RequestException:
            continue

    return None


MOTIFS_RESEAUX_SOCIAUX = {
    "linkedin": r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9\-_%]+",
    "facebook": r"https?://(?:www\.)?facebook\.com/[a-zA-Z0-9.\-_%]+",
    "instagram": r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9.\-_%]+",
    "twitter": r"https?://(?:www\.)?(?:twitter|x)\.com/[a-zA-Z0-9.\-_%]+",
}

# Pages usuelles essayées en repli si la page d'accueil ne donne ni e-mail ni
# téléphone — "" = page d'accueil elle-même, déjà tentée en premier.
# Volontairement limité à UNE seule page de repli (pas de /mentions-legales
# en plus) : chaque page candidate coûte jusqu'à TIMEOUT_SECONDES sur un site
# lent/injoignable — un lot de sourcing avec plusieurs dizaines d'acteurs et
# 3 pages candidates pouvait ajouter plusieurs minutes au run (régression
# mesurée : ~150s -> 363s sur un lot réel), sans gain proportionnel de
# contacts trouvés (la page de contact couvre l'immense majorité des cas que
# les mentions légales n'apportent pas déjà).
PAGES_CONTACT_CANDIDATES = ["", "/contact"]


def extraire_reseaux_sociaux(html_source: str) -> dict[str, str]:
    trouves = {}
    for reseau, motif in MOTIFS_RESEAUX_SOCIAUX.items():
        correspondance = re.search(motif, html_source)
        if correspondance:
            trouves[reseau] = correspondance.group(0).rstrip("/\"'")
    return trouves


def extraire_contact(url_site: str) -> tuple[str | None, str | None, dict[str, str]]:
    """Cherche e-mail/téléphone/réseaux sociaux sur la page d'accueil, puis
    quelques pages usuelles si besoin (best-effort — jamais de fabrication :
    une absence de résultat reste une absence, pas une erreur)."""
    email, telephone, reseaux = None, None, {}

    for chemin in PAGES_CONTACT_CANDIDATES:
        if email and telephone:
            break
        try:
            reponse = requests.get(url_site.rstrip("/") + chemin, timeout=TIMEOUT_SECONDES)
            reponse.raise_for_status()
        except requests.exceptions.RequestException:
            continue

        html_page = reponse.text

        if not reseaux:
            reseaux = extraire_reseaux_sociaux(html_page)

        if not email:
            emails_trouves = re.findall(r"mailto:([^\"'?>\s]+)", html_page) or re.findall(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html_page
            )
            email = next((e for e in emails_trouves if email_valide(e)), None)

        if not telephone:
            telephones_trouves = re.findall(r"tel:([^\"'?>\s]+)", html_page) or re.findall(
                r"0\d(?:[\s.-]?\d{2}){4}", html_page
            )
            telephone = next((t for t in telephones_trouves if telephone_valide(t)), None)

        if chemin != PAGES_CONTACT_CANDIDATES[-1]:
            time.sleep(PAUSE_ENTRE_REQUETES_SECONDES)

    return email, telephone, reseaux


def enrichir_un_acteur(nom_entreprise: str, commune: str) -> dict:
    """Enrichit UN acteur (nom + commune) et renvoie toujours un résultat
    exploitable, même en cas d'échec total — jamais d'exception qui remonte :
    un site injoignable, un nom générant une URL invalide, ou toute autre
    erreur imprévue se traduit par enrichissement_statut='echec', pas par un
    crash qui ferait perdre le reste du lot (pipeline) ou planterait la
    requête (bouton "Ré-enrichir" du dashboard, voir api/main.py)."""
    site, email, telephone, reseaux = None, None, None, {}
    try:
        site = chercher_site_via_recherche_web(nom_entreprise, commune)
        time.sleep(PAUSE_ENTRE_REQUETES_SECONDES)
        if site:
            email, telephone, reseaux = extraire_contact(site)
    except Exception as e:
        log.error(f"Enrichissement échoué pour {nom_entreprise} ({commune}) — contact non trouvé : {e}")

    if email and telephone:
        statut = "reussi"
    elif email or telephone:
        statut = "partiel"
    else:
        statut = "echec"

    return {
        "site_web": site,
        "email": email,
        "telephone": telephone,
        "linkedin_url": reseaux.get("linkedin"),
        "reseaux_sociaux": {k: v for k, v in reseaux.items() if k != "linkedin"},
        "enrichissement_statut": statut,
        "contact_exploitable": bool(email or telephone),
    }


def filtrer_et_enrichir() -> list[dict]:
    if not FICHIER_ENTREE.exists():
        log.error(f"{FICHIER_ENTREE} introuvable — lancer d'abord sourcing_acteurs_pro.py")
        return []

    acteurs_bruts = json.loads(FICHIER_ENTREE.read_text(encoding="utf-8"))
    communes_normalisees = {normaliser(c) for c in COMMUNES_CIBLES}

    enrichis = []
    for acteur in acteurs_bruts:
        commune = acteur.get("commune") or ""
        nom = acteur.get("nom_entreprise") or ""

        if normaliser(commune) not in communes_normalisees or not nom:
            continue

        resultat = enrichir_un_acteur(nom, commune)
        acteur_enrichi = {**acteur, **resultat}
        enrichis.append(acteur_enrichi)

        log.info(
            f"{nom} ({commune}) : site={bool(resultat['site_web'])} email={bool(resultat['email'])} "
            f"tel={bool(resultat['telephone'])} linkedin={bool(resultat['linkedin_url'])} "
            f"statut={resultat['enrichissement_statut']}"
        )

    log.info(f"Enrichissement terminé : {len(enrichis)} acteurs traités, "
              f"{sum(1 for a in enrichis if a['contact_exploitable'])} avec contact exploitable")
    return enrichis


if __name__ == "__main__":
    resultats = filtrer_et_enrichir()
    FICHIER_SORTIE.write_text(json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Résultats enrichis écrits dans {FICHIER_SORTIE}")
