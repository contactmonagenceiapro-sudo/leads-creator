"""
Test de régression pour le correctif a4 (voir audit/audit_verification_2026-09-08.md) :
process_runner.py::lancer() n'avait aucune assertion défensive est_admin()
au niveau de la fonction elle-même — seule protection jusqu'ici : le
routage des pages (toutes admin-only), pas une défense en profondeur au
niveau de la fonction qui lance réellement le subprocess.

Toute interaction Streamlit/subprocess est mockée : aucun sous-processus
réellement lancé.

Usage : python3 -m unittest tests.test_process_runner_admin_defensif -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import process_runner  # noqa: E402


class TestLancerAssertionAdmin(unittest.TestCase):
    def test_refuse_si_non_admin(self):
        with patch.object(process_runner, "est_admin", return_value=False), \
             patch.object(process_runner.subprocess, "Popen") as mock_popen:
            with self.assertRaises(PermissionError):
                process_runner.lancer("scraping", None, ["echo", "test"])

        mock_popen.assert_not_called()

    def test_autorise_si_admin(self):
        processus_factice = MagicMock()
        with patch.object(process_runner, "est_admin", return_value=True), \
             patch.object(process_runner, "est_en_cours", return_value=False), \
             patch.object(process_runner.subprocess, "Popen", return_value=processus_factice) as mock_popen, \
             patch.object(process_runner, "st") as mock_st, \
             patch("builtins.open", MagicMock()):
            mock_st.session_state = {}
            process_runner.lancer("scraping", None, ["echo", "test"])

        mock_popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
