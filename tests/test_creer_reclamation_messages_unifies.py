"""
Test de régression pour le cas particulier signalé lors de la vague
précédente (voir audit/audit_verification_2026-09-08.md et le commit
2a68a84, signaler_lead_pro_invalide) : creer_reclamation() (dashboard/
data_access.py) distinguait deux messages différents selon qu'un lead
n'existe pas ou qu'il appartienne à un autre client — un compte client
pouvait ainsi deviner lequel des deux cas s'appliquait à un lead_id
qu'il ne possède pas. Traité ici avec le même remède que
signaler_lead_pro_invalide : message IDENTIQUE dans les deux cas.

Toute interaction Supabase est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_creer_reclamation_messages_unifies -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import data_access  # noqa: E402


def _supabase_avec(data):
    m = MagicMock()
    m.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=data)
    return m


def _message_erreur_b2c(demandes, client_lead_id):
    with patch.object(data_access, "supabase", _supabase_avec(demandes)):
        with unittest.TestCase().assertRaises(data_access.DataAccessError) as ctx:
            data_access.creer_reclamation(
                "b2c", "d1", data_access.MOTIFS_RECLAMATION[0], None, False, client_lead_id=client_lead_id,
            )
    return str(ctx.exception)


def _message_erreur_b2b(leads_pro, client_final):
    with patch.object(data_access, "supabase", _supabase_avec(leads_pro)):
        with unittest.TestCase().assertRaises(data_access.DataAccessError) as ctx:
            data_access.creer_reclamation(
                "b2b", "l1", data_access.MOTIFS_RECLAMATION[0], None, False, client_final=client_final,
            )
    return str(ctx.exception)


class TestMessagesUnifiesB2C(unittest.TestCase):
    def test_messages_identiques_introuvable_vs_appartient_a_autrui(self):
        message_introuvable = _message_erreur_b2c([], client_lead_id="c1")
        message_autrui = _message_erreur_b2c(
            [{"id": "d1", "statut": "livree", "lead_id_livraison": "autre-client"}],
            client_lead_id="moi",
        )

        self.assertEqual(
            message_introuvable, message_autrui,
            "creer_reclamation() ne doit jamais laisser deviner si la "
            "demande n'existe pas ou appartient à un autre client (cas "
            "particulier signalé, même traitement que 2a68a84).",
        )

    def test_admin_peut_toujours_reclamer_pour_n_importe_quel_client(self):
        demande = {
            "id": "d1", "statut": "livree", "lead_id_livraison": "autre-client",
            "livree_le": "2026-09-01T00:00:00+00:00",
        }
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[demande])
        supabase_factice.table.return_value.insert.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "r1"}])

        with patch.object(data_access, "supabase", supabase_factice):
            resultat = data_access.creer_reclamation(
                "b2c", "d1", data_access.MOTIFS_RECLAMATION[0], None, True,
            )

        self.assertIn("reclamation", resultat)


class TestMessagesUnifiesB2B(unittest.TestCase):
    def test_messages_identiques_introuvable_vs_appartient_a_autrui(self):
        message_introuvable = _message_erreur_b2b([], client_final="Client A")
        message_autrui = _message_erreur_b2b(
            [{"id": "l1", "client_final": "Client B", "created_at": "2026-09-01T00:00:00+00:00"}],
            client_final="Client A",
        )

        self.assertEqual(message_introuvable, message_autrui)


if __name__ == "__main__":
    unittest.main()
