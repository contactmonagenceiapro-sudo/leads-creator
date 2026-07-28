"""
Enrichissement de numéro de téléphone via Google Places API — filet de
sécurité complémentaire au scraping du site propre de l'entreprise
(email_enricher.py pour les artisans, outbound_chantiers/enrichir_acteurs_pro.py
pour les architectes/promoteurs/maîtres d'œuvre), utilisé uniquement quand
cette première méthode n'a trouvé aucun téléphone.

Sources écartées, et pourquoi (vérifié en pratique, pas une supposition) :
- API SIRENE / "Annuaire des Entreprises" (recherche-entreprises.api.gouv.fr,
  déjà utilisée dans ce projet — voir outbound_chantiers/sourcing_acteurs_pro.py) :
  AUCUN champ téléphone dans la réponse, quelle que soit l'entreprise testée.
  Le registre officiel français ne collecte pas ce numéro à l'immatriculation.
- Pappers : source ses données du même registre (SIRENE/RCS/RNE) — pour la
  même raison, ne fournit pas non plus de téléphone.
- Scraping direct de Pages Jaunes : leurs CGU interdisent explicitement le
  scraping, et les numéros y sont généralement masqués derrière du
  JavaScript précisément pour bloquer ce type d'extraction. Ce projet
  utilise déjà PagesJaunes ailleurs (scraper_batiment.py) mais uniquement
  comme filet de SOURCING de secours (trouver des entreprises), jamais pour
  en extraire un contact — volontairement pas reproduit ici pour la même
  raison (risque ToS documenté à cet endroit-là).

Google Places retenu : les artisans/architectes/promoteurs ciblés sont des
commerces locaux presque toujours présents sur Google Maps avec un numéro
publié ; usage conforme aux CGU (API officielle, payante, ~0,02-0,03 €/
recherche sur le tarif "Find Place" + "Place Details" au 2026).

Activation OPTIONNELLE : sans GOOGLE_PLACES_API_KEY dans .env, cette brique
est silencieusement désactivée (renvoie toujours None) — jamais un
pré-requis bloquant pour le reste des pipelines qui l'appellent.
"""

import logging
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
TIMEOUT_SECONDES = 8

URL_RECHERCHE = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
URL_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"


def telephone_enrichissement_disponible() -> bool:
    return bool(GOOGLE_PLACES_API_KEY)


def _trouver_place_id(nom_entreprise: str, commune: str) -> str | None:
    try:
        reponse = requests.get(
            URL_RECHERCHE,
            params={
                "input": f"{nom_entreprise} {commune}".strip(),
                "inputtype": "textquery",
                "fields": "place_id",
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=TIMEOUT_SECONDES,
        )
        reponse.raise_for_status()
        data = reponse.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log.warning(f"Google Places (recherche) statut inattendu pour « {nom_entreprise} » : {data.get('status')}")
        candidats = data.get("candidates", [])
        return candidats[0]["place_id"] if candidats else None
    except requests.exceptions.RequestException as e:
        log.warning(f"Google Places (recherche) indisponible pour « {nom_entreprise} » : {e}")
        return None
    except (KeyError, ValueError, IndexError) as e:
        log.warning(f"Réponse Google Places (recherche) inattendue pour « {nom_entreprise} » : {e}")
        return None


def enrichir_telephone_via_google_places(nom_entreprise: str, commune: str) -> str | None:
    """Cherche le téléphone publié sur la fiche Google Business de
    l'entreprise (recherche par nom + commune, puis détails de la fiche
    trouvée). Renvoie None si non trouvé, non configuré (pas de clé), ou en
    cas d'erreur réseau — ne lève JAMAIS d'exception : un échec
    d'enrichissement reste un échec, jamais un crash du pipeline appelant
    (même contrat que email_enricher.py / enrichir_acteurs_pro.py)."""
    if not telephone_enrichissement_disponible():
        return None
    if not nom_entreprise:
        return None

    place_id = _trouver_place_id(nom_entreprise, commune or "")
    if not place_id:
        return None

    try:
        reponse = requests.get(
            URL_DETAILS,
            params={
                "place_id": place_id,
                "fields": "formatted_phone_number,international_phone_number",
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=TIMEOUT_SECONDES,
        )
        reponse.raise_for_status()
        resultat = reponse.json().get("result", {})
        return resultat.get("formatted_phone_number") or resultat.get("international_phone_number") or None
    except requests.exceptions.RequestException as e:
        log.warning(f"Google Places (détails) indisponible pour « {nom_entreprise} » : {e}")
        return None
    except (KeyError, ValueError) as e:
        log.warning(f"Réponse Google Places (détails) inattendue pour « {nom_entreprise} » : {e}")
        return None
