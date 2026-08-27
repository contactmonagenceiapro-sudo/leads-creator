#!/usr/bin/env python3
"""
Module 7 (pilotage) — alerte hebdomadaire si le taux de hard bounce dépasse
SEUIL_ALERTE_TAUX_BOUNCE (5 %) sur les 30 derniers jours, sur l'un ou l'autre
segment (voir dashboard/data_access.py::get_taux_bounce).

Réutilise alertes.py (Discord si DISCORD_WEBHOOK_URL configuré, repli
e-mail sinon) — même mécanisme que scripts/controle_echeances.py.

Usage : python3 scripts/controle_delivrabilite.py
Déclenché hebdomadairement par .github/workflows/controle_delivrabilite.yml.
"""

import logging
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "dashboard"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import alertes  # noqa: E402
from data_access import SEUIL_ALERTE_TAUX_BOUNCE, get_taux_bounce  # noqa: E402

NOMS_SEGMENTS = {"lead_artisan": "Artisans", "lead_professionnel": "B2B"}


def main() -> None:
    stats = get_taux_bounce(jours=30)

    depassements = []
    for cle, nom in NOMS_SEGMENTS.items():
        segment = stats[cle]
        if segment["envoyes"] and segment["taux_bounce"] > SEUIL_ALERTE_TAUX_BOUNCE:
            depassements.append((nom, segment))

    if not depassements:
        log.info(
            f"Taux de bounce sous le seuil ({SEUIL_ALERTE_TAUX_BOUNCE:.0%}) sur les deux "
            f"segments (30 derniers jours) : {stats}"
        )
        return

    lignes = "\n".join(
        f"- **{nom}** : {segment['taux_bounce']:.1%} de bounces ({segment['bounces']}/{segment['envoyes']} envois)"
        for nom, segment in depassements
    )
    message = (
        f"📉 **Taux de bounce au-dessus du seuil ({SEUIL_ALERTE_TAUX_BOUNCE:.0%}) sur 30 jours**\n{lignes}"
    )

    if alertes.DISCORD_WEBHOOK_URL:
        alertes.alerter_discord(message)
        log.info("Alerte Discord envoyée.")
    else:
        log.warning(
            "DISCORD_WEBHOOK_URL non configuré — impossible d'alerter sur Discord. "
            "Repli sur l'e-mail d'alerte (voir alertes.envoyer_alerte_email)."
        )
        alertes.envoyer_alerte_email("📉 Taux de bounce anormal — délivrabilité e-mail", message)
        log.info("Alerte e-mail envoyée (repli).")


if __name__ == "__main__":
    main()
