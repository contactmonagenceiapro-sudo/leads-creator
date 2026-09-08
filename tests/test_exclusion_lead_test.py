"""
Test de régression pour le correctif m1 (voir audit/audit_verification_2026-09-08.md) :
le lead de test __TEST_E2E_TUNNEL__ (constants.LEADS_TEST_A_EXCLURE)
n'était filtré nulle part en dehors de dashboard/data_access.py — ni dans
scorer_leads.py::rescorer_leads_existants() (le rescorait comme un lead
réel), ni dans livraison_devis.py::_artisans_clients_actifs() (l'aurait
traité comme artisan client actif s'il passait un jour à status='paye').

Toute interaction réseau/Supabase est mockée : aucun appel réel.

Usage : python3 -m unittest tests.test_exclusion_lead_test -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from constants import LEADS_TEST_A_EXCLURE


class TestScorerLeadsExcluLeadTest(unittest.TestCase):
    def test_rescorer_leads_existants_exclut_le_lead_de_test(self):
        import scorer_leads

        reponse_get = MagicMock()
        reponse_get.raise_for_status.return_value = None
        reponse_get.json.return_value = []

        with patch.object(scorer_leads, "SUPABASE_URL", "https://x.supabase.co"), \
             patch.object(scorer_leads, "SUPABASE_KEY", "fake-key"), \
             patch.object(scorer_leads.requests, "get", return_value=reponse_get) as mock_get:
            scorer_leads.rescorer_leads_existants()

        params_envoyes = mock_get.call_args.kwargs["params"]
        self.assertIn("id", params_envoyes)
        for lead_id in LEADS_TEST_A_EXCLURE:
            self.assertIn(lead_id, params_envoyes["id"])
        self.assertTrue(params_envoyes["id"].startswith("not.in."))


class TestLivraisonDevisExcluLeadTest(unittest.TestCase):
    def test_artisans_clients_actifs_exclut_le_lead_de_test(self):
        import livraison_devis

        requete_leads = MagicMock()
        requete_leads.select.return_value.eq.return_value.not_.in_.return_value.execute.return_value = \
            SimpleNamespace(data=[])
        supabase_factice = MagicMock()
        supabase_factice.table.return_value = requete_leads

        with patch.object(livraison_devis, "supabase", supabase_factice):
            resultat = livraison_devis._artisans_clients_actifs()

        self.assertEqual(resultat, [])
        requete_leads.select.return_value.eq.return_value.not_.in_.assert_called_once_with(
            "id", LEADS_TEST_A_EXCLURE
        )


if __name__ == "__main__":
    unittest.main()
