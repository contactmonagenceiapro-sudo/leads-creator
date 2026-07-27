#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_leads_commerciaux.py
=============================

Formalise l'export d'un lot de leads BTP prêt à livrer à un client B2B
(vente de leads), remplaçant les commandes Python ad-hoc tapées à la main
lors des campagnes précédentes.

Sépare toujours le lot en deux niveaux, cohérents avec ce qui a été livré
jusqu'ici :
    - premium_leads.csv   : leads avec email réellement vérifié (utilisable
      directement pour du démarchage par email/téléphone).
    - base_entreprises.csv : leads avec données d'entreprise réelles (SIREN,
      adresse, secteur) mais sans contact direct trouvé — utile pour un
      démarchage postal/téléphonique via recherche manuelle, PAS pour un
      envoi d'email automatisé (ils n'en ont pas).

Utilisation :
    source venv/bin/activate
    python export_leads_commerciaux.py
    python export_leads_commerciaux.py --source leads.json --sortie exports/lyon_2026-07
    python export_leads_commerciaux.py --secteur "Bâtiment - Plâtrerie" --ville Lyon
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname).1s] %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

COLONNES_PREMIUM = ["entreprise", "email", "telephone", "siren", "adresse", "secteur", "ville"]
COLONNES_BASE = ["entreprise", "siren", "adresse", "secteur", "ville"]


def charger_leads(chemin: Path) -> list[dict]:
    if not chemin.exists():
        log.error(f"Fichier introuvable : {chemin}")
        sys.exit(1)
    with open(chemin, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        log.error(f"{chemin} doit contenir une liste JSON, reçu : {type(data).__name__}")
        sys.exit(1)
    return data


def filtrer_leads(leads: list[dict], secteur: str | None, ville: str | None) -> list[dict]:
    resultat = leads
    if secteur:
        resultat = [l for l in resultat if l.get("industry") == secteur]
    if ville:
        resultat = [l for l in resultat if (l.get("ville") or "").lower() == ville.lower()]
    return resultat


def ligne_premium(lead: dict) -> list[str]:
    return [
        lead.get("company_name", ""),
        lead.get("email", ""),
        lead.get("telephone") or "",
        lead.get("siren", "") or "",
        lead.get("adresse", "") or "",
        lead.get("industry", ""),
        lead.get("ville", ""),
    ]


def ligne_base(lead: dict) -> list[str]:
    return [
        lead.get("company_name", ""),
        lead.get("siren", "") or "",
        lead.get("adresse", "") or "",
        lead.get("industry", ""),
        lead.get("ville", ""),
    ]


def ecrire_csv(chemin: Path, colonnes: list[str], lignes: list[list[str]]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(colonnes)
        w.writerows(lignes)


def exporter(source: Path, dossier_sortie: Path, secteur: str | None, ville: str | None) -> None:
    leads = charger_leads(source)
    leads = filtrer_leads(leads, secteur, ville)

    if not leads:
        log.warning("Aucun lead ne correspond aux filtres demandés, export vide.")
        return

    premium = [l for l in leads if l.get("email")]
    base = [l for l in leads if not l.get("email")]

    chemin_premium = dossier_sortie / "premium_leads.csv"
    chemin_base = dossier_sortie / "base_entreprises.csv"

    ecrire_csv(chemin_premium, COLONNES_PREMIUM, [ligne_premium(l) for l in premium])
    ecrire_csv(chemin_base, COLONNES_BASE, [ligne_base(l) for l in base])

    ratio = (len(premium) / len(leads) * 100) if leads else 0
    log.info("=== Résumé de l'export ===")
    log.info(f"Total leads exploitables       : {len(leads)}")
    log.info(f"  Premium (email vérifié)       : {len(premium)} ({ratio:.1f}%)")
    log.info(f"  Base entreprises (sans email) : {len(base)}")
    log.info(f"Répartition secteur (premium)   : {dict(Counter(l['industry'] for l in premium))}")
    log.info(f"Fichiers écrits dans            : {dossier_sortie}/")
    log.info(f"  - {chemin_premium.name}")
    log.info(f"  - {chemin_base.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export commercial d'un lot de leads BTP (vente B2B)")
    parser.add_argument("--source", default="leads.json", help="Fichier leads.json à exporter (défaut: leads.json)")
    parser.add_argument("--sortie", default=None, help="Dossier de sortie (défaut: exports/<horodatage>)")
    parser.add_argument("--secteur", default=None, help="Filtrer sur un secteur exact (ex: 'Bâtiment - Plâtrerie')")
    parser.add_argument("--ville", default=None, help="Filtrer sur une ville exacte (ex: 'Lyon 3e')")
    args = parser.parse_args()

    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d_%Hh%M")
    dossier_sortie = Path(args.sortie) if args.sortie else Path("exports") / horodatage

    exporter(Path(args.source), dossier_sortie, args.secteur, args.ville)
    return 0


if __name__ == "__main__":
    sys.exit(main())
