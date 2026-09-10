"""
Test de régression pour l'angle mort identifié le 10/09/2026 : un crash
survenant AVANT d'entrer dans la boucle par-événement de
traiter_file_attente() (scripts/traiter_paiements_stripe.py) — ex. le SELECT
initial sur stripe_webhook_events qui échoue — n'était catché par aucun
try/except (l'except Exception existant est câblé par-événement, DANS la
boucle). Résultat : crash bruyant côté CI, mais AUCUNE alerte Discord —
précisément le cas le plus grave puisqu'aucun événement Stripe n'est traité
du tout.

Le correctif ajoute un filet englobant dans main(), distinct de l'alerte
par-événement existante (conservée intacte, voir
tests/test_traiter_paiements_stripe_verrou.py pour sa couverture).

Toute interaction Supabase est mockée : aucun appel réseau réel, aucun appel
Stripe réel, aucun appel Discord réel.

Usage : python3 -m unittest tests.test_traiter_paiements_stripe_crash_avant_boucle -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import traiter_paiements_stripe as tps  # noqa: E402


class TestFiletCrashAvantBoucle(unittest.TestCase):
    def test_crash_sur_le_select_declenche_alerter_discord(self):
        """traiter_file_attente() plante dès le SELECT (avant toute boucle
        par-événement) -> main() doit catcher, alerter Discord avec un
        message distinct du cas par-événement, puis relaisser l'exception
        remonter (le script doit rester en échec visible côté CI)."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.order.return_value.execute.side_effect = \
            RuntimeError("connexion Supabase refusée")

        with patch.object(tps, "supabase", supabase_factice), \
             patch.object(tps, "SUPABASE_URL", "https://exemple.supabase.co"), \
             patch.object(tps, "SUPABASE_KEY", "cle-factice"), \
             patch.object(tps, "alerter_discord") as mock_alerte:
            with self.assertRaises(RuntimeError):
                tps.main()

        mock_alerte.assert_called_once()
        message = mock_alerte.call_args[0][0]
        self.assertIn("avant traitement des événements", message.lower())

    def test_traitement_normal_n_appelle_pas_le_filet(self):
        """Cas nominal (aucun événement en attente) : le filet englobant ne
        doit rien déclencher — non-régression sur le chemin heureux."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []

        with patch.object(tps, "supabase", supabase_factice), \
             patch.object(tps, "SUPABASE_URL", "https://exemple.supabase.co"), \
             patch.object(tps, "SUPABASE_KEY", "cle-factice"), \
             patch.object(tps, "alerter_discord") as mock_alerte:
            tps.main()

        mock_alerte.assert_not_called()


if __name__ == "__main__":
    unittest.main()
