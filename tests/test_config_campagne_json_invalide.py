"""
Test de régression pour le correctif m13 (voir audit/audit_verification_2026-09-08.md) :
outbound_chantiers/config.py::_charger_campagne() retombait silencieusement
sur la configuration par défaut (identité S.B.G Travaux) si
OUTBOUND_CAMPAGNE_JSON était explicitement défini mais malformé — un run
destiné à un AUTRE client aurait alors tourné sous la mauvaise identité
(mauvaises communes, mauvais pitch), un "faux succès silencieux".

Le cas OUTBOUND_CAMPAGNE_JSON ABSENT (CLI autonome, légitime) n'est pas
concerné et continue de replier normalement sur la config par défaut.

Usage : python3 -m unittest tests.test_config_campagne_json_invalide -v
"""
import importlib
import json
import unittest
from unittest.mock import patch

import outbound_chantiers.config as cfg


class TestChargerCampagne(unittest.TestCase):
    def test_json_malformé_leve_explicitement(self):
        with patch.dict(cfg.os.environ, {"OUTBOUND_CAMPAGNE_JSON": "{ceci n'est pas du JSON"}):
            with self.assertRaises(ValueError) as ctx:
                cfg._charger_campagne()

        self.assertIn("OUTBOUND_CAMPAGNE_JSON", str(ctx.exception))

    def test_json_absent_replie_sur_la_config_par_defaut(self):
        """Cas légitime (CLI autonome) — comportement INCHANGÉ par ce
        correctif : seul un JSON explicitement présent mais invalide doit
        échouer, jamais son absence pure et simple."""
        with patch.dict(cfg.os.environ, {}, clear=False):
            cfg.os.environ.pop("OUTBOUND_CAMPAGNE_JSON", None)
            resultat = cfg._charger_campagne()

        self.assertEqual(resultat, cfg._CAMPAGNE_PAR_DEFAUT)

    def test_json_valide_est_utilise_normalement(self):
        campagne = {"nom_client": "Autre Client", "communes_cibles": ["Reims"]}
        with patch.dict(cfg.os.environ, {"OUTBOUND_CAMPAGNE_JSON": json.dumps(campagne)}):
            resultat = cfg._charger_campagne()

        self.assertEqual(resultat, campagne)

    def test_import_du_module_echoue_si_json_malforme_au_chargement(self):
        """_CAMPAGNE = _charger_campagne() s'exécute à l'IMPORT du module —
        un cron mal configuré doit planter tout de suite (log clair dans
        GitHub Actions), pas tourner silencieusement sous la mauvaise
        identité client."""
        try:
            with patch.dict(cfg.os.environ, {"OUTBOUND_CAMPAGNE_JSON": "{invalide"}):
                with self.assertRaises(ValueError):
                    importlib.reload(cfg)
        finally:
            # Remet le module dans un état sain quoi qu'il arrive (y compris
            # si l'assertion ci-dessus échoue) — outbound_chantiers.config
            # est partagé par plusieurs modules du reste de la suite.
            importlib.reload(cfg)


if __name__ == "__main__":
    unittest.main()
