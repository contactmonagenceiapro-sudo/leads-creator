"""
Test de régression pour le correctif m12 (voir audit/audit_verification_2026-09-08.md) :
dashboard/secrets_loader.py réaffichait SUPABASE_URL en entier dans un
message d'erreur si elle ne ressemblait pas à une URL Supabase valide — un
copier-coller malencontreux (ex. une clé collée au mauvais endroit)
aurait alors été réaffiché en clair dans l'UI.

Usage : python3 -m unittest tests.test_troncature_url_secrets_loader -v
"""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import secrets_loader  # noqa: E402


class TestTronquerPourAffichage(unittest.TestCase):
    def test_valeur_courte_inchangee(self):
        self.assertEqual(secrets_loader._tronquer_pour_affichage("https://x.co"), "https://x.co")

    def test_valeur_longue_tronquee(self):
        valeur = "https://iijvonnzanbvhwvexvzm.supabase.co"
        resultat = secrets_loader._tronquer_pour_affichage(valeur)

        self.assertLess(len(resultat), len(valeur))
        self.assertTrue(resultat.endswith("…"))
        self.assertTrue(valeur.startswith(resultat[:-1]))

    def test_secret_accidentellement_colle_nest_jamais_affiche_en_entier(self):
        """Simule le vrai scénario redouté : une clé JWT (longue) collée
        par erreur dans SUPABASE_URL — ne doit jamais apparaître en entier
        dans le message d'erreur produit par _diagnostiquer_cles_supabase()."""
        fausse_cle_collee_par_erreur = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "x" * 100

        with unittest.mock.patch.dict(
            secrets_loader.os.environ,
            {"SUPABASE_URL": fausse_cle_collee_par_erreur, "SUPABASE_KEY": ""},
        ):
            erreurs = secrets_loader._diagnostiquer_cles_supabase()

        self.assertTrue(erreurs)
        message = erreurs[0]
        self.assertNotIn(
            fausse_cle_collee_par_erreur, message,
            "La valeur complète ne doit jamais apparaître dans le message "
            "d'erreur (constat m12) — risque de réafficher un secret "
            "accidentellement collé dans le mauvais champ.",
        )


if __name__ == "__main__":
    unittest.main()
