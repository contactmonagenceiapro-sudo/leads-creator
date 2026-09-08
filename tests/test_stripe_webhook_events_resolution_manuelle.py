"""
Test de régression pour le correctif m5 (voir audit/audit_verification_2026-09-08.md) :
le bouton de secours du dashboard (marquer_contrat_paye,
marquer_demande_devis_payee_et_livree) mettait à jour
contracts/demandes_devis_particuliers sans jamais toucher à la ligne
stripe_webhook_events correspondante — une ligne 'echec' déjà corrigée à
la main restait signalée indéfiniment par
controle_sante_bdd.py::controler_paiements_stripe_en_echec() (constat m4,
même vague).

Toute interaction Supabase est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_stripe_webhook_events_resolution_manuelle -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import data_access  # noqa: E402


class TestMarquerEvenementsStripeResolusManuellement(unittest.TestCase):
    def test_met_a_jour_le_bon_chemin_jsonb_et_la_bonne_valeur(self):
        supabase_factice = MagicMock()
        chaine = supabase_factice.table.return_value.update.return_value.eq.return_value.eq.return_value
        chaine.execute.return_value = SimpleNamespace(data=[{"id": "evt-1"}])

        with patch.object(data_access, "supabase", supabase_factice):
            data_access._marquer_evenements_stripe_resolus_manuellement("contract_id", "c1")

        supabase_factice.table.assert_called_with("stripe_webhook_events")
        appel_update = supabase_factice.table.return_value.update.call_args[0][0]
        self.assertEqual(appel_update["statut"], "traite")
        appel_eq1 = supabase_factice.table.return_value.update.return_value.eq.call_args_list[0]
        self.assertEqual(appel_eq1[0], ("statut", "echec"))
        appel_eq2 = supabase_factice.table.return_value.update.return_value.eq.return_value.eq.call_args
        self.assertEqual(appel_eq2[0], ("payload->data->object->metadata->>contract_id", "c1"))

    def test_erreur_supabase_ne_leve_jamais(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.side_effect = \
            Exception("panne réseau")

        with patch.object(data_access, "supabase", supabase_factice):
            try:
                data_access._marquer_evenements_stripe_resolus_manuellement("demande_id", "d1")
            except Exception as e:  # pragma: no cover - échec du test si levée
                self.fail(f"Ne doit jamais lever, a levé : {e}")


class TestBoutonsDeSecoursAppellentLaResolution(unittest.TestCase):
    def test_marquer_contrat_paye_appelle_la_resolution_stripe(self):
        contrat = {"id": "c1", "lead_id": "l1"}
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[contrat])
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access, "_marquer_evenements_stripe_resolus_manuellement") as mock_resolution, \
             patch.object(data_access, "get_contracts"), \
             patch.object(data_access, "journaliser_action_admin"):
            data_access.marquer_contrat_paye("c1", "pi_123")

        mock_resolution.assert_called_once_with("contract_id", "c1")

    def test_marquer_demande_devis_payee_et_livree_appelle_la_resolution_stripe(self):
        demande = {
            "id": "d1", "statut": "proposee", "lead_id_livraison": "l1",
            "nom": "Client", "email": "c@example.com", "telephone": None, "commune": None, "message": None,
        }
        artisan = {"id": "l1", "email": "artisan@example.com"}
        supabase_factice = MagicMock()

        def table(nom):
            m = MagicMock()
            if nom == "demandes_devis_particuliers":
                m.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[demande])
                m.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
            elif nom == "leads":
                m.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[artisan])
            return m

        supabase_factice.table.side_effect = table

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access, "_marquer_evenements_stripe_resolus_manuellement") as mock_resolution, \
             patch("ceo_agent.send_email_prospect", return_value=True), \
             patch.object(data_access, "get_demandes_devis"):
            data_access.marquer_demande_devis_payee_et_livree("d1", "pi_456")

        mock_resolution.assert_called_once_with("demande_id", "d1")


if __name__ == "__main__":
    unittest.main()
