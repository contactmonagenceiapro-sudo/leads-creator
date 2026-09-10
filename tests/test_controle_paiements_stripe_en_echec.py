"""
Test de régression pour le correctif m4 (voir audit/audit_verification_2026-09-08.md) :
aucun contrôle ne surveillait les lignes stripe_webhook_events.statut='echec'
non résolues — seul filet auparavant : l'alerte Discord ponctuelle au
moment de l'échec (scripts/traiter_paiements_stripe.py), qui pouvait être
manquée/mal configurée. controle_sante_bdd.py::controler_paiements_stripe_en_echec()
comble ce trou, sur le modèle de controler_erreurs_non_resolues().

Toute interaction Supabase est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_controle_paiements_stripe_en_echec -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controle_sante_bdd as csb  # noqa: E402


class TestControlerPaiementsStripeEnEchec(unittest.TestCase):
    def _requete(self, count, data):
        requete = MagicMock()
        requete.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
            SimpleNamespace(count=count, data=data)
        return requete

    def test_statut_ok_sans_echec(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value = self._requete(0, [])

        with patch.object(csb, "supabase", supabase_factice):
            resultat = csb.controler_paiements_stripe_en_echec()

        self.assertEqual(resultat["statut"], "ok")
        self.assertEqual(resultat["detail"]["nombre_en_echec"], 0)

    def test_statut_attention_des_un_seul_echec(self):
        """Seuil à 0, contrairement à erreurs_non_resolues (seuil 10) —
        chaque échec est un paiement réel potentiellement non honoré."""
        evenement = {"id": "evt-1", "stripe_event_id": "evt_stripe_1", "erreur": "Contrat introuvable", "created_at": "2026-09-01T00:00:00Z"}
        supabase_factice = MagicMock()
        supabase_factice.table.return_value = self._requete(1, [evenement])

        with patch.object(csb, "supabase", supabase_factice):
            resultat = csb.controler_paiements_stripe_en_echec()

        self.assertEqual(
            resultat["statut"], "attention",
            "Un seul événement en échec doit déjà déclencher 'attention' — "
            "un paiement reçu mais jamais activé ne doit jamais rester "
            "invisible faute de seuil de tolérance (constat m4).",
        )
        self.assertEqual(resultat["detail"]["evenements"], [evenement])

    def test_filtre_bien_sur_statut_echec(self):
        supabase_factice = MagicMock()
        requete = self._requete(0, [])
        supabase_factice.table.return_value = requete

        with patch.object(csb, "supabase", supabase_factice):
            csb.controler_paiements_stripe_en_echec()

        requete.select.return_value.eq.assert_called_once_with("statut", "echec")

    def test_enregistre_dans_actions_concretes(self):
        self.assertIn("paiements_stripe_en_echec", csb.ACTIONS_CONCRETES)

    def test_cable_dans_main(self):
        """La logique métier vit dans _executer_controles() depuis le
        filet englobant ajouté dans main() (même correctif que le commit
        d231638 sur scripts/traiter_paiements_stripe.py) — le câblage réel
        se vérifie donc là, main() ne faisant plus qu'appeler cette
        fonction dans un try/except."""
        import inspect
        source = inspect.getsource(csb._executer_controles)
        self.assertIn("controler_paiements_stripe_en_echec", source)


if __name__ == "__main__":
    unittest.main()
