"""
Test de régression pour le correctif M4 (voir audit/audit_verification_2026-09-08.md) :
afficher_presentation/afficher_intake (dashboard/pages_publiques.py)
résolvaient le lead directement par lead_id (clé primaire UUID, en clair
dans le lien envoyé par email) — désormais STRICTEMENT par
token_acces_public, un token dédié imprévisible (secrets.token_urlsafe),
même doctrine que signature_token/token_confirmation.

Décision produit confirmée le 08/09/2026 : PAS de repli sur lead_id — un
ancien lien envoyé avant ce correctif ne fonctionne plus.

Toute interaction Supabase est mockée : aucun appel réseau réel, aucun
e-mail envoyé.

Usage : python3 -m unittest tests.test_token_acces_public_lead -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import pages_publiques  # noqa: E402


class TestGetLeadParToken(unittest.TestCase):
    def test_resout_par_token(self):
        lead = {"id": "l1", "token_acces_public": "tok-abc", "company": "Acme"}
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[lead])

        with patch.object(pages_publiques, "supabase", supabase_factice):
            resultat = pages_publiques._get_lead_par_token("tok-abc")

        self.assertEqual(resultat, lead)
        supabase_factice.table.return_value.select.return_value.eq.assert_called_with("token_acces_public", "tok-abc")

    def test_lead_id_ne_resout_plus_rien(self):
        """Un ancien lien (?lead_id=...) ne doit plus jamais résoudre un
        lead : seul token_acces_public est un contrôle d'accès valide."""
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[])  # aucune ligne ne matche lead_id comme token

        with patch.object(pages_publiques, "supabase", supabase_factice):
            resultat = pages_publiques._get_lead_par_token("l1")  # un ancien lead_id, pas un token

        self.assertIsNone(resultat)

    def test_token_absent_ne_resout_rien(self):
        supabase_factice = MagicMock()
        with patch.object(pages_publiques, "supabase", supabase_factice):
            resultat = pages_publiques._get_lead_par_token(None)

        self.assertIsNone(resultat)
        supabase_factice.table.assert_not_called()

    def test_erreur_supabase_ne_leve_jamais(self):
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.select.return_value.eq.return_value.execute.side_effect = \
            Exception("panne réseau")
        with patch.object(pages_publiques, "supabase", supabase_factice):
            resultat = pages_publiques._get_lead_par_token("tok-abc")

        self.assertIsNone(resultat)


class TestAfficherPresentationEtIntakeUtilisentLeToken(unittest.TestCase):
    def test_afficher_presentation_appelle_get_lead_par_token(self):
        with patch.object(pages_publiques, "_get_lead_par_token", return_value=None) as mock_resolveur, \
             patch.object(pages_publiques.st, "error"):
            pages_publiques.afficher_presentation("tok-abc")

        mock_resolveur.assert_called_once_with("tok-abc")

    def test_afficher_intake_appelle_get_lead_par_token(self):
        with patch.object(pages_publiques, "_get_lead_par_token", return_value=None) as mock_resolveur, \
             patch.object(pages_publiques.st, "error"):
            pages_publiques.afficher_intake("tok-abc")

        mock_resolveur.assert_called_once_with("tok-abc")


class TestEnvoyerSuiviPositifGenereUnToken(unittest.TestCase):
    def test_genere_et_persiste_un_token_puis_l_utilise_dans_les_liens(self):
        import mail_processor

        lead = {"id": "l1", "company": "Acme", "email": "acme@example.com"}
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "l1"}])

        corps_envoye = {}

        def _capturer_envoi(to_email, sujet, corps, lead_id=None):
            corps_envoye["texte"] = corps
            return True

        with patch.object(mail_processor, "supabase", supabase_factice), \
             patch("ceo_agent.send_email_prospect", side_effect=_capturer_envoi), \
             patch.object(mail_processor, "update_lead_status"):
            mail_processor.envoyer_suivi_positif(lead)

        appel_update = supabase_factice.table.return_value.update.call_args[0][0]
        self.assertIn("token_acces_public", appel_update)
        token_genere = appel_update["token_acces_public"]
        self.assertNotEqual(token_genere, "l1")  # jamais le lead_id lui-même
        self.assertIn(f"vue=presentation&token={token_genere}", corps_envoye["texte"])
        self.assertIn(f"vue=intake&token={token_genere}", corps_envoye["texte"])
        self.assertNotIn("lead_id=", corps_envoye["texte"])

    def test_reutilise_un_token_deja_existant(self):
        import mail_processor

        lead = {"id": "l1", "company": "Acme", "email": "acme@example.com", "token_acces_public": "tok-existant"}
        supabase_factice = MagicMock()
        supabase_factice.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            SimpleNamespace(data=[{"id": "l1"}])

        with patch.object(mail_processor, "supabase", supabase_factice), \
             patch("ceo_agent.send_email_prospect", return_value=True), \
             patch.object(mail_processor, "update_lead_status"):
            mail_processor.envoyer_suivi_positif(lead)

        appel_update = supabase_factice.table.return_value.update.call_args[0][0]
        self.assertEqual(appel_update["token_acces_public"], "tok-existant")


if __name__ == "__main__":
    unittest.main()
