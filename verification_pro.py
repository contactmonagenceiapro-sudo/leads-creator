"""
Vérification "papiers professionnels" des artisans qui deviennent Clients
payants (SIRET + assurance décennale) — objectif : afficher un badge
"Vérifié" côté site public, argument de confiance pour les particuliers qui
remplissent /demande-devis (voir dashboard/pages_publiques.py::badge_verification_pro).

Schéma : sql/init_verification_pro_artisans.sql (colonnes ajoutées sur
`leads`, pas de table dédiée — un artisan = une ligne = un état courant,
même cardinalité que `status`/`contacted` déjà présents sur cette table).

verifier_siret_sirene() interroge la même API publique que
scraper_batiment.py (API_GOUV_RECHERCHE_URL) et outbound_chantiers/config.py
(SIRENE_API_URL) — recherche-entreprises.api.gouv.fr, gratuite, sans clé.
URL RE-DÉFINIE ici plutôt qu'importée depuis l'un de ces deux modules : même
duplication assumée que SCORE_NEUTRE_LEAD_B2C dans generation_contrats.py —
ce module doit rester importable depuis le tunnel B2C public (afficher_intake)
sans traîner scraper_batiment.py (lourd : BeautifulSoup, scraping
PagesJaunes, hors-sujet ici) ni le package B2B outbound_chantiers/ (segment
indépendant).

Seul le SIRET est vérifié automatiquement. L'assurance décennale reste un
engagement PUREMENT DÉCLARATIF (case cochée par l'artisan à l'intake) :
aucune vérification automatique de document scanné à ce stade (jugé trop
complexe) — seul un contrôle humain manuel (voir dashboard/app_pages/
administration_contrats.py, section "Vérification pro") peut faire passer
statut_verification_pro à 'verifie'.
"""

import re

import requests

SIRENE_API_URL = "https://recherche-entreprises.api.gouv.fr/search"
TIMEOUT_SECONDES = 10

STATUT_NON_VERIFIE = "non_verifie"
STATUT_EN_ATTENTE = "en_attente"
STATUT_VERIFIE = "verifie"
STATUT_REFUSE = "refuse"

STATUTS_VERIFICATION_PRO = (STATUT_NON_VERIFIE, STATUT_EN_ATTENTE, STATUT_VERIFIE, STATUT_REFUSE)

LIBELLE_STATUT_VERIFICATION_PRO = {
    STATUT_NON_VERIFIE: "Non vérifié",
    STATUT_EN_ATTENTE: "En attente (SIRET validé, assurance à contrôler)",
    STATUT_VERIFIE: "✅ Vérifié",
    STATUT_REFUSE: "Refusé",
}

_MOTIF_SIRET = re.compile(r"^\d{14}$")


def normaliser_siret(siret: str | None) -> str:
    """Retire les espaces (courants dans une saisie humaine, ex: '123 456
    789 00012') — utilisé aussi bien avant stockage qu'avant appel API, pour
    que le SIRET comparé/stocké soit toujours sous la même forme."""
    return re.sub(r"\s+", "", siret or "")


def siret_format_valide(siret: str | None) -> bool:
    """14 chiffres exactement (établissement). Un SIREN seul (9 chiffres,
    entreprise — déjà présent sur `leads.siren`, alimenté par le scraping)
    n'identifie pas un établissement précis, insuffisant pour ce contrôle."""
    return bool(_MOTIF_SIRET.match(normaliser_siret(siret)))


def verifier_siret_sirene(siret: str | None) -> dict:
    """Interroge l'API Recherche d'Entreprises (SIRENE) pour un SIRET
    déclaré — vérifie qu'il existe ET correspond à un établissement ACTIF
    ('etat_administratif' == 'A', à la fois pour l'entreprise et pour
    l'établissement précis visé par ce SIRET, pas seulement son siège).

    Ne lève JAMAIS d'exception (appelée depuis une page publique non
    authentifiée, dashboard/pages_publiques.py::afficher_intake) : toute
    panne réseau/API se traduit par un message dans "erreur", trouve=False,
    actif=False — jamais un traceback.

    Renvoie {"trouve": bool, "actif": bool, "nom_entreprise": str|None,
    "erreur": str|None}. "erreur" n'est renseigné que pour une panne
    technique (format invalide, réseau, API) — un SIRET simplement
    introuvable/inactif est un résultat normal (trouve=False, erreur=None),
    pas une erreur."""
    siret = normaliser_siret(siret)
    resultat_vide = {"trouve": False, "actif": False, "nom_entreprise": None, "erreur": None}

    if not siret_format_valide(siret):
        return {**resultat_vide, "erreur": "Format SIRET invalide (14 chiffres attendus)."}

    try:
        reponse = requests.get(
            SIRENE_API_URL, params={"q": siret, "per_page": 1}, timeout=TIMEOUT_SECONDES,
        )
        if reponse.status_code != 200:
            return {**resultat_vide, "erreur": f"API SIRENE : code HTTP {reponse.status_code}."}
        donnees = reponse.json()
    except requests.exceptions.RequestException as e:
        return {**resultat_vide, "erreur": f"API SIRENE injoignable : {e}"}
    except ValueError:
        return {**resultat_vide, "erreur": "API SIRENE : réponse non-JSON."}

    resultats = donnees.get("results") or []
    if not resultats:
        return resultat_vide

    entreprise = resultats[0]
    etablissements = entreprise.get("matching_etablissements") or []
    siege = entreprise.get("siege") or {}
    etablissement_vise = next(
        (e for e in etablissements if e.get("siret") == siret),
        siege if siege.get("siret") == siret else None,
    )
    if not etablissement_vise:
        # L'API a renvoyé un résultat mais pour un SIRET/SIREN différent
        # (recherche floue) — traité comme "non trouvé" plutôt que de
        # risquer de valider le mauvais établissement.
        return resultat_vide

    actif = (
        entreprise.get("etat_administratif") == "A"
        and etablissement_vise.get("etat_administratif") == "A"
    )
    nom_entreprise = entreprise.get("nom_complet") or entreprise.get("nom_raison_sociale")
    return {"trouve": True, "actif": actif, "nom_entreprise": nom_entreprise, "erreur": None}
