"""
Test de régression pour le correctif m11 (voir audit/audit_verification_2026-09-08.md) :
verifier_siret_sirene() (verification_pro.py) n'avait aucun retry/backoff,
contrairement au sourcing B2B (sourcing_acteurs_pro.py::interroger_sirene,
3 tentatives) — un hoquet réseau ponctuel faisait échouer en direct la
vérification SIRET depuis une page publique (dashboard/pages_publiques.py::
afficher_intake), sans deuxième chance.

Toute interaction réseau (requests.get) est mockée : aucun appel réel.
time.sleep mocké pour ne jamais ralentir les tests.

Usage : python3 -m unittest tests.test_retry_verification_siret -v
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

import verification_pro as vp

SIRET_VALIDE = "12345678900012"


def _reponse(status_code, data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = data if data is not None else {"results": []}
    return r


class TestRetryVerifierSiretSirene(unittest.TestCase):
    def test_succes_du_premier_coup_aucun_retry(self):
        with patch.object(vp.requests, "get", return_value=_reponse(200)) as mock_get, \
             patch.object(vp.time, "sleep") as mock_sleep:
            resultat = vp.verifier_siret_sirene(SIRET_VALIDE)

        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIsNone(resultat["erreur"])

    def test_erreur_reseau_puis_succes(self):
        with patch.object(
            vp.requests, "get",
            side_effect=[requests.exceptions.RequestException("timeout"), _reponse(200)],
        ) as mock_get, patch.object(vp.time, "sleep") as mock_sleep:
            resultat = vp.verifier_siret_sirene(SIRET_VALIDE)

        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()
        self.assertIsNone(
            resultat["erreur"],
            "Un hoquet réseau ponctuel suivi d'un succès ne doit plus faire "
            "échouer la vérification SIRET (constat m11).",
        )

    def test_429_retente_puis_reussit(self):
        with patch.object(
            vp.requests, "get",
            side_effect=[_reponse(429), _reponse(200)],
        ) as mock_get, patch.object(vp.time, "sleep") as mock_sleep:
            resultat = vp.verifier_siret_sirene(SIRET_VALIDE)

        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()
        self.assertIsNone(resultat["erreur"])

    def test_echec_reseau_persistant_epuise_les_tentatives(self):
        with patch.object(
            vp.requests, "get",
            side_effect=requests.exceptions.RequestException("panne réseau"),
        ) as mock_get, patch.object(vp.time, "sleep"):
            resultat = vp.verifier_siret_sirene(SIRET_VALIDE)

        self.assertEqual(mock_get.call_count, vp.NB_TENTATIVES_MAX)
        self.assertIsNotNone(resultat["erreur"])
        self.assertFalse(resultat["trouve"])

    def test_erreur_500_non_retentee(self):
        """Seul 429 (rate-limit) déclenche une nouvelle tentative — une
        erreur serveur générique échoue directement, comme avant."""
        with patch.object(vp.requests, "get", return_value=_reponse(500)) as mock_get, \
             patch.object(vp.time, "sleep") as mock_sleep:
            resultat = vp.verifier_siret_sirene(SIRET_VALIDE)

        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertIn("500", resultat["erreur"])

    def test_format_invalide_aucun_appel_reseau(self):
        with patch.object(vp.requests, "get") as mock_get:
            resultat = vp.verifier_siret_sirene("123")

        mock_get.assert_not_called()
        self.assertIsNotNone(resultat["erreur"])


if __name__ == "__main__":
    unittest.main()
