"""
Test de régression pour le correctif M2 (voir audit/audit_verification_2026-09-08.md) :
une erreur systémique du scan IMAP (identifiants Zoho manquants, échec de
connexion/login, etc.) devait, avant ce correctif, s'afficher "succeeded"
côté cron GitHub Actions mail_check.yml — check_for_replies() ne renvoyait
jamais rien et son bloc __main__ n'appelait jamais sys.exit(). Même défaut
que celui déjà corrigé sur ceo_agent.py/relance_prospects.py/livraison_devis.py.

Ne confond pas ce cas avec une erreur isolée sur UN SEUL message (déjà
absorbée message par message dans _scanner_boite — ce comportement-là est
correct et n'est volontairement PAS changé par ce correctif, voir
test_erreur_isolee_sur_un_message_ne_fait_pas_echouer_le_scan ci-dessous).

Toute interaction Supabase/IMAP est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_mail_processor_echec_scan -v
"""
import unittest
from unittest.mock import patch

import mail_processor


class TestCheckForRepliesRemonteUnEchecSystemique(unittest.TestCase):
    def test_renvoie_false_si_scan_en_erreur(self):
        with patch.object(mail_processor, "_acquerir_verrou", return_value=True), \
             patch.object(mail_processor, "_liberer_verrou"), \
             patch.object(mail_processor, "_enregistrer_run"), \
             patch.object(mail_processor, "_scanner_boite",
                           return_value=({}, 0, "Identifiants Zoho manquants")):
            resultat = mail_processor.check_for_replies()

        self.assertFalse(
            resultat,
            "check_for_replies() doit renvoyer False quand _scanner_boite() "
            "remonte une erreur systémique (constat M2) — sinon le cron "
            "horaire mail_check.yml s'affiche 'succeeded' malgré un scan qui "
            "n'a jamais eu lieu (bounces/STOP RGPD/réponses non traités).",
        )

    def test_renvoie_true_en_fonctionnement_normal(self):
        with patch.object(mail_processor, "_acquerir_verrou", return_value=True), \
             patch.object(mail_processor, "_liberer_verrou"), \
             patch.object(mail_processor, "_enregistrer_run"), \
             patch.object(mail_processor, "_scanner_boite",
                           return_value=({"positifs": 1}, 3, None)):
            resultat = mail_processor.check_for_replies()

        self.assertTrue(resultat)

    def test_renvoie_true_si_scan_deja_en_cours(self):
        """Un chevauchement avec un scan déjà en cours (verrou) reste un
        arrêt normal, pas un échec — ne doit jamais faire échouer le cron."""
        with patch.object(mail_processor, "_acquerir_verrou", return_value=False), \
             patch.object(mail_processor, "_enregistrer_run") as mock_run, \
             patch.object(mail_processor, "_scanner_boite") as mock_scan:
            resultat = mail_processor.check_for_replies()

        mock_scan.assert_not_called()
        mock_run.assert_called_once_with("ignore_chevauchement", {}, 0)
        self.assertTrue(resultat)

    def test_erreur_isolee_sur_un_message_ne_fait_pas_echouer_le_scan(self):
        """_scanner_boite() absorbe déjà les erreurs par message (compteurs
        ["erreurs"] incrémenté, scan poursuivi, erreur=None au final) — ce
        comportement existant n'est pas modifié par ce correctif."""
        with patch.object(mail_processor, "_acquerir_verrou", return_value=True), \
             patch.object(mail_processor, "_liberer_verrou"), \
             patch.object(mail_processor, "_enregistrer_run"), \
             patch.object(mail_processor, "_scanner_boite",
                           return_value=({"erreurs": 1, "positifs": 0}, 5, None)):
            resultat = mail_processor.check_for_replies()

        self.assertTrue(resultat)


if __name__ == "__main__":
    unittest.main()
