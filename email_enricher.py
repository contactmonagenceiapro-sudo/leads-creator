#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_enricher.py
==================

Enrichit les leads de `leads.json` (produits par scraper_batiment.py, sans
email réel car le registre SIRENE ne publie pas de coordonnées commerciales).

Stratégie, par ordre de confiance :
    1. Déduire un ou plusieurs noms de domaine plausibles à partir du nom de
       l'entreprise (nom commercial entre parenthèses en priorité, sinon
       raison sociale nettoyée des formes juridiques).
    2. Vérifier en HTTP si l'un de ces domaines existe réellement.
    3. Si oui : tenter d'en extraire un email réel (lien mailto:, puis motif
       d'email dans le HTML). Sinon : conserver contact@domaine (le domaine
       est confirmé réel, l'adresse exacte reste une convention plausible).
    4. Si aucun domaine ne répond : conserver la génération structurelle
       d'origine, explicitement marquée comme non vérifiée.

Chaque lead récupère un champ `email_source` pour tracer le niveau de
confiance : "email_verifie_site", "domaine_verifie_sans_email",
"aucun_domaine_trouve".

Utilisation :
    source venv/bin/activate
    python email_enricher.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from email_validator import email_exploitable
from phone_enricher import enrichir_telephone_via_google_places

LEADS_FILE = Path(__file__).resolve().parent / "leads.json"
BACKUP_FILE = Path(__file__).resolve().parent / "leads.json.bak"

TIMEOUT_REQUETE = 4
MAX_WORKERS = 20
TLDS = ["fr", "com", "eu"]

# Sous-pages fréquentes où un email de contact traîne quand il n'est pas sur
# l'accueil (mentions légales notamment, souvent obligatoires en France).
PAGES_CONTACT = ["/contact", "/contact.html", "/mentions-legales", "/nous-contacter"]

FORMES_JURIDIQUES = re.compile(
    r"\b(SARL|SAS|SASU|EURL|SCI|SA|EI|EIRL|SNC)\b", re.IGNORECASE
)
MOTIF_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
MOTIF_MAILTO = re.compile(r'href=["\']mailto:([^"\'?]+)', re.IGNORECASE)

# Téléphone français : lien tel: (signal le plus fiable) en priorité, motif
# texte "0X XX XX XX XX" (séparateurs espace/point/tiret ou aucun, ou
# +33/0033) en repli. La normalisation (10 chiffres, préfixe 0) se fait dans
# normaliser_telephone().
MOTIF_TEL_HREF = re.compile(r'href=["\']tel:([+0-9][0-9\s().-]{7,17})["\']', re.IGNORECASE)
MOTIF_TELEPHONE = re.compile(
    r"(?:\+33|0033)[\s.-]?[1-9](?:[\s.-]?\d{2}){4}|0[1-9](?:[\s.-]?\d{2}){4}"
)

# Emails-placeholder qui apparaissent dans des templates/exemples non rendus
# (ex: "user@domain.com", "${email}", "email@example.com"), ou adresses
# techniques générées par des SDK de monitoring embarqués dans le site
# (ex: Sentry sur des sites Wix : "<hash>@sentry-next.wixpress.com" — un cas
# réel observé dans ce pipeline, aucun rapport avec un vrai contact
# commercial) : jamais valides.
MOTIF_EMAIL_INVALIDE = re.compile(
    r"^(user|test|foo|bar|someone|name|nom|prenom|email|votre)@|"
    r"@(domain|example|test|yourdomain|mydomain)\.(com|fr|org)$|"
    r"^[0-9a-f]{16,}@|"
    r"@([a-z0-9-]+\.)*(sentry|wixpress|sentry-next)\.|"
    r"[{}$%]",
    re.IGNORECASE,
)

# Mots trop courts ou trop génériques pour valider qu'une page appartient
# bien à l'entreprise cherchée (sinon "SA", "LE", "DE"... matcheraient partout).
MOTS_GENERIQUES = {
    "sarl", "sas", "sasu", "eurl", "sci", "entreprise", "entreprises",
    "societe", "renovation", "batiment", "construction", "constructions",
    "services", "service", "france", "groupe", "etablissements", "ets",
    "plus", "pro", "habitat", "energie", "energies", "nord", "sud", "est", "ouest",
}


def normaliser(texte: str) -> str:
    """Minuscules + suppression des accents, pour une comparaison robuste."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def mots_significatifs(nom: str) -> list[str]:
    """Extrait les mots du nom d'entreprise assez longs et distinctifs pour
    servir de preuve qu'une page web appartient bien à cette entreprise."""
    nom_normalise = normaliser(FORMES_JURIDIQUES.sub("", nom))
    mots = re.findall(r"[a-z0-9]+", nom_normalise)
    return [m for m in mots if len(m) >= 4 and m not in MOTS_GENERIQUES]


def contient_mot(texte_normalise: str, mot: str) -> bool:
    """Recherche mot entier (limites de mot), pas une simple sous-chaîne :
    'albat' ne doit pas matcher à l'intérieur de 'albatros'. Bug réel observé
    lors d'un premier essai (faux positif sur un site sans rapport)."""
    return re.search(rf"\b{re.escape(mot)}\b", texte_normalise) is not None


def page_correspond_a_entreprise(nom_complet: str, ville: str | None, texte_page: str) -> bool:
    """Vérifie qu'au moins la majorité des mots distinctifs du nom de
    l'entreprise apparaissent réellement sur la page (en mots entiers), ET
    que la ville ciblée y apparaît aussi si elle est connue. Un domaine qui
    répond ne prouve rien en soi (domaine parqué, revendu, ou homonyme sans
    rapport — observé en pratique : une page espagnole/mexicaine sans rapport,
    ou une grande entreprise internationale portant le même nom qu'un petit
    artisan local). La vérification de ville réduit fortement ce risque
    d'homonymie, une grande entreprise nationale ne mentionnant typiquement
    pas une petite ville du Grand Est sur sa page d'accueil."""
    nom_principal, nom_commercial = extraire_nom_commercial(nom_complet)
    mots = mots_significatifs(nom_commercial or nom_principal)
    if not mots:
        # Aucun mot distinctif exploitable (ex: nom trop court/générique) :
        # on ne peut pas valider, donc on refuse plutôt que de deviner.
        return False

    texte_normalise = normaliser(texte_page)
    trouves = sum(1 for m in mots if contient_mot(texte_normalise, m))
    seuil = max(1, (len(mots) + 1) // 2)
    if trouves < seuil:
        return False

    if ville:
        ville_normalisee = normaliser(re.sub(r"[^a-zA-Z]+", " ", ville)).strip()
        if ville_normalisee and not contient_mot(texte_normalise, ville_normalisee.replace(" ", "")):
            # Essai aussi avec le premier mot de la ville (ex: "Charleville"
            # pour "Charleville-Mézières") au cas où le site n'utilise pas
            # le nom complet avec tiret.
            premier_mot = ville_normalisee.split(" ")[0] if " " in ville_normalisee else ville_normalisee
            if not contient_mot(texte_normalise, premier_mot):
                return False

    return True

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-company-lead-enrichment/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}


def configurer_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname).1s] %(message)s",
        stream=sys.stdout,
    )


log = logging.getLogger(__name__)


def extraire_nom_commercial(nom_complet: str) -> tuple[str, str | None]:
    """Sépare le nom commercial (entre parenthèses, souvent la marque
    utilisée réellement) de la raison sociale légale. Ex:
    'DEOUST (DEOUST ELECTRICITE)' -> ('DEOUST', 'DEOUST ELECTRICITE')."""
    match = re.match(r"^(.*?)\s*\((.*?)\)\s*$", nom_complet.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return nom_complet.strip(), None


def nettoyer_pour_slug(nom: str, avec_tirets: bool = False) -> str:
    nom = FORMES_JURIDIQUES.sub("", nom).lower()
    if avec_tirets:
        return re.sub(r"[^a-z0-9]+", "-", nom).strip("-")
    return re.sub(r"[^a-z0-9]+", "", nom)


def generer_slugs_candidats(nom_complet: str) -> list[str]:
    """Retourne les slugs de domaine à essayer, du plus probable au moins
    probable : nom commercial (compact puis à tirets) d'abord, raison
    sociale ensuite. Beaucoup de petites entreprises utilisent un domaine
    avec tirets (ex: nord-est-ravalement.fr) plutôt que collé."""
    nom_principal, nom_commercial = extraire_nom_commercial(nom_complet)
    slugs = []
    for nom in (nom_commercial, nom_principal):
        if not nom:
            continue
        for avec_tirets in (False, True):
            slug = nettoyer_pour_slug(nom, avec_tirets)
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def nettoyer_texte_extrait(brut: str) -> str:
    """Retire les espaces et caractères Unicode invisibles (espace de largeur
    nulle, marques de direction...) qui se glissent parfois dans un attribut
    href="mailto:..."/"tel:..." mal formé côté site source — sans rapport
    avec la donnée elle-même, mais qui la rendraient invalide telle quelle
    (cas réel observé : "​jonathan.dray@outlook.fr")."""
    return "".join(c for c in brut if unicodedata.category(c) != "Cf").strip()


def extraire_email_depuis_html(html: str) -> str | None:
    candidats = []
    mailto = MOTIF_MAILTO.search(html)
    if mailto:
        candidats.append(nettoyer_texte_extrait(mailto.group(1).split("?")[0]))
    motif = MOTIF_EMAIL.search(html)
    if motif:
        candidats.append(motif.group(0))

    for candidat in candidats:
        if candidat and not MOTIF_EMAIL_INVALIDE.search(candidat):
            return candidat
    return None


def normaliser_telephone(brut: str) -> str:
    """Réduit un numéro à ses seuls chiffres, avec un préfixe '0' homogène
    que la source soit au format local (0X...) ou international (+33X...),
    pour un format stable et directement composable/exploitable en CRM."""
    chiffres = re.sub(r"\D", "", nettoyer_texte_extrait(brut))
    if chiffres.startswith("33") and len(chiffres) == 11:
        chiffres = "0" + chiffres[2:]
    elif chiffres.startswith("0033") and len(chiffres) == 13:
        chiffres = "0" + chiffres[4:]
    return chiffres


def telephone_plausible(chiffres: str) -> bool:
    """Rejette les numéros manifestement placeholder (chiffre unique répété
    type "0666666666", ou séquence triviale) : réellement présents sur la
    page mais typiques d'un gabarit de site non complété par l'artisan, pas
    d'un vrai numéro joignable."""
    if len(chiffres) != 10:
        return False
    if len(set(chiffres[1:])) == 1:
        return False
    if chiffres[1:] in "0123456789" or chiffres[1:] in "9876543210":
        return False
    return True


def extraire_telephone_depuis_html(html: str) -> str | None:
    """N'extrait un numéro que s'il est réellement présent dans la page (lien
    tel: en priorité, plus fiable qu'un simple motif texte) ; ne fabrique
    JAMAIS de numéro — un champ vide est préférable à un numéro inventé qui
    ferait échouer un appel commercial réel une fois le lead vendu."""
    lien = MOTIF_TEL_HREF.search(html)
    if lien:
        chiffres = normaliser_telephone(lien.group(1))
        if telephone_plausible(chiffres):
            return chiffres

    motif = MOTIF_TELEPHONE.search(html)
    if motif:
        chiffres = normaliser_telephone(motif.group(0))
        if telephone_plausible(chiffres):
            return chiffres
    return None


def chercher_contact_sous_pages(
    session: requests.Session, base_url: str
) -> tuple[str | None, str | None]:
    """Repli : cherche email ET téléphone sur les sous-pages contact/mentions-
    légales en une seule passe par page (fréquent : ces coordonnées ne sont
    parfois que sur la page contact, les mentions légales étant obligatoires
    en France). Retourne (email_ou_None, telephone_ou_None)."""
    email_trouve = None
    telephone_trouve = None
    for chemin in PAGES_CONTACT:
        if email_trouve and telephone_trouve:
            break
        try:
            reponse = session.get(base_url + chemin, headers=HEADERS, timeout=TIMEOUT_REQUETE)
            if reponse.status_code != 200:
                continue
            reponse.encoding = reponse.encoding or "utf-8"
            if not email_trouve:
                email_trouve = extraire_email_depuis_html(reponse.text)
            if not telephone_trouve:
                telephone_trouve = extraire_telephone_depuis_html(reponse.text)
        except requests.exceptions.RequestException:
            continue
    return email_trouve, telephone_trouve


def sonder_domaine(
    session: requests.Session, domaine: str, nom_complet: str, ville: str | None
) -> tuple[bool, str | None, str | None]:
    """Vérifie si le domaine répond ET que la page correspond réellement à
    l'entreprise cherchée (pas seulement un domaine qui répond par hasard :
    homonyme, domaine revendu/parqué...). Retourne
    (page_validee, email_ou_None, telephone_ou_None)."""
    for url in (f"https://{domaine}", f"https://www.{domaine}"):
        try:
            reponse = session.get(url, headers=HEADERS, timeout=TIMEOUT_REQUETE, allow_redirects=True)
            if reponse.status_code != 200:
                continue
            reponse.encoding = reponse.encoding or "utf-8"
            if not page_correspond_a_entreprise(nom_complet, ville, reponse.text):
                log.debug(f"{domaine} répond mais le contenu ne correspond pas à {nom_complet!r}")
                continue
            email = extraire_email_depuis_html(reponse.text)
            telephone = extraire_telephone_depuis_html(reponse.text)
            if not email or not telephone:
                email_repli, telephone_repli = chercher_contact_sous_pages(session, url)
                email = email or email_repli
                telephone = telephone or telephone_repli
            return True, email, telephone
        except requests.exceptions.RequestException:
            continue
    return False, None, None


def enrichir_lead(lead: dict) -> dict:
    """Enrichit un lead : ne modifie jamais company_name/industry/weakness
    de façon destructive, ajoute/actualise email + email_source + telephone."""
    session = requests.Session()
    slugs = generer_slugs_candidats(lead["company_name"])
    ville = lead.get("ville")

    for slug in slugs:
        for tld in TLDS:
            domaine = f"{slug}.{tld}"
            valide, email, telephone = sonder_domaine(session, domaine, lead["company_name"], ville)
            if not valide:
                continue
            if not telephone:
                # Site validé mais aucun téléphone publié dessus : dernier
                # recours (payant, optionnel) via Google Places — voir
                # phone_enricher.py. Un numéro publié sur la fiche Google
                # Business de l'entreprise reste une donnée fiable, à la
                # différence de l'e-mail (jamais fabriqué, voir plus bas).
                telephone = enrichir_telephone_via_google_places(lead["company_name"], ville or "")
            if email:
                # Format + domaine jetable + MX (voir email_validator.py) :
                # une adresse trouvée dans le HTML peut être syntaxiquement
                # correcte mais pointer vers un domaine sans aucun serveur
                # mail (typo, adresse d'un ancien prestataire jamais mise à
                # jour...) — mieux vaut ne conserver aucun email que garder
                # une adresse condamnée à un hard bounce à l'envoi.
                exploitable, raison = email_exploitable(email)
                if exploitable:
                    log.info(f"Email trouvé et validé pour {lead['company_name']} : {email}")
                    return {**lead, "email": email, "email_source": "email_verifie_site", "telephone": telephone}
                log.warning(f"Email {email} trouvé pour {lead['company_name']} mais écarté ({raison})")
                email = None
            log.info(f"Domaine {domaine} validé pour {lead['company_name']}, mais aucun email exploitable")
            # contact@{domaine} n'est qu'une convention plausible (le
            # domaine, lui, est confirmé réel via la requête HTTP
            # précédente) — vérifiée avant d'être retenue, pour la même
            # raison que ci-dessus.
            email_devine = f"contact@{domaine}"
            exploitable, raison = email_exploitable(email_devine)
            return {
                **lead,
                "email": email_devine if exploitable else None,
                "email_source": "domaine_verifie_sans_email" if exploitable else "domaine_verifie_email_ecarte",
                "telephone": telephone,
            }

    log.warning(f"Aucun domaine validé pour {lead['company_name']} — email fabriqué écarté")
    # IMPORTANT : on efface explicitement l'email (même s'il en existait déjà
    # un dans `lead`) plutôt que de le laisser passer tel quel. Un email de la
    # forme contact@{nom-entreprise}.fr construit sans domaine confirmé n'est
    # qu'une supposition et provoque un rejet NXDOMAIN à l'envoi (cas réel :
    # STENGER PLATRES & STAFF -> contact@stengerplatresstaffwereystenger.fr,
    # un domaine qui n'existe pas). None est filtré par le pipeline d'envoi
    # (cf. ceo_agent.py::get_leads_from_supabase, filtre email vérifié). Le
    # téléphone n'a pas la même contrainte (jamais fabriqué non plus, mais
    # une fiche Google Places n'a pas besoin d'un domaine confirmé pour être
    # fiable) : dernier recours tenté ici aussi avant d'abandonner.
    telephone = enrichir_telephone_via_google_places(lead["company_name"], ville or "")
    return {**lead, "email": None, "email_source": "aucun_domaine_trouve", "telephone": telephone}


def enrichir_tous(leads: list[dict]) -> list[dict]:
    resultats = [None] * len(leads)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(enrichir_lead, lead): i for i, lead in enumerate(leads)}
        for i, future in enumerate(as_completed(futures), 1):
            index = futures[future]
            try:
                resultats[index] = future.result()
            except Exception as erreur:
                log.error(f"Échec d'enrichissement pour {leads[index]['company_name']} : {erreur}")
                resultats[index] = {**leads[index], "email_source": "erreur_enrichissement"}
            if i % 20 == 0 or i == len(leads):
                log.info(f"Progression : {i}/{len(leads)} leads traités")
    return resultats


def main() -> int:
    configurer_logging()

    if not LEADS_FILE.exists():
        log.error(f"Fichier introuvable : {LEADS_FILE}")
        return 1

    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            leads = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"{LEADS_FILE} illisible ou corrompu — enrichissement annulé : {e}")
        return 1

    log.info(f"{len(leads)} leads chargés, début de l'enrichissement (domaines réels)...")

    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    log.info(f"Sauvegarde de l'original dans {BACKUP_FILE}")

    leads_enrichis = enrichir_tous(leads)

    with open(LEADS_FILE, "w", encoding="utf-8") as f:
        json.dump(leads_enrichis, f, ensure_ascii=False, indent=2)

    compteurs = {}
    for lead in leads_enrichis:
        source = lead.get("email_source", "inconnu")
        compteurs[source] = compteurs.get(source, 0) + 1

    log.info("=== Résumé de l'enrichissement ===")
    for source, n in sorted(compteurs.items(), key=lambda x: -x[1]):
        log.info(f"  {source} : {n}")
    log.info(f"leads.json mis à jour ({len(leads_enrichis)} leads)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
