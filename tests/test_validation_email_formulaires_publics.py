"""
Test de régression pour le correctif m6 (voir audit/audit_verification_2026-09-08.md) :
aucune validation de format d'email sur afficher_devis/afficher_demande_devis
(dashboard/pages_publiques.py), contrairement à afficher_devenir_client
(email_blackliste_ou_a_risque) — données de mauvaise qualité en base,
tentatives d'envoi SMTP vers adresses malformées.

Toute interaction Streamlit (st.warning) est mockée.

Usage : python3 -m unittest tests.test_validation_email_formulaires_publics -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import pages_publiques  # noqa: E402


class TestEmailOkOuAvertir(unittest.TestCase):
    def test_email_vide_toujours_ok(self):
        with patch.object(pages_publiques, "email_blackliste_ou_a_risque") as mock_check, \
             patch.object(pages_publiques.st, "warning") as mock_warning:
            resultat = pages_publiques._email_ok_ou_avertir("")

        self.assertTrue(resultat)
        mock_check.assert_not_called()
        mock_warning.assert_not_called()

    def test_email_valide_ok(self):
        with patch.object(pages_publiques, "email_blackliste_ou_a_risque", return_value=(False, "")), \
             patch.object(pages_publiques.st, "warning") as mock_warning:
            resultat = pages_publiques._email_ok_ou_avertir("contact@exemple.fr")

        self.assertTrue(resultat)
        mock_warning.assert_not_called()

    def test_email_blackliste_rejete_avec_avertissement(self):
        with patch.object(
            pages_publiques, "email_blackliste_ou_a_risque",
            return_value=(True, "domaine jetable"),
        ), patch.object(pages_publiques.st, "warning") as mock_warning:
            resultat = pages_publiques._email_ok_ou_avertir("test@mailinator.com")

        self.assertFalse(
            resultat,
            "_email_ok_ou_avertir() doit rejeter un email blacklisté/à risque "
            "(constat m6) — sinon des adresses malformées/jetables continuent "
            "à générer des tentatives d'envoi SMTP et des données de mauvaise "
            "qualité en base.",
        )
        mock_warning.assert_called_once()
        self.assertIn("domaine jetable", mock_warning.call_args[0][0])


class TestFormulairesUtilisentLaValidation(unittest.TestCase):
    def test_afficher_devis_utilise_email_ok_ou_avertir(self):
        import inspect
        source = inspect.getsource(pages_publiques.afficher_devis)
        self.assertIn("_email_ok_ou_avertir", source)

    def test_afficher_demande_devis_utilise_email_ok_ou_avertir(self):
        import inspect
        source = inspect.getsource(pages_publiques.afficher_demande_devis)
        self.assertIn("_email_ok_ou_avertir", source)


if __name__ == "__main__":
    unittest.main()
