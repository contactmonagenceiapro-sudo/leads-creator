"""
Test de régression pour le correctif m15 (voir audit/audit_verification_2026-09-08.md) :
FormatteurPrefixe (logging) était dupliquée à l'identique dans
scraper_batiment.py, lead_worker.py et pipeline.py — toute évolution du
format de log devait être répliquée manuellement dans les 3.

Usage : python3 -m unittest tests.test_logging_utils_mutualise -v
"""
import logging
import unittest

import lead_worker
import pipeline
import scraper_batiment
from logging_utils import FormatteurPrefixe


class TestFormatteurPrefixeMutualise(unittest.TestCase):
    def test_les_trois_modules_utilisent_la_meme_classe(self):
        self.assertIs(pipeline.FormatteurPrefixe, FormatteurPrefixe)
        self.assertIs(scraper_batiment.FormatteurPrefixe, FormatteurPrefixe)
        self.assertIs(lead_worker.FormatteurPrefixe, FormatteurPrefixe)

    def test_format_prefixe_correct_par_niveau(self):
        formatteur = FormatteurPrefixe()
        cas = [
            (logging.DEBUG, "[*]"),
            (logging.INFO, "[+]"),
            (logging.WARNING, "[!]"),
            (logging.ERROR, "[x]"),
            (logging.CRITICAL, "[x]"),
        ]
        for niveau, prefixe_attendu in cas:
            record = logging.LogRecord("test", niveau, __file__, 1, "message", None, None)
            self.assertEqual(formatteur.format(record), f"{prefixe_attendu} message")


if __name__ == "__main__":
    unittest.main()
