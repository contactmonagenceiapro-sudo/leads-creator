#!/usr/bin/env python3
"""
Module 8 (pilotage) — qualité des données leads : doublons potentiels,
champs manquants sur les leads actifs, enrichissement B2B stagnant.

Réutilise directement dashboard/data_access.py::score_qualite_leads() (même
logique que la page dashboard/app_pages/qualite_leads.py — pas de calcul
dupliqué) : ce script n'est qu'un habillage CLI pour un lancement à la
demande ou via un cron optionnel, PAS un système d'alerte automatique (voir
data_access.py pour la justification de l'absence de table dédiée ici).

Usage : python3 scripts/controle_qualite_leads.py
"""

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "dashboard"))

from data_access import score_qualite_leads  # noqa: E402


def main() -> None:
    r = score_qualite_leads()

    print("=== Qualité des données leads ===")
    print(f"Score global : {r['score']}/100")
    print(f"Groupes de doublons potentiels : {r['nb_groupes_doublons']}")
    print(f"Leads actifs sans e-mail : {r['taux_sans_email_pct']}%")
    print(f"Leads pro non enrichis depuis >{r['enrichissement']['seuil_jours']}j : {r['pourcentage_enrichissement_stagnant']}%")
    print()

    print("--- Doublons potentiels (leads B2C) ---")
    for champ, groupes in r["doublons_leads"].items():
        for g in groupes:
            noms = ", ".join(l["company"] for l in g["lignes"])
            print(f"  [{champ}] {g['valeur']!r} -> {noms}")

    print("--- Doublons/partages potentiels (leads pro B2B, au sein d'une même campagne) ---")
    print("  (téléphone/SIREN identiques : peut être un vrai doublon OU un numéro mal attribué par l'enrichissement — à vérifier au cas par cas)")
    for champ, groupes in r["doublons_leads_professionnels"].items():
        for g in groupes:
            noms = ", ".join(l["nom_entreprise"] for l in g["lignes"])
            print(f"  [{champ}] {g['valeur']!r} (campagne {g['client_final']}) -> {noms}")

    print()
    print(f"--- Champs manquants (leads B2C actifs, {r['manquants_leads']['total_actifs']} au total) ---")
    print(f"  Sans e-mail (non contactables) : {len(r['manquants_leads']['sans_email'])}")
    print(f"  Sans téléphone : {len(r['manquants_leads']['sans_telephone'])}")

    print(f"--- Champs manquants (leads pro actifs, {r['manquants_leads_professionnels']['total_actifs']} au total) ---")
    print(f"  Sans e-mail : {len(r['manquants_leads_professionnels']['sans_email'])}")
    print(f"  Sans téléphone : {len(r['manquants_leads_professionnels']['sans_telephone'])}")

    print()
    e = r["enrichissement"]
    print(f"--- Enrichissement B2B stagnant (non_tente depuis >{e['seuil_jours']}j) ---")
    print(f"  {e['nb_stagnants']}/{e['total']} ({e['pourcentage_stagnants']}%)")


if __name__ == "__main__":
    main()
