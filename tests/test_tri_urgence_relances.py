"""
Test de régression pour le correctif m14 (voir audit/audit_verification_2026-09-08.md) :
lancer_relances() (outbound_chantiers/outbound_pro_btp.py) n'avait aucun
tri par urgence, contrairement à lancer_campagne_initiale
(order=score_final.desc) — si le budget quotidien de warmup s'épuisait en
cours de boucle, l'ordre de traitement ne priorisait pas les relances les
plus en retard.

Toute interaction réseau (supabase_get) est mockée : aucun appel réel.

Usage : python3 -m unittest tests.test_tri_urgence_relances -v
"""
import unittest
from unittest.mock import patch

import outbound_chantiers.outbound_pro_btp as opb


class TestTriUrgenceRelances(unittest.TestCase):
    def test_requete_triee_par_derniere_relance_puis_contact(self):
        with patch.object(opb, "statut_ramp_warmup", return_value={"budget_restant": 10, "jour_ramp": 1, "envoyes_aujourdhui": 0, "plafond_jour": 10}), \
             patch.object(opb, "supabase_get", return_value=[]) as mock_get, \
             patch.object(opb, "emails_blacklistes", return_value=set()):
            opb.lancer_relances()

        params_envoyes = mock_get.call_args[0][0]
        self.assertIn(
            "order=last_relance_at.asc.nullsfirst,contacted_at.asc", params_envoyes,
            "lancer_relances() doit trier par urgence (constat m14) — sinon "
            "un épuisement du budget en cours de boucle ne priorise pas les "
            "relances les plus en retard.",
        )

    def test_filtre_statut_toujours_present(self):
        """Le tri ajouté ne doit pas remplacer le filtre existant."""
        with patch.object(opb, "statut_ramp_warmup", return_value={"budget_restant": 10, "jour_ramp": 1, "envoyes_aujourdhui": 0, "plafond_jour": 10}), \
             patch.object(opb, "supabase_get", return_value=[]) as mock_get, \
             patch.object(opb, "emails_blacklistes", return_value=set()):
            opb.lancer_relances()

        params_envoyes = mock_get.call_args[0][0]
        self.assertIn("statut=eq.contacte_attente_reponse", params_envoyes)
        self.assertIn(f"client_final=eq.{opb.CLIENT_FINAL}", params_envoyes)


if __name__ == "__main__":
    unittest.main()
