#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py
===========

Script maître orchestrant la chaîne complète de prospection :

    1. Scraping / mise à jour des leads   (scraper_batiment.py -> leads.json)
    2. Traitement IA + génération de pitchs personnalisés + upsert Supabase
       (lead_worker.py)
    3. Rapport de synthèse de l'exécution (et, en option, campagne d'envoi
       d'emails réelle via ceo_agent.py)

Utilisation :
    python pipeline.py                  # scraping + traitement IA + rapport
    python pipeline.py --skip-scraping  # réutilise le leads.json existant
    python pipeline.py --send-emails    # + déclenche l'envoi réel des emails
                                         #   de prospection (ceo_agent.py)

Code de sortie : 0 si toutes les étapes exécutées ont réussi, 1 sinon.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field


class FormatteurPrefixe(logging.Formatter):
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


def configurer_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatteurPrefixe())
    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.handlers.clear()
    racine.addHandler(handler)


log = logging.getLogger("pipeline")


@dataclass
class EtapeResultat:
    nom: str
    succes: bool
    duree_sec: float
    detail: str = ""


@dataclass
class RapportPipeline:
    etapes: list[EtapeResultat] = field(default_factory=list)

    def ajouter(self, nom: str, succes: bool, duree_sec: float, detail: str = "") -> None:
        self.etapes.append(EtapeResultat(nom, succes, duree_sec, detail))

    def tout_a_reussi(self) -> bool:
        return all(e.succes for e in self.etapes)

    def afficher(self) -> None:
        log.info("=" * 60)
        log.info("RAPPORT D'EXÉCUTION DU PIPELINE")
        log.info("=" * 60)
        for e in self.etapes:
            statut = "✅ SUCCÈS" if e.succes else "❌ ÉCHEC"
            log.info(f"{statut} | {e.nom:35s} | {e.duree_sec:6.1f}s | {e.detail}")
        log.info("=" * 60)
        if self.tout_a_reussi():
            log.info("Résultat global : SUCCÈS")
        else:
            log.error("Résultat global : ÉCHEC (voir détail ci-dessus)")


def etape_scraping(rapport: RapportPipeline) -> bool:
    log.info("--- Étape 1/3 : Scraping / mise à jour des leads ---")
    debut = time.time()
    try:
        import scraper_batiment

        scraper_batiment.main()
        duree = time.time() - debut
        rapport.ajouter("1. Scraping des leads", True, duree, "leads.json généré")
        return True
    except SystemExit as erreur:
        duree = time.time() - debut
        succes = erreur.code in (0, None)
        rapport.ajouter("1. Scraping des leads", succes, duree, f"sys.exit({erreur.code})")
        return succes
    except Exception as erreur:
        duree = time.time() - debut
        log.error(f"Échec du scraping : {erreur}")
        rapport.ajouter("1. Scraping des leads", False, duree, str(erreur))
        return False


def etape_traitement_ia(rapport: RapportPipeline) -> bool:
    log.info("--- Étape 2/3 : Traitement IA (Ollama) + upsert Supabase ---")
    debut = time.time()
    try:
        import lead_worker

        resultat = lead_worker.process_pipeline()
        duree = time.time() - debut
        succes = resultat.total > 0 and resultat.echecs == 0
        detail = f"{resultat.succes} succès / {resultat.echecs} échecs / {resultat.total} total"
        rapport.ajouter("2. Traitement IA + Supabase", succes, duree, detail)
        return succes
    except Exception as erreur:
        duree = time.time() - debut
        log.error(f"Échec du traitement IA : {erreur}")
        rapport.ajouter("2. Traitement IA + Supabase", False, duree, str(erreur))
        return False


def etape_rapport_final(rapport: RapportPipeline, envoyer_emails: bool) -> bool:
    log.info("--- Étape 3/3 : Rapport final" + (" + campagne d'emails" if envoyer_emails else "") + " ---")
    debut = time.time()
    if not envoyer_emails:
        duree = time.time() - debut
        rapport.ajouter("3. Rapport", True, duree, "envoi d'emails désactivé (--send-emails pour l'activer)")
        return True

    try:
        import ceo_agent

        ceo_agent.run_ceo_analysis()
        duree = time.time() - debut
        rapport.ajouter("3. Rapport + campagne d'emails", True, duree, "campagne exécutée (voir logs CEO ci-dessus)")
        return True
    except Exception as erreur:
        duree = time.time() - debut
        log.error(f"Échec de la campagne d'emails : {erreur}")
        rapport.ajouter("3. Rapport + campagne d'emails", False, duree, str(erreur))
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline complet de prospection ai-company")
    parser.add_argument("--skip-scraping", action="store_true", help="Réutilise leads.json existant sans relancer le scraping")
    parser.add_argument("--skip-worker", action="store_true", help="Ne relance pas le traitement IA / upsert Supabase")
    parser.add_argument("--send-emails", action="store_true", help="Déclenche réellement l'envoi des emails de prospection (ceo_agent.py)")
    args = parser.parse_args()

    configurer_logging()
    log.info("=== Lancement du pipeline ai-company ===")

    rapport = RapportPipeline()

    if args.skip_scraping:
        log.info("Étape 1/3 ignorée (--skip-scraping)")
    else:
        etape_scraping(rapport)

    if args.skip_worker:
        log.info("Étape 2/3 ignorée (--skip-worker)")
    else:
        etape_traitement_ia(rapport)

    etape_rapport_final(rapport, envoyer_emails=args.send_emails)

    rapport.afficher()
    return 0 if rapport.tout_a_reussi() else 1


if __name__ == "__main__":
    sys.exit(main())
