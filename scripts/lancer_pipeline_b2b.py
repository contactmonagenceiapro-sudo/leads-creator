#!/usr/bin/env python3
"""
scripts/lancer_pipeline_b2b.py
================================
Wrapper cron pour outbound_chantiers.pipeline_outbound_chantiers — lance le
pipeline pour CHAQUE campagne active (table `campagnes`, statut='active'),
avec la même construction d'environnement (OUTBOUND_CAMPAGNE_JSON) que le
bouton manuel du dashboard (voir dashboard/process_runner.py::construire_env_campagne),
mais sans dépendre d'un humain qui sélectionne une campagne dans le menu à
chaque run. Une campagne 'brouillon' n'est jamais incluse.

Remplace l'automatisation prévue via n8n (voir
outbound_chantiers/n8n_workflow_outbound_chantiers.md, workflows A et B) qui
ciblait un endpoint `{API_URL}/agent/trigger` — supprimé à la migration du
dashboard vers Streamlit Community Cloud (voir docker-compose.yml : "il n'y
a plus de backend FastAPI... à faire tourner ici"). Ce doc n8n décrit donc
une automatisation qui ne fonctionne plus depuis cette migration ; ce script
reprend les mêmes horaires prévus (lundi 6h sourcing, 9h quotidien
campagne — voir .github/workflows/outbound_chantiers_sourcing.yml et
outbound_chantiers_campagne.yml) mais via GitHub Actions, cohérent avec le
reste des automatisations du projet (voir docs/architecture_globale.md,
section "Pipelines et automatisations").

Usage :
    python3 scripts/lancer_pipeline_b2b.py                # sourcing seul (modules 1-3)
    python3 scripts/lancer_pipeline_b2b.py --envoi-seul    # campagne + relances (module 4)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from dotenv import load_dotenv  # noqa: E402
from supabase import create_client  # noqa: E402

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def campagnes_actives() -> list[dict]:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase.table("campagnes").select("*").eq("statut", "active").execute().data


def main() -> int:
    parser = argparse.ArgumentParser(description="Lance le pipeline B2B pour chaque campagne active")
    parser.add_argument("--envoi-seul", action="store_true", help="N'exécute que la campagne d'envoi + relances")
    args = parser.parse_args()

    campagnes = campagnes_actives()
    if not campagnes:
        print("Aucune campagne active — rien à faire.")
        return 0

    code_sortie = 0
    for campagne in campagnes:
        nom = campagne.get("nom_client")
        print(f"=== Campagne active : {nom} ===")
        env = os.environ.copy()
        env["OUTBOUND_CAMPAGNE_JSON"] = json.dumps(campagne, ensure_ascii=False)
        commande = [sys.executable, "-m", "outbound_chantiers.pipeline_outbound_chantiers"]
        if args.envoi_seul:
            commande.append("--envoi-seul")
        resultat = subprocess.run(commande, cwd=str(RACINE), env=env)
        if resultat.returncode != 0:
            print(f"ÉCHEC pour la campagne {nom} (code {resultat.returncode})")
            code_sortie = 1

    return code_sortie


if __name__ == "__main__":
    sys.exit(main())
