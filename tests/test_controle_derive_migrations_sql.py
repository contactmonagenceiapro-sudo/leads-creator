"""
Test de régression pour le correctif m2 (voir audit/audit_verification_2026-09-08.md) :
rien ne vérifiait auparavant que les fichiers sql/*.sql du dépôt avaient
une ligne correspondante en base (migrations_appliquees) — dérive
silencieuse possible entre environnements, détectable seulement via un bug
fonctionnel révélant un écart de schéma. controle_sante_bdd.py::
controler_derive_migrations_sql() compare désormais les deux (best-effort,
comparaison par nom de fichier).

Toute interaction Supabase et filesystem est mockée : aucun appel réseau
réel, aucune lecture du vrai dossier sql/ du dépôt.

Usage : python3 -m unittest tests.test_controle_derive_migrations_sql -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controle_sante_bdd as csb  # noqa: E402


class TestControlerDeriveMigrationsSql(unittest.TestCase):
    def _patch_fichiers_repo(self, noms: list[str]):
        faux_fichiers = [SimpleNamespace(name=n) for n in noms]
        return patch.object(Path, "glob", return_value=faux_fichiers)

    def test_statut_ok_quand_tout_est_journalise(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.execute.return_value = \
            SimpleNamespace(data=[{"nom": "init_leads.sql"}, {"nom": "fix_x.sql"}])

        with self._patch_fichiers_repo(["init_leads.sql", "fix_x.sql"]), \
             patch.object(csb, "supabase", supabase_factice):
            resultat = csb.controler_derive_migrations_sql()

        self.assertEqual(resultat["statut"], "ok")
        self.assertEqual(resultat["detail"]["jamais_appliques_ou_non_journalises"], [])

    def test_statut_attention_si_fichier_jamais_journalise(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.execute.return_value = \
            SimpleNamespace(data=[{"nom": "init_leads.sql"}])

        with self._patch_fichiers_repo(["init_leads.sql", "fix_nouveau.sql"]), \
             patch.object(csb, "supabase", supabase_factice):
            resultat = csb.controler_derive_migrations_sql()

        self.assertEqual(resultat["statut"], "attention")
        self.assertEqual(resultat["detail"]["jamais_appliques_ou_non_journalises"], ["fix_nouveau.sql"])

    def test_lignes_orphelines_signalees_mais_jamais_bloquantes(self):
        """Une ligne migrations_appliquees sans fichier correspondant
        (renommé/supprimé) reste informationnelle, jamais 'attention'."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.execute.return_value = \
            SimpleNamespace(data=[{"nom": "init_leads.sql"}, {"nom": "ancien_fichier_supprime.sql"}])

        with self._patch_fichiers_repo(["init_leads.sql"]), \
             patch.object(csb, "supabase", supabase_factice):
            resultat = csb.controler_derive_migrations_sql()

        self.assertEqual(resultat["statut"], "ok")
        self.assertEqual(
            resultat["detail"]["lignes_orphelines_migrations_appliquees"],
            ["ancien_fichier_supprime.sql"],
        )

    def test_enregistre_dans_actions_concretes(self):
        self.assertIn("derive_migrations_sql", csb.ACTIONS_CONCRETES)

    def test_cable_dans_main(self):
        """La logique métier vit dans _executer_controles() depuis le
        filet englobant ajouté dans main() (même correctif que le commit
        d231638 sur scripts/traiter_paiements_stripe.py) — le câblage réel
        se vérifie donc là, main() ne faisant plus qu'appeler cette
        fonction dans un try/except."""
        import inspect
        source = inspect.getsource(csb._executer_controles)
        self.assertIn("controler_derive_migrations_sql", source)


if __name__ == "__main__":
    unittest.main()
