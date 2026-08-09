#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scorer_leads.py
================

Scoring de qualité des leads artisans (B2C, table `leads`) — même principe
que outbound_chantiers/scorer_et_publier.py côté B2B, adapté aux données
réellement disponibles ici (voir diagnostic du 2026-08-09/10) :

    score_activite = score_pour_commune(commune)     # signal Sitadel3/SDES, national
    bonus_taille   = score_taille_entreprise(tranche) x BONUS_MAX_TAILLE_ENTREPRISE
    score_final    = round(min(score_activite + bonus_taille, 1.0) * 100)

Échelle 0-100 (pas 0-1 comme leads_professionnels.score_final) : `leads.score`
est un INTEGER (sql/init.sql), convention déjà en place avant ce module (les
quelques scores résiduels d'un ancien scraper disparu étaient sur cette
échelle) — respectée plutôt que réinventée.

Le secteur (industry) reste NEUTRE (aucun poids) : aucune donnée de
conversion ne permet aujourd'hui de dire objectivement qu'un corps de métier
convertit/paie mieux qu'un autre (décision explicite, faute de recul — même
limite que côté B2B).

Contrairement à outbound_chantiers/signal_activite_chantiers.py::COMMUNES_CIBLES
(scope = la campagne B2B active, ex: Métropole de Lyon), les leads artisans
couvrent deux zones historiques (Grand Est + Métropole de Lyon) : la
normalisation doit donc porter sur les communes RÉELLEMENT présentes dans
`leads`, jamais sur la config d'une autre campagne B2B — d'où l'appel
explicite à recuperer_activite_par_commune(communes=...) plutôt qu'à son
défaut.

La table de correspondance tranche d'effectif -> score est dupliquée depuis
outbound_chantiers/scorer_et_publier.py (nomenclature INSEE standard, ne
varie pas) plutôt qu'importée depuis ce package : les deux segments
commerciaux (B2B pro / B2C artisans) ne doivent jamais dépendre l'un de
l'autre (voir sql/init_leads_professionnels.sql).

Usage :
    python3 -m scorer_leads
"""

import logging
import os

import requests
from dotenv import load_dotenv

from outbound_chantiers.signal_activite_chantiers import recuperer_activite_par_commune, score_pour_commune

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCORING-ARTISANS] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Code INSEE de tranche d'effectif salarié (établissement) -> score 0-1 —
# copie de outbound_chantiers/scorer_et_publier.py::SCORES_TRANCHE_EFFECTIF,
# voir ce module pour le détail du raisonnement (croissant puis plafonné).
SCORES_TRANCHE_EFFECTIF = {
    "00": 0.0, "01": 0.15, "02": 0.3, "03": 0.45, "11": 0.6, "12": 0.75,
    "21": 0.9, "22": 1.0, "31": 1.0, "32": 1.0, "41": 1.0, "42": 1.0,
    "51": 1.0, "52": 1.0, "53": 1.0,
}

# Plus élevé que côté B2B (0.05) : ici c'est le SEUL critère secondaire (pas
# de "type d'acteur" équivalent, le secteur restant neutre faute de recul) —
# voir le diagnostic validé avant implémentation.
BONUS_MAX_TAILLE_ENTREPRISE = 0.10


def score_taille_entreprise(tranche_effectif_salarie: str | None) -> float:
    return SCORES_TRANCHE_EFFECTIF.get(tranche_effectif_salarie or "", 0.0)


def calculer_score(commune: str | None, tranche_effectif_salarie: str | None, activite_par_commune: dict[str, float]) -> int:
    score_activite = score_pour_commune(commune or "", activite_par_commune)
    bonus = score_taille_entreprise(tranche_effectif_salarie) * BONUS_MAX_TAILLE_ENTREPRISE
    return round(min(score_activite + bonus, 1.0) * 100)


def activite_pour_communes(communes: list[str]) -> dict[str, float]:
    """Enveloppe fine autour de signal_activite_chantiers.py, pour que le
    reste du code B2C (lead_worker.py) n'ait jamais à importer directement
    depuis le package outbound_chantiers (B2B) — seul ce module le fait."""
    if not communes:
        return {}
    return recuperer_activite_par_commune(communes=communes)


def _communes_distinctes(leads: list[dict]) -> list[str]:
    return sorted({lead["commune"] for lead in leads if lead.get("commune")})


def rescorer_leads_existants() -> dict:
    """(Re)calcule et persiste le score de tous les leads déjà en base ayant
    une commune connue. Rejouable (idempotent) : un simple recalcul, jamais
    une opération destructive — aucun autre champ n'est touché, et un lead
    dont le score ne change pas n'est pas ré-écrit inutilement."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL/SUPABASE_KEY manquants — impossible de (re)scorer les leads.")
        return {}

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    reponse = requests.get(
        f"{SUPABASE_URL}/rest/v1/leads",
        params={"select": "id,commune,tranche_effectif_salarie,score"},
        headers=headers,
        timeout=20,
    )
    reponse.raise_for_status()
    leads = reponse.json()

    communes = _communes_distinctes(leads)
    log.info(f"{len(leads)} lead(s) chargé(s), {len(communes)} commune(s) distincte(s) à scorer.")
    activite_par_commune = activite_pour_communes(communes)
    log.info(f"Signal d'activité chantiers récupéré pour {len(activite_par_commune)} commune(s).")

    maj, inchanges, echecs = 0, 0, 0
    for lead in leads:
        nouveau_score = calculer_score(lead.get("commune"), lead.get("tranche_effectif_salarie"), activite_par_commune)
        if nouveau_score == lead.get("score"):
            inchanges += 1
            continue
        try:
            resp = requests.patch(
                f"{SUPABASE_URL}/rest/v1/leads",
                params={"id": f"eq.{lead['id']}"},
                json={"score": nouveau_score},
                headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            echecs += 1
            log.error(f"Erreur réseau mise à jour score pour lead {lead['id']} : {e}")
            continue
        if resp.status_code in (200, 204):
            maj += 1
        else:
            echecs += 1
            log.error(f"Échec mise à jour score pour lead {lead['id']} : {resp.status_code} {resp.text[:200]}")

    log.info(f"Scoring terminé : {maj} mis à jour, {inchanges} inchangé(s), {echecs} échec(s).")
    return {"maj": maj, "inchanges": inchanges, "echecs": echecs, "total": len(leads)}


if __name__ == "__main__":
    rescorer_leads_existants()
