"""
Test de régression pour le correctif M8 (voir audit/audit_verification_2026-09-08.md) :
supabase_patch() (outbound_chantiers/outbound_pro_btp.py) n'avait ni retry
ni alerte en cas d'échec de la mise à jour de statut APRÈS un envoi SMTP
réussi — l'acteur restait 'a_contacter' (ou relance_count inchangé) et
risquait d'être recontacté/relancé en double au run suivant, sans que
personne ne le sache avant de le constater en base.

Toute interaction réseau (requests.patch, alertes Discord) est mockée :
aucun appel réel.

Usage : python3 -m unittest tests.test_outbound_pro_btp_supabase_patch -v
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

import outbound_chantiers.outbound_pro_btp as opb


class TestSupabasePatchRetryEtAlerte(unittest.TestCase):
    def test_succes_du_premier_coup_aucun_retry_aucune_alerte(self):
        with patch.object(opb.requests, "patch", return_value=MagicMock()) as mock_patch, \
             patch.object(opb, "alerter_discord") as mock_alerte, \
             patch.object(opb.time, "sleep"):
            opb.supabase_patch("acteur-1", {"statut": "contacte_attente_reponse"})

        self.assertEqual(mock_patch.call_count, 1)
        mock_alerte.assert_not_called()

    def test_echec_puis_succes_ne_declenche_pas_d_alerte(self):
        with patch.object(
            opb.requests, "patch",
            side_effect=[requests.exceptions.RequestException("timeout"), MagicMock()],
        ) as mock_patch, \
             patch.object(opb, "alerter_discord") as mock_alerte, \
             patch.object(opb.time, "sleep") as mock_sleep:
            opb.supabase_patch("acteur-1", {"statut": "contacte_attente_reponse"})

        self.assertEqual(mock_patch.call_count, 2)
        mock_sleep.assert_called_once()
        mock_alerte.assert_not_called()

    def test_echec_definitif_alerte_discord(self):
        with patch.object(
            opb.requests, "patch",
            side_effect=requests.exceptions.RequestException("panne réseau"),
        ) as mock_patch, \
             patch.object(opb, "alerter_discord") as mock_alerte, \
             patch.object(opb.time, "sleep"):
            opb.supabase_patch("acteur-1", {"statut": "contacte_attente_reponse"})

        self.assertEqual(mock_patch.call_count, opb.NB_TENTATIVES_MAJ_STATUT)
        mock_alerte.assert_called_once()
        message = mock_alerte.call_args[0][0]
        self.assertIn("acteur-1", message)

    def test_echec_definitif_ne_leve_jamais(self):
        """supabase_patch() ne doit jamais lever d'exception vers
        l'appelant (lancer_campagne_initiale/lancer_relances) — l'e-mail
        est déjà parti, irréversible, la boucle doit continuer."""
        with patch.object(
            opb.requests, "patch",
            side_effect=requests.exceptions.RequestException("panne réseau"),
        ), patch.object(opb, "alerter_discord"), patch.object(opb.time, "sleep"):
            try:
                opb.supabase_patch("acteur-1", {"statut": "contacte_attente_reponse"})
            except Exception as e:  # pragma: no cover - échec du test si levée
                self.fail(f"supabase_patch() ne doit jamais lever d'exception, a levé : {e}")


if __name__ == "__main__":
    unittest.main()
