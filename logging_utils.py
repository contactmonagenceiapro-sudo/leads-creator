"""
Formatteur de log partagé — voir audit/audit_verification_2026-09-08.md,
constat m15 : FormatteurPrefixe était dupliquée à l'identique dans
scraper_batiment.py, lead_worker.py et pipeline.py — toute évolution du
format de log devait jusqu'ici être répliquée manuellement dans les 3.
"""

import logging


class FormatteurPrefixe(logging.Formatter):
    """Formate chaque ligne de log avec un préfixe visuel selon sa gravité."""

    PREFIXES = {
        logging.DEBUG: "[*]",
        logging.INFO: "[+]",
        logging.WARNING: "[!]",
        logging.ERROR: "[x]",
        logging.CRITICAL: "[x]",
    }

    def format(self, record):
        prefixe = self.PREFIXES.get(record.levelno, "[*]")
        return f"{prefixe} {record.getMessage()}"
