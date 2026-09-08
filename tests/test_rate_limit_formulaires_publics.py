"""
Test de régression pour le correctif M5 (voir audit/audit_verification_2026-09-08.md) :
les 3 formulaires publics (afficher_devis, afficher_demande_devis,
afficher_devenir_client) n'avaient aucune limitation de fréquence — chaque
soumission déclenche une écriture DB + une alerte Discord + (pour 2 des 3)
un envoi SMTP réel, sans aucun frein anti-abus.

Toute interaction Supabase et Streamlit (st.context) est mockée : aucun
appel réseau réel.

Usage : python3 -m unittest tests.test_rate_limit_formulaires_publics -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import data_access  # noqa: E402


class TestVerifierRateLimitPublic(unittest.TestCase):
    def _contexte_ip(self, ip):
        return SimpleNamespace(ip_address=ip)

    def test_autorise_sous_le_seuil(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"nb_appels": 2}])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.st, "context", self._contexte_ip("1.2.3.4")):
            autorise = data_access.verifier_rate_limit_public("devis", max_appels=5, fenetre_minutes=10)

        self.assertTrue(autorise)
        supabase_factice.table.return_value.upsert.assert_called_once()

    def test_bloque_au_dela_du_seuil(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"nb_appels": 5}])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.st, "context", self._contexte_ip("1.2.3.4")):
            autorise = data_access.verifier_rate_limit_public("devis", max_appels=5, fenetre_minutes=10)

        self.assertFalse(
            autorise,
            "verifier_rate_limit_public() doit bloquer au-delà du seuil "
            "(constat M5) — sinon les formulaires publics restent ouverts "
            "à un abus scripté en rafale.",
        )
        supabase_factice.table.return_value.upsert.assert_not_called()

    def test_premiere_soumission_de_la_fenetre_autorisee(self):
        """Aucune ligne existante pour (ip, formulaire, fenêtre) -> première
        soumission, toujours autorisée."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.st, "context", self._contexte_ip("1.2.3.4")):
            autorise = data_access.verifier_rate_limit_public("devenir_client", max_appels=5, fenetre_minutes=10)

        self.assertTrue(autorise)

    def test_ip_absente_toujours_autorise(self):
        """Repli permissif si st.context.ip_address est indisponible —
        mieux vaut ne pas limiter que bloquer tous les visiteurs derrière
        une IP non résolue."""
        supabase_factice = MagicMock()
        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.st, "context", self._contexte_ip(None)):
            autorise = data_access.verifier_rate_limit_public("devis")

        self.assertTrue(autorise)
        supabase_factice.table.assert_not_called()

    def test_table_indisponible_toujours_autorise(self):
        """Repli permissif si la table rate_limit_formulaires_publics n'est
        pas encore migrée / Supabase temporairement indisponible — ne doit
        jamais bloquer un vrai visiteur pour une raison d'infrastructure."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.side_effect = \
            Exception("relation \"rate_limit_formulaires_publics\" does not exist")

        with patch.object(data_access, "supabase", supabase_factice), \
             patch.object(data_access.st, "context", self._contexte_ip("1.2.3.4")):
            autorise = data_access.verifier_rate_limit_public("devis")

        self.assertTrue(autorise)


class TestFormulairesPublicsAppellentLeRateLimit(unittest.TestCase):
    """Vérifie que les 3 formulaires publics appellent bien le rate-limit
    partagé (_rate_limit_ou_avertir) — sans exécuter les formulaires
    Streamlit eux-mêmes (nécessiteraient un vrai runtime Streamlit),
    seulement les fonctions de garde qu'ils utilisent."""

    def test_pages_publiques_importe_verifier_rate_limit_public(self):
        import pages_publiques
        self.assertTrue(hasattr(pages_publiques, "verifier_rate_limit_public"))
        self.assertTrue(hasattr(pages_publiques, "_rate_limit_ou_avertir"))

    def test_rate_limit_ou_avertir_delegue_et_avertit(self):
        import pages_publiques

        with patch.object(pages_publiques, "verifier_rate_limit_public", return_value=False), \
             patch.object(pages_publiques.st, "warning") as mock_warning:
            resultat = pages_publiques._rate_limit_ou_avertir("devis")

        self.assertFalse(resultat)
        mock_warning.assert_called_once()
        # Le message ne doit jamais révéler le seuil exact (max_appels/fenêtre).
        message = mock_warning.call_args[0][0]
        self.assertNotRegex(message, r"\d")

    def test_rate_limit_ou_avertir_autorise_sans_avertissement(self):
        import pages_publiques

        with patch.object(pages_publiques, "verifier_rate_limit_public", return_value=True), \
             patch.object(pages_publiques.st, "warning") as mock_warning:
            resultat = pages_publiques._rate_limit_ou_avertir("devis")

        self.assertTrue(resultat)
        mock_warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
