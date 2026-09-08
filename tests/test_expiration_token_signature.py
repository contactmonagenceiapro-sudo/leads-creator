"""
Test de régression pour le correctif m7 (voir audit/audit_verification_2026-09-08.md) :
le token de signature interne (secrets.token_urlsafe(32), robuste) restait
valide indéfiniment, contrairement à token_confirmation (24h) — un lien
intercepté (boîte mail compromise des mois après l'envoi) restait
exploitable sans limite de temps pour SIGNER un contrat.

Toute interaction Supabase/Streamlit est mockée : aucun appel réseau réel.

Usage : python3 -m unittest tests.test_expiration_token_signature -v
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import signature_interne  # noqa: E402


def _iso(delta_jours):
    return (datetime.now(timezone.utc) - timedelta(days=delta_jours)).isoformat()


class TestTokenExpire(unittest.TestCase):
    def test_contrat_recent_non_expire(self):
        contrat = {"yousign_status": "a_envoyer", "created_at": _iso(1)}
        self.assertFalse(signature_interne.token_expire(contrat))

    def test_contrat_de_31_jours_expire(self):
        contrat = {"yousign_status": "a_envoyer", "created_at": _iso(31)}
        self.assertTrue(
            signature_interne.token_expire(contrat),
            "Un contrat non signé de plus de 30 jours doit être considéré "
            "expiré (constat m7) — sinon un lien intercepté reste exploitable "
            "indéfiniment.",
        )

    def test_contrat_de_29_jours_pas_encore_expire(self):
        contrat = {"yousign_status": "a_envoyer", "created_at": _iso(29)}
        self.assertFalse(signature_interne.token_expire(contrat))

    def test_contrat_deja_signe_jamais_expire(self):
        """Consulter le récapitulatif d'un contrat déjà signé, même ancien,
        reste légitime — seule la possibilité de SIGNER doit être limitée."""
        contrat = {"yousign_status": "signe", "created_at": _iso(365)}
        self.assertFalse(signature_interne.token_expire(contrat))

    def test_created_at_absent_jamais_expire(self):
        """Repli permissif : ne doit jamais bloquer un vrai client sur une
        donnée manquante."""
        contrat = {"yousign_status": "a_envoyer"}
        self.assertFalse(signature_interne.token_expire(contrat))


class TestEnregistrerSignatureRejetteSiExpire(unittest.TestCase):
    def test_refuse_de_signer_un_contrat_expire(self):
        contrat = {"id": "c1", "lead_id": "l1", "yousign_status": "a_envoyer", "created_at": _iso(31)}
        with patch.object(signature_interne, "_get_lead") as mock_get_lead:
            ok, erreur = signature_interne.enregistrer_signature(contrat, "Jean Dupont")

        self.assertFalse(ok)
        self.assertIn("expiré", erreur)
        mock_get_lead.assert_not_called()  # ne va pas plus loin dans le traitement


class TestAfficherSignatureBloqueSiExpire(unittest.TestCase):
    def test_page_publique_bloque_un_token_expire(self):
        import pages_publiques

        contrat = {"id": "c1", "lead_id": "l1", "yousign_status": "a_envoyer", "created_at": _iso(31)}
        with patch.object(pages_publiques, "get_contrat_par_token", return_value=contrat), \
             patch.object(pages_publiques.st, "error") as mock_error, \
             patch.object(pages_publiques, "_get_lead") as mock_get_lead:
            pages_publiques.afficher_signature("tok-expire")

        mock_error.assert_called_once()
        self.assertIn("expiré", mock_error.call_args[0][0])
        mock_get_lead.assert_not_called()  # la page s'arrête avant d'aller plus loin


if __name__ == "__main__":
    unittest.main()
