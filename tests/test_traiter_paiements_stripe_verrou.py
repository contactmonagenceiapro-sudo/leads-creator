"""
Test de régression pour le correctif M7 (voir audit/audit_verification_2026-09-08.md) :
traiter_file_attente() (scripts/traiter_paiements_stripe.py) n'avait aucun
verrou par événement — un run manuel local aurait pu chevaucher le run cron
*/10min et traiter deux fois le même événement stripe_webhook_events
(double écriture journal_audit_admin, double alerte Discord).

Toute interaction Supabase est mockée : aucun appel réseau réel, aucun
appel Stripe réel.

Usage : python3 -m unittest tests.test_traiter_paiements_stripe_verrou -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import traiter_paiements_stripe as tps  # noqa: E402


class TestVerrouParEvenement(unittest.TestCase):
    def _evenement(self, id_="evt-1"):
        return {"id": id_, "payload": {"data": {"object": {"metadata": {"contract_id": "c1"}, "payment_intent": "pi_1"}}}}

    def test_evenement_deja_pris_en_charge_est_ignore(self):
        """Course perdue : l'UPDATE conditionnel n'affecte aucune ligne
        (déjà passé à 'en_cours' par un autre run) -> événement sauté, sans
        appeler _traiter_un_evenement ni retraiter."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = \
            SimpleNamespace(data=[self._evenement()])
        supabase_factice.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])  # 0 ligne affectée : course perdue

        with patch.object(tps, "supabase", supabase_factice), \
             patch.object(tps, "_traiter_un_evenement") as mock_traiter:
            tps.traiter_file_attente()

        mock_traiter.assert_not_called()
        # Vérifie que la tentative de verrou visait bien statut='recu' -> 'en_cours'.
        appel_verrou = supabase_factice.table.return_value.update.call_args_list[0]
        self.assertEqual(appel_verrou[0][0], {"statut": "en_cours"})

    def test_evenement_verrouille_avec_succes_est_traite(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = \
            SimpleNamespace(data=[self._evenement()])
        # Le premier UPDATE (verrou 'en_cours') réussit ; le second UPDATE
        # (finalisation 'traite') n'est pas vérifié ici en détail.
        supabase_factice.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "evt-1"}])
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "evt-1"}])

        with patch.object(tps, "supabase", supabase_factice), \
             patch.object(tps, "_traiter_un_evenement") as mock_traiter:
            tps.traiter_file_attente()

        mock_traiter.assert_called_once()

    def test_aucun_evenement_en_attente_ne_tente_aucun_verrou(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = \
            SimpleNamespace(data=[])

        with patch.object(tps, "supabase", supabase_factice), \
             patch.object(tps, "_traiter_un_evenement") as mock_traiter:
            tps.traiter_file_attente()

        mock_traiter.assert_not_called()
        supabase_factice.table.return_value.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
