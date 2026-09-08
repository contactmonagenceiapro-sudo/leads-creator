"""
Test de régression pour le correctif M6 (voir audit/audit_verification_2026-09-08.md) :
enregistrer_signature() écrivait INCONDITIONNELLEMENT la preuve de
signature (nom saisi, IP, user-agent, horodatage, empreinte SHA-256) —
aucune garde côté serveur n'empêchait un second appel (double-clic, deux
onglets ouverts sur le même lien, ou tout appel hors du parcours normal
afficher_signature()) d'écraser silencieusement une preuve déjà
enregistrée, affaiblissant sa valeur probante (Article 1367 du Code civil)
en cas de contestation.

Toute interaction Supabase/Streamlit est mockée : aucun appel réseau réel,
aucun e-mail envoyé.

Usage : python3 -m unittest tests.test_signature_interne_scellement -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import signature_interne  # noqa: E402


class TestScellementSignature(unittest.TestCase):
    def _contrat(self, id_="c1", lead_id="l1", yousign_status="a_envoyer"):
        return {"id": id_, "lead_id": lead_id, "yousign_status": yousign_status}

    def _config_supabase_factice(self, donnees_update_reussi):
        """donnees_update_reussi : la valeur de .data renvoyée par le premier
        UPDATE conditionnel sur `contracts` — [] simule une course perdue
        (0 ligne affectée), une liste non vide simule un succès. Mémorise un
        seul mock par nom de table (même objet renvoyé à chaque appel
        .table(nom)) pour pouvoir inspecter les appels après coup."""
        mocks_par_table: dict[str, MagicMock] = {}

        def table(nom):
            if nom not in mocks_par_table:
                m = MagicMock()
                if nom == "contracts":
                    m.update.return_value.eq.return_value.neq.return_value.execute.return_value = \
                        SimpleNamespace(data=donnees_update_reussi)
                elif nom == "leads":
                    m.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
                mocks_par_table[nom] = m
            return mocks_par_table[nom]

        supabase_factice = MagicMock()
        supabase_factice.table.side_effect = table
        return supabase_factice

    def _patches_communs(self, supabase_factice):
        return [
            patch.object(signature_interne, "supabase", supabase_factice),
            patch.object(signature_interne, "_get_lead", return_value={"id": "l1", "email": None, "company": "Acme"}),
            patch.object(signature_interne, "_get_intake_pour_lead", return_value={}),
            patch.object(signature_interne, "generer_pdf_devis", return_value=b"pdf-bytes"),
            patch.object(signature_interne.st, "context", SimpleNamespace(
                ip_address="1.2.3.4", headers={"User-Agent": "TestAgent"},
            )),
        ]

    def test_premiere_signature_reussit_et_ecrit_la_preuve(self):
        supabase_factice = self._config_supabase_factice([{"id": "c1"}])
        patches = self._patches_communs(supabase_factice)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ok, erreur = signature_interne.enregistrer_signature(self._contrat(), "Jean Dupont")

        self.assertTrue(ok)
        self.assertEqual(erreur, "")
        # Vérifie que l'UPDATE conditionnel a bien été appelé avec la garde
        # anti-écrasement (.neq("yousign_status", "signe")).
        appel_update = supabase_factice.table("contracts").update.call_args
        self.assertEqual(appel_update[0][0]["signature_nom_saisi"], "Jean Dupont")
        appel_neq = supabase_factice.table("contracts").update.return_value.eq.return_value.neq.call_args
        self.assertEqual(appel_neq[0], ("yousign_status", "signe"))

    def test_second_appel_est_rejete_sans_ecraser_la_preuve(self):
        """Simule la course perdue : le premier UPDATE conditionnel n'a
        affecté aucune ligne (déjà signé entre-temps par un autre appel)."""
        supabase_factice = self._config_supabase_factice([])  # 0 ligne affectée
        patches = self._patches_communs(supabase_factice)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ok, erreur = signature_interne.enregistrer_signature(self._contrat(), "Un Autre Nom")

        self.assertFalse(
            ok,
            "enregistrer_signature() doit échouer si le contrat est déjà "
            "signé (constat M6) — sinon la preuve d'origine peut être "
            "écrasée silencieusement par un second appel.",
        )
        self.assertIn("déjà été signé", erreur)

    def test_echec_maj_statut_lead_ne_defait_pas_la_signature_deja_scellee(self):
        """La preuve de signature (contracts) est l'autorité — un échec
        best-effort sur la mise à jour du statut leads ne doit jamais faire
        échouer une signature déjà scellée avec succès."""
        supabase_factice = self._config_supabase_factice([{"id": "c1"}])
        supabase_factice.table("leads").update.return_value.eq.return_value.execute.side_effect = \
            Exception("panne réseau")
        patches = self._patches_communs(supabase_factice)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ok, erreur = signature_interne.enregistrer_signature(self._contrat(), "Jean Dupont")

        self.assertTrue(ok)
        self.assertEqual(erreur, "")


if __name__ == "__main__":
    unittest.main()
