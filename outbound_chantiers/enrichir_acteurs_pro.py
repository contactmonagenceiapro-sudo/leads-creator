"""
MODULE 2 — Filtrage & enrichissement des acteurs professionnels.

1. Filtre les acteurs bruts (module 1a) sur la zone cible et un nom
   d'entreprise exploitable.
2. Enrichit avec l'e-mail, le téléphone et les liens réseaux sociaux
   PROFESSIONNELS réellement publiés sur le site de l'entreprise (page
   d'accueil, contact, mentions légales) — jamais de donnée personnelle,
   jamais de déduction/fabrication d'adresse. Reste volontairement
   "best-effort/gratuit" pour l'e-mail (pas d'API d'enrichissement payante) :
   impossible d'obtenir de façon fiable un e-mail nominatif par cette
   méthode, seulement les contacts génériques publiés par l'entreprise
   elle-même.
3. Si aucun téléphone n'a été trouvé sur le site (ou qu'aucun site n'a été
   localisé), tente un dernier recours via Google Places (voir
   phone_enricher.py) — payant mais optionnel (désactivé sans
   GOOGLE_PLACES_API_KEY), retenu uniquement pour le téléphone car,
   contrairement à l'e-mail nominatif, une fiche Google Business publiée
   par l'entreprise elle-même est une donnée fiable, pas une supposition.

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
from phone_enricher import enrichir_telephone_via_google_places

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENRICHISSEMENT-PRO] %(message)s")
log = logging.getLogger(__name__)

FICHIER_ENTREE = Path(__file__).parent / "acteurs_pro_bruts.json"
FICHIER_SORTIE = Path(__file__).parent / "acteurs_pro_enrichis.json"

TIMEOUT_SECONDES = 5
PAUSE_ENTRE_REQUETES_SECONDES = 1.0

# Recherche DuckDuckGo (chercher_site_via_recherche_web) : bien plus
# surveillée que la récupération d'une simple page de contact — cf.
# _reponse_ddg_bloquee ci-dessous. Pause dédiée, plus longue, pour rester
# sous le seuil de blocage anti-bot plutôt que de le découvrir en production
# (incident réel du 28/07 : 461/465 acteurs en échec sur un run à ~1 req/1s).
PAUSE_ENTRE_RECHERCHES_DDG_SECONDES = 3.0
NB_TENTATIVES_AVANT_ABANDON_DDG = 2  # au-delà, on cesse d'interroger DDG pour le reste du run (voir _ddg_bloque)
DELAI_BACKOFF_BLOCAGE_DDG_SECONDES = 20

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


_SUFFIXE_ORDINAL = re.compile(r"^\d+(er|e|eme)$")


def _mots_significatifs(nom: str) -> set[str]:
    """Mots significatifs d'un nom de commune : tirets traités comme des
    espaces, suffixes d'arrondissement ("1er", "2e"...) et le mot
    "arrondissement" ignorés.

    Nécessaire car l'API SIRENE renvoie souvent un nom de commune plus
    générique ou légalement différent de la config métier : "LYON" (sans
    numéro d'arrondissement) pour une adresse en "Lyon 3e", ou
    "OULLINS-PIERRE-BENITE" (fusion de communes actée en 2019) pour
    l'ancienne "Oullins". Sur un lot réel de 111 acteurs sourcés, une
    comparaison stricte (avant ce correctif) en écartait 73 (66 %) à ce
    seul stade, alors qu'ils étaient dans la zone ciblée."""
    mots = normaliser(nom).replace("-", " ").split()
    return {m for m in mots if m != "arrondissement" and not _SUFFIXE_ORDINAL.fullmatch(m)}


def commune_correspond(commune_source: str, communes_cibles: list[str]) -> bool:
    """Vrai si `commune_source` (telle que renvoyée par l'API SIRENE) désigne
    la même ville qu'une des communes cibles de la campagne, en tolérant les
    variantes ci-dessus — comparaison par inclusion d'ensembles de mots
    plutôt qu'égalité stricte de chaîne (dans un sens ou dans l'autre, pour
    couvrir aussi bien "LYON" ⊂ "Lyon 3e" que "Oullins" ⊂ "Oullins-Pierre-Bénite")."""
    mots_source = _mots_significatifs(commune_source)
    if not mots_source:
        return False
    return any(
        mots_source <= _mots_significatifs(cible) or _mots_significatifs(cible) <= mots_source
        for cible in communes_cibles
    )


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


def _reponse_ddg_bloquee(html: str) -> bool:
    """Vrai si DuckDuckGo a renvoyé sa page de challenge anti-bot (captcha
    image "anomaly detection", HTTP 202) plutôt que de vrais résultats.
    Marqueurs stables observés en production le 28/07 : classe CSS
    "anomaly-modal__check" et formulaire "challenge-form" postant vers
    duckduckgo.com/anomaly.js — jamais présents sur une page de résultats
    normale. Ce n'est PAS un simple rate-limit ponctuel (comme un 429) :
    un vrai captcha ne se résout pas en réessayant après une courte pause,
    d'où le disjoncteur ci-dessous plutôt qu'une boucle de retry classique."""
    return "anomaly-modal" in html or "challenge-form" in html


_ddg_definitivement_bloque = False  # disjoncteur : une fois vrai, plus aucun appel DDG pour le reste de ce run (process)


def chercher_site_via_recherche_web(nom_entreprise: str, commune: str) -> str | None:
    """Cherche le vrai site de l'entreprise via une recherche web plutôt que
    de deviner un nom de domaine — examine les premiers résultats pertinents
    (hors annuaires/réseaux sociaux) et ne retient le premier qui passe
    page_correspond_bien() (anti-homonyme). Ne lève jamais d'exception :
    une recherche indisponible/bloquée renvoie simplement None, traité comme
    "site introuvable" par l'appelant.

    Si DuckDuckGo bloque (cf. _reponse_ddg_bloquee), un seul nouvel essai est
    tenté après une pause ; si le blocage persiste, le disjoncteur
    _ddg_definitivement_bloque s'active pour le reste du run — sans lui, un
    run de plusieurs centaines d'acteurs continuerait à cogner contre le mur
    pendant des dizaines de minutes pour un résultat nul garanti (incident
    réel : 461/465 échecs sur un run bloqué dès les premières recherches)."""
    global _ddg_definitivement_bloque
    if _ddg_definitivement_bloque:
        return None

    reponse = None
    for tentative in range(1, NB_TENTATIVES_AVANT_ABANDON_DDG + 1):
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

        if not _reponse_ddg_bloquee(reponse.text):
            break

        if tentative < NB_TENTATIVES_AVANT_ABANDON_DDG:
            log.warning(
                f"DuckDuckGo a renvoyé une page anti-bot (essai {tentative}/{NB_TENTATIVES_AVANT_ABANDON_DDG}) — "
                f"pause {DELAI_BACKOFF_BLOCAGE_DDG_SECONDES}s avant nouvel essai."
            )
            time.sleep(DELAI_BACKOFF_BLOCAGE_DDG_SECONDES)
        else:
            log.error(
                "DuckDuckGo bloque toujours après un nouvel essai — recherche de site DÉSACTIVÉE pour le "
                "reste de ce run (les acteurs restants seront marqués 'non_tente', pas 'echec' : à "
                "ré-enrichir individuellement une fois le blocage levé, cf. bouton Ré-enrichir du dashboard)."
            )
            _ddg_definitivement_bloque = True
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
    ddg_bloque_avant_appel = _ddg_definitivement_bloque
    try:
        site = chercher_site_via_recherche_web(nom_entreprise, commune)
        # Pas la peine de patienter si le disjoncteur DDG est déjà déclenché
        # (ou vient de l'être ci-dessus) : plus aucune requête ne part, donc
        # rien à espacer — sans ce test, chaque acteur restant du run
        # perdrait quand même PAUSE_ENTRE_RECHERCHES_DDG_SECONDES pour rien.
        if not _ddg_definitivement_bloque:
            time.sleep(PAUSE_ENTRE_RECHERCHES_DDG_SECONDES)
        if site:
            email, telephone, reseaux = extraire_contact(site)
    except Exception as e:
        log.error(f"Enrichissement échoué pour {nom_entreprise} ({commune}) — contact non trouvé : {e}")

    if not telephone:
        # Dernier recours (payant, optionnel) : le site propre de l'entreprise
        # ne publiait aucun téléphone, ou aucun site n'a été localisé du tout —
        # voir phone_enricher.py pour pourquoi Google Places et pas
        # SIRENE/Pappers/Pages Jaunes (aucune de ces sources n'a de téléphone,
        # ou pas de façon exploitable sans risque ToS).
        telephone = enrichir_telephone_via_google_places(nom_entreprise, commune)

    if email and telephone:
        statut = "reussi"
    elif email or telephone:
        statut = "partiel"
    elif ddg_bloque_avant_appel:
        # DDG était déjà hors-jeu avant même de tenter cet acteur : jamais
        # réellement recherché, à distinguer d'un échec de recherche réel
        # (voir docstring de la fonction et le bouton "Ré-enrichir" du
        # dashboard, qui doit pouvoir cibler spécifiquement ce cas).
        statut = "non_tente"
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

    enrichis = []
    for acteur in acteurs_bruts:
        commune = acteur.get("commune") or ""
        nom = acteur.get("nom_entreprise") or ""

        if not nom or not commune_correspond(commune, COMMUNES_CIBLES):
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
