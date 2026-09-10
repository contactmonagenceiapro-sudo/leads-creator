"""
Test de régression pour le même angle mort que le commit d231638
(scripts/traiter_paiements_stripe.py) : dans scripts/controle_echeances.py,
l'alerte Discord/e-mail n'était déclenchée qu'APRÈS le calcul des échéances
(get_echeances_a_relancer()) — un crash pendant ce calcul (ex. la requête
Supabase sous-jacente qui échoue) sortait en erreur Python bruyante côté CI
sans jamais déclencher d'alerte.

main() catche désormais toute exception non gérée survenant avant ou en
dehors de _controler_echeances(), alerte Discord avec un message distinct,
puis relaisse l'exception remonter.

Toute interaction Supabase est mockée : aucun appel réseau réel, aucun appel
Discord réel.

Usage : python3 -m unittest tests.test_controle_echeances_crash_avant_controle -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controle_echeances as ce  # noqa: E402


class TestFiletCrashAvantControle(unittest.TestCase):
    def test_crash_sur_get_echeances_a_relancer_declenche_alerter_discord(self):
        """_controler_echeances() plante dès get_echeances_a_relancer()
        (avant toute logique d'alerte) -> main() doit catcher, alerter
        Discord avec un message distinct, puis relaisser l'exception
        remonter (le script doit rester en échec visible côté CI)."""
        with patch.object(ce, "get_echeances_a_relancer", side_effect=RuntimeError("connexion Supabase refusée")), \
             patch.object(ce.alertes, "alerter_discord") as mock_alerte:
            with self.assertRaises(RuntimeError):
                ce.main()

        mock_alerte.assert_called_once()
        message = mock_alerte.call_args[0][0]
        self.assertIn("avant le contrôle des échéances", message.lower())

    def test_traitement_normal_n_appelle_pas_le_filet(self):
        """Cas nominal (aucune échéance à relancer) : le filet englobant ne
        doit rien déclencher — non-régression sur le chemin heureux."""
        resultat = {"echeances": [], "seuil_jours": 30}
        with patch.object(ce, "get_echeances_a_relancer", return_value=resultat), \
             patch.object(ce.alertes, "alerter_discord") as mock_alerte:
            ce.main()

        mock_alerte.assert_not_called()


if __name__ == "__main__":
    unittest.main()
