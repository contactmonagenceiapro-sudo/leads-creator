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


def _controler_echeances() -> None:
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


def main() -> None:
    # Filet englobant, DISTINCT de l'alerte existante dans
    # _controler_echeances() : un crash avant ou en dehors de cette logique
    # (ex: get_echeances_a_relancer() qui échoue) sortait jusqu'ici en
    # erreur Python bruyante côté CI sans jamais déclencher d'alerte
    # Discord/e-mail — voir commit d231638 (même correctif sur
    # scripts/traiter_paiements_stripe.py). Ne remplace pas le mécanisme
    # d'alerte existant, s'ajoute en plus de lui.
    try:
        _controler_echeances()
    except Exception as e:
        log.error(f"Échec avant le contrôle des échéances : {e}")
        alertes.alerter_discord(f"🚨 Échec avant le contrôle des échéances (script interrompu) : {e}")
        raise


if __name__ == "__main__":
    main()
