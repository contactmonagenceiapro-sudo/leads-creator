#!/usr/bin/env python3
"""
Module 5 (pilotage) — alerte hebdomadaire sur les échéances légales/
administratives à moins de 30 jours et encore 'a_traiter' (voir
sql/init_echeances.sql, dashboard/data_access.py::get_echeances_a_relancer).

Réutilise alertes.py (Discord si DISCORD_WEBHOOK_URL configuré, repli
e-mail sinon) — même mécanisme que scripts/controle_sante_bdd.py.

Usage : python3 scripts/controle_echeances.py
Déclenché hebdomadairement par .github/workflows/controle_echeances.yml.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "dashboard"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import alertes  # noqa: E402
from data_access import get_echeances_a_relancer  # noqa: E402


def main() -> None:
    resultat = get_echeances_a_relancer()
    echeances = resultat["echeances"]

    if not echeances:
        log.info(f"Aucune échéance à moins de {resultat['seuil_jours']} jours.")
        return

    aujourdhui = datetime.now(timezone.utc).date().isoformat()
    lignes = "\n".join(
        f"- **{e['type']}** — {e['description']} (échéance : {e['date_echeance']})"
        + (" ⚠️ DÉPASSÉE" if e["date_echeance"] < aujourdhui else "")
        for e in echeances
    )
    message = f"📅 **{len(echeances)} échéance(s) à traiter sous {resultat['seuil_jours']} jours**\n{lignes}"

    if alertes.DISCORD_WEBHOOK_URL:
        alertes.alerter_discord(message)
        log.info(f"{len(echeances)} échéance(s) — alerte Discord envoyée.")
    else:
        log.warning(
            "DISCORD_WEBHOOK_URL non configuré — impossible d'alerter sur Discord. "
            "Repli sur l'e-mail d'alerte (voir alertes.envoyer_alerte_email)."
        )
        alertes.envoyer_alerte_email(f"📅 {len(echeances)} échéance(s) à traiter sous {resultat['seuil_jours']} jours", message)
        log.info(f"{len(echeances)} échéance(s) — alerte e-mail envoyée (repli).")


if __name__ == "__main__":
    main()
