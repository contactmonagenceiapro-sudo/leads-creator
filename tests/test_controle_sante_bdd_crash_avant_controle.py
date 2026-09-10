"""
Test de régression pour le même angle mort que le commit d231638
(scripts/traiter_paiements_stripe.py) : dans scripts/controle_sante_bdd.py,
l'alerte Discord/e-mail n'était déclenchée qu'APRÈS l'exécution de tous les
contrôles (_schema_openapi() / _tables_et_fks(), puis chaque
enregistrer(controler_xxx())) — un crash pendant _schema_openapi() (ex. la
requête HTTP vers le schéma OpenAPI Supabase qui échoue) sortait en erreur
Python bruyante côté CI sans jamais déclencher d'alerte.

main() catche désormais toute exception non gérée survenant avant ou en
dehors de _executer_controles(), alerte Discord avec un message distinct,
puis relaisse l'exception remonter.

Toute interaction Supabase/HTTP est mockée : aucun appel réseau réel, aucun
appel Discord réel.

Usage : python3 -m unittest tests.test_controle_sante_bdd_crash_avant_controle -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controle_sante_bdd as csb  # noqa: E402


class TestFiletCrashAvantControle(unittest.TestCase):
    def test_crash_sur_schema_openapi_declenche_alerter_discord(self):
        """_executer_controles() plante dès _schema_openapi() (avant toute
        exécution de contrôle individuel) -> main() doit catcher, alerter
        Discord avec un message distinct, puis relaisser l'exception
        remonter (le script doit rester en échec visible côté CI)."""
        with patch.object(csb, "_schema_openapi", side_effect=RuntimeError("connexion Supabase refusée")), \
             patch.object(csb.alertes, "alerter_discord") as mock_alerte:
            with self.assertRaises(RuntimeError):
                csb.main()

        mock_alerte.assert_called_once()
        message = mock_alerte.call_args[0][0]
        self.assertIn("avant le contrôle de santé bdd", message.lower())

    def test_traitement_normal_n_appelle_pas_le_filet(self):
        """Cas nominal (aucun contrôle critique) : le filet englobant ne
        doit rien déclencher — non-régression sur le chemin heureux.
        _executer_controles() elle-même est mockée : ce test porte sur le
        comportement du filet de main(), pas sur la logique interne des
        contrôles (déjà hors périmètre de ce correctif)."""
        with patch.object(csb, "_executer_controles") as mock_executer, \
             patch.object(csb.alertes, "alerter_discord") as mock_alerte:
            csb.main()

        mock_executer.assert_called_once()
        mock_alerte.assert_not_called()


if __name__ == "__main__":
    unittest.main()
