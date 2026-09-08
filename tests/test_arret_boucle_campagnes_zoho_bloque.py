"""
Test de régression pour le correctif m10 (voir audit/audit_verification_2026-09-08.md) :
scripts/lancer_pipeline_b2b.py continuait sa boucle sur les campagnes
actives après un blocage du compte Zoho détecté sur la première — un
gaspillage de temps CI (compte Zoho partagé, échec certain sur les
suivantes), sans impact sur la fiabilité du signal d'échec final mais
qui allongeait chaque run pour rien.

Toute interaction Supabase/subprocess est mockée : aucun appel réseau réel,
aucun sous-processus réellement lancé.

Usage : python3 -m unittest tests.test_arret_boucle_campagnes_zoho_bloque -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import lancer_pipeline_b2b as lpb  # noqa: E402
import outbound_chantiers.pipeline_outbound_chantiers as poc


class TestPipelineOutboundChantiersCodeSortieZoho(unittest.TestCase):
    def test_renvoie_code_distinct_si_zoho_bloque(self):
        from alertes import CompteZohoBloqueError

        with patch.object(poc, "executer_etape", side_effect=CompteZohoBloqueError("bloqué")), \
             patch("sys.argv", ["pipeline_outbound_chantiers.py", "--envoi-seul"]):
            code = poc.main()

        self.assertEqual(code, poc.CODE_SORTIE_ZOHO_BLOQUE)
        self.assertNotIn(poc.CODE_SORTIE_ZOHO_BLOQUE, (0, 1))

    def test_renvoie_1_pour_une_autre_erreur(self):
        with patch.object(poc, "executer_etape", return_value=False), \
             patch("sys.argv", ["pipeline_outbound_chantiers.py", "--envoi-seul"]):
            code = poc.main()

        self.assertEqual(code, 1)


class TestConstantesCoherentes(unittest.TestCase):
    def test_meme_code_de_sortie_des_deux_cotes(self):
        """lancer_pipeline_b2b.py duplique volontairement cette constante en
        littéral (voir son commentaire) plutôt que de l'importer — ce test
        garantit que les deux valeurs ne divergent jamais silencieusement."""
        self.assertEqual(lpb.CODE_SORTIE_ZOHO_BLOQUE, poc.CODE_SORTIE_ZOHO_BLOQUE)


class TestLancerPipelineB2bInterrompLaBoucle(unittest.TestCase):
    def _campagnes(self, n):
        return [{"nom_client": f"Client {i}"} for i in range(n)]

    def test_interrompt_apres_blocage_zoho_sur_la_premiere_campagne(self):
        resultats = [
            SimpleNamespace(returncode=lpb.CODE_SORTIE_ZOHO_BLOQUE),
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
        ]
        with patch.object(lpb, "campagnes_actives", return_value=self._campagnes(3)), \
             patch.object(lpb.subprocess, "run", side_effect=resultats) as mock_run, \
             patch("sys.argv", ["lancer_pipeline_b2b.py", "--envoi-seul"]):
            code = lpb.main()

        self.assertEqual(
            mock_run.call_count, 1,
            "La boucle doit s'arrêter dès le premier blocage Zoho détecté "
            "(constat m10) — pas de tentative sur les campagnes restantes.",
        )
        self.assertEqual(code, 1)

    def test_continue_normalement_sans_blocage_zoho(self):
        resultats = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)]
        with patch.object(lpb, "campagnes_actives", return_value=self._campagnes(2)), \
             patch.object(lpb.subprocess, "run", side_effect=resultats) as mock_run, \
             patch("sys.argv", ["lancer_pipeline_b2b.py"]):
            code = lpb.main()

        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(code, 0)

    def test_echec_non_zoho_continue_la_boucle(self):
        """Un échec ORDINAIRE (pas un blocage Zoho) ne doit pas interrompre
        les campagnes suivantes — seul le blocage Zoho justifie l'arrêt."""
        resultats = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
        with patch.object(lpb, "campagnes_actives", return_value=self._campagnes(2)), \
             patch.object(lpb.subprocess, "run", side_effect=resultats) as mock_run, \
             patch("sys.argv", ["lancer_pipeline_b2b.py"]):
            code = lpb.main()

        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
