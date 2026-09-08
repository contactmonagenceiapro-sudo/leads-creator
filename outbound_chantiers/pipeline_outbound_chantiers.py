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

from alertes import CompteZohoBloqueError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PIPELINE-PRO] %(message)s")
log = logging.getLogger(__name__)

# Code de sortie distinct pour un blocage du compte Zoho (voir main() et
# scripts/lancer_pipeline_b2b.py, qui l'utilise pour interrompre sa boucle
# sur les campagnes actives — constat m10, audit/audit_verification_2026-09-08.md) :
# le compte Zoho est partagé par TOUTES les campagnes, un blocage sur l'une
# échouera de façon certaine sur toutes les suivantes.
CODE_SORTIE_ZOHO_BLOQUE = 2


def executer_etape(nom: str, fonction) -> bool:
    log.info(f"--- Étape : {nom} ---")
    debut = time.monotonic()
    try:
        fonction()
        log.info(f"OK ({time.monotonic() - debut:.1f}s) : {nom}")
        return True
    except CompteZohoBloqueError:
        # Propagée (pas absorbée en simple False) : main() a besoin de
        # distinguer ce cas précis pour renvoyer CODE_SORTIE_ZOHO_BLOQUE —
        # voir constat m10.
        log.error(f"ÉCHEC ({time.monotonic() - debut:.1f}s) : {nom} — compte Zoho bloqué")
        raise
    except Exception as e:
        log.error(f"ÉCHEC ({time.monotonic() - debut:.1f}s) : {nom} — {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline maître — Outbound & Génération de Chantiers")
    parser.add_argument("--avec-envoi", action="store_true", help="Enchaîne aussi la campagne d'envoi (module 4)")
    parser.add_argument("--envoi-seul", action="store_true", help="N'exécute que le module 4 (relances quotidiennes)")
    args = parser.parse_args()

    etapes_ok = []

    try:
        if not args.envoi_seul:
            from outbound_chantiers.sourcing_acteurs_pro import sourcer_acteurs_pro
            from outbound_chantiers.enrichir_acteurs_pro import filtrer_et_enrichir
            from outbound_chantiers.scorer_et_publier import scorer_et_publier

            # sourcer_acteurs_pro()/filtrer_et_enrichir() écrivent elles-mêmes leur
            # résultat sur disque (FICHIER_SORTIE, scopé par CLIENT_FINAL depuis le
            # fix du 04/09/2026) AVANT de retourner leur valeur — y compris quand
            # elles sont appelées ici comme de simples fonctions (pas seulement
            # depuis leur bloc __main__ CLI). C'est volontaire : filtrer_et_enrichir()/
            # scorer_et_publier() lisent toujours depuis FICHIER_ENTREE sur disque,
            # jamais depuis une valeur de retour transmise en mémoire (voir fix du
            # 08/09/2026 après l'incident du sourcing B2B silencieusement cassé
            # depuis le 04/09/2026 — audit/audit_verification_2026-09-08.md,
            # constat C1 : avant ce fix, l'écriture n'avait lieu que dans le bloc
            # __main__, jamais quand ces fonctions étaient appelées d'ici).
            # Les anciens wrappers de ce fichier ré-écrivaient en plus sur un
            # chemin fixe non scopé par client, jamais relu par personne — code
            # mort qui, en plus, aurait pu faire fuiter des acteurs d'une campagne
            # vers une autre en cas de runs concurrents. Retiré (pas corrigé) le
            # 04/09/2026.
            etapes_ok.append(executer_etape("Sourcing des acteurs professionnels", sourcer_acteurs_pro))
            etapes_ok.append(executer_etape("Filtrage + enrichissement", filtrer_et_enrichir))
            etapes_ok.append(executer_etape("Scoring + publication en base", scorer_et_publier))

        if args.avec_envoi or args.envoi_seul:
            from outbound_chantiers.outbound_pro_btp import lancer_campagne_initiale, lancer_relances

            etapes_ok.append(executer_etape("Campagne d'envoi initiale", lancer_campagne_initiale))
            etapes_ok.append(executer_etape("Relances automatiques", lancer_relances))
    except CompteZohoBloqueError:
        # Voir CODE_SORTIE_ZOHO_BLOQUE ci-dessus et constat m10 : signal
        # distinct de "0 ou 1" pour que scripts/lancer_pipeline_b2b.py
        # puisse interrompre sa boucle sur les campagnes actives restantes
        # plutôt que de retenter (compte Zoho partagé, échec certain).
        log.error("Pipeline interrompu : compte Zoho bloqué.")
        return CODE_SORTIE_ZOHO_BLOQUE

    succes = all(etapes_ok) if etapes_ok else False
    log.info("Pipeline terminé : " + ("succès" if succes else "au moins un échec — voir logs ci-dessus"))
    return 0 if succes else 1


if __name__ == "__main__":
    sys.exit(main())
