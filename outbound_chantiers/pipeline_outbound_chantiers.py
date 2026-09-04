#!/usr/bin/env python3
"""
pipeline_outbound_chantiers.py
===============================

Script maître du département "Outbound & Génération de Chantiers",
sur le même principe que pipeline.py (racine du projet) : orchestre en
séquence, de façon bloquante et en un seul processus, les modules 1 à 4 —
ce qui évite d'avoir à deviner des délais d'attente entre étapes côté n8n
(cf. méthodologie de mise en production, section "orchestration").

Étapes :
    1. Sourcing des acteurs professionnels   (sourcing_acteurs_pro.py)
    2. Filtrage + enrichissement contact réel (enrichir_acteurs_pro.py)
    3. Scoring + publication en base          (scorer_et_publier.py)
    4. Campagne d'envoi + relances            (outbound_pro_btp.py) — optionnel

Utilisation :
    python3 -m outbound_chantiers.pipeline_outbound_chantiers                  # 1+2+3
    python3 -m outbound_chantiers.pipeline_outbound_chantiers --avec-envoi     # 1+2+3+4
    python3 -m outbound_chantiers.pipeline_outbound_chantiers --envoi-seul     # 4 seul (relances quotidiennes)

Code de sortie : 0 si toutes les étapes exécutées ont réussi, 1 sinon.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PIPELINE-PRO] %(message)s")
log = logging.getLogger(__name__)


def executer_etape(nom: str, fonction) -> bool:
    log.info(f"--- Étape : {nom} ---")
    debut = time.monotonic()
    try:
        fonction()
        log.info(f"OK ({time.monotonic() - debut:.1f}s) : {nom}")
        return True
    except Exception as e:
        log.error(f"ÉCHEC ({time.monotonic() - debut:.1f}s) : {nom} — {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline maître — Outbound & Génération de Chantiers")
    parser.add_argument("--avec-envoi", action="store_true", help="Enchaîne aussi la campagne d'envoi (module 4)")
    parser.add_argument("--envoi-seul", action="store_true", help="N'exécute que le module 4 (relances quotidiennes)")
    args = parser.parse_args()

    etapes_ok = []

    if not args.envoi_seul:
        from outbound_chantiers.sourcing_acteurs_pro import sourcer_acteurs_pro
        from outbound_chantiers.enrichir_acteurs_pro import filtrer_et_enrichir
        from outbound_chantiers.scorer_et_publier import scorer_et_publier

        # sourcer_acteurs_pro()/filtrer_et_enrichir() écrivent déjà elles-mêmes
        # leur résultat sur disque (FICHIER_SORTIE, scopé par CLIENT_FINAL
        # depuis le fix du 04/09/2026) : les anciens wrappers ré-écrivaient en
        # plus sur un chemin fixe non scopé par client, jamais relu par
        # personne (filtrer_et_enrichir()/scorer_et_publier() lisent toujours
        # depuis FICHIER_ENTREE, pas depuis la valeur de retour) — code mort
        # qui, en plus, aurait pu faire fuiter des acteurs d'une campagne vers
        # une autre en cas de runs concurrents. Retiré plutôt que corrigé.
        etapes_ok.append(executer_etape("Sourcing des acteurs professionnels", sourcer_acteurs_pro))
        etapes_ok.append(executer_etape("Filtrage + enrichissement", filtrer_et_enrichir))
        etapes_ok.append(executer_etape("Scoring + publication en base", scorer_et_publier))

    if args.avec_envoi or args.envoi_seul:
        from outbound_chantiers.outbound_pro_btp import lancer_campagne_initiale, lancer_relances

        etapes_ok.append(executer_etape("Campagne d'envoi initiale", lancer_campagne_initiale))
        etapes_ok.append(executer_etape("Relances automatiques", lancer_relances))

    succes = all(etapes_ok) if etapes_ok else False
    log.info("Pipeline terminé : " + ("succès" if succes else "au moins un échec — voir logs ci-dessus"))
    return 0 if succes else 1


if __name__ == "__main__":
    sys.exit(main())
