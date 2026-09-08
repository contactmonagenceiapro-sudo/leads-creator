"""
Test de régression pour le correctif M3 (voir audit/audit_verification_2026-09-08.md) :
signaler_lead_pro_invalide() ne vérifiait pas que le lead signalé
appartient bien à la campagne (client_final) de l'appelant, contrairement à
creer_reclamation() qui fait ce contrôle explicitement — la seule
protection était le widget st.selectbox du portail client, pas une défense
en profondeur côté fonction d'écriture (IDOR potentiel : un compte client
aurait pu signaler comme invalide, et déclencher un avoir sur, un lead
d'une AUTRE campagne que la sienne).

Toute interaction Supabase est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_signaler_lead_pro_invalide_scoping -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import data_access  # noqa: E402


class TestScopingSignalerLeadProInvalide(unittest.TestCase):
    def _lead_pro(self, client_final="Client A"):
        return {
            "id": "lead-1", "nom_entreprise": "Cabinet Test", "client_final": client_final,
        }

    def test_client_ne_peut_pas_signaler_le_lead_d_une_autre_campagne(self):
        lead_dune_autre_campagne = self._lead_pro(client_final="Client B")
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[lead_dune_autre_campagne])

        with patch.object(data_access, "supabase", supabase_factice):
            with self.assertRaises(data_access.DataAccessError):
                data_access.signaler_lead_pro_invalide(
                    "lead-1", "hors zone", False, 0, client_final="Client A",
                )

    def test_client_peut_signaler_le_lead_de_sa_propre_campagne(self):
        lead_du_bon_client = self._lead_pro(client_final="Client A")
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[lead_du_bon_client])
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])
        supabase_factice.table.return_value.insert.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "remb-1"}])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.get_leads_pro, "clear"), \
             patch.object(data_access.get_remboursements, "clear"):
            resultat = data_access.signaler_lead_pro_invalide(
                "lead-1", "hors zone", False, 0, client_final="Client A",
            )

        self.assertEqual(resultat["remboursement"], {"id": "remb-1"})

    def test_admin_peut_signaler_sans_egard_a_client_final(self):
        """Bypass explicite pour un admin (même doctrine que creer_reclamation)
        — client_final=None ne doit jamais bloquer un appel admin."""
        lead_dune_campagne = self._lead_pro(client_final="Client B")
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[lead_dune_campagne])
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])
        supabase_factice.table.return_value.insert.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "remb-2"}])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.get_leads_pro, "clear"), \
             patch.object(data_access.get_remboursements, "clear"):
            resultat = data_access.signaler_lead_pro_invalide(
                "lead-1", "hors zone", True, 5000,
            )

        self.assertEqual(resultat["remboursement"], {"id": "remb-2"})

    def test_message_identique_lead_introuvable_ou_appartenant_a_autrui(self):
        """Le message d'erreur ne doit jamais laisser deviner si le lead
        n'existe pas ou appartient à un autre client (pas d'oracle
        d'énumération pour un compte client)."""
        supabase_absent = MagicMock()
        supabase_absent.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])
        with patch.object(data_access, "supabase", supabase_absent):
            with self.assertRaises(data_access.DataAccessError) as ctx_absent:
                data_access.signaler_lead_pro_invalide("inconnu", "motif", False, 0, client_final="Client A")

        supabase_autrui = MagicMock()
        supabase_autrui.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[self._lead_pro(client_final="Client B")])
        with patch.object(data_access, "supabase", supabase_autrui):
            with self.assertRaises(data_access.DataAccessError) as ctx_autrui:
                data_access.signaler_lead_pro_invalide("lead-1", "motif", False, 0, client_final="Client A")

        self.assertEqual(str(ctx_absent.exception), str(ctx_autrui.exception))


if __name__ == "__main__":
    unittest.main()
