"""
Test de régression pour le correctif m16 (voir audit/audit_verification_2026-09-08.md) :
inserer_lead() (lead_worker.py) ne dédupliquait que par nom d'entreprise
(on_conflict=company) — une variation de formulation du nom entre deux
scrapes de la MÊME entreprise réelle (SIREN identique) créait une seconde
fiche distincte, provoquant une double sollicitation commerciale.

Toute interaction réseau (requests) est mockée : aucun appel réel, aucun
e-mail envoyé.

Usage : python3 -m unittest tests.test_dedoublonnage_siren_lead_worker -v
"""
import unittest
from unittest.mock import MagicMock, patch

import lead_worker as lw


def _lead(company_name="Dupont SARL", siren="123456789", email=None):
    return {
        "company_name": company_name, "industry": "Bâtiment", "weakness": "test",
        "email": email, "siren": siren, "adresse": None, "telephone": None,
        "tranche_effectif_salarie": None, "score": 50,
    }


def _reponse(status_code, data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = data if data is not None else []
    r.text = ""
    return r


class TestLeadExistantParSiren(unittest.TestCase):
    def test_trouve_une_ligne_existante(self):
        with patch.object(lw.requests, "get", return_value=_reponse(200, [{"id": "l1", "company": "SARL Dupont"}])):
            resultat = lw._lead_existant_par_siren("123456789")

        self.assertEqual(resultat, {"id": "l1", "company": "SARL Dupont"})

    def test_aucun_siren_ne_fait_aucun_appel(self):
        with patch.object(lw.requests, "get") as mock_get:
            resultat = lw._lead_existant_par_siren(None)

        mock_get.assert_not_called()
        self.assertIsNone(resultat)

    def test_erreur_reseau_renvoie_none_sans_lever(self):
        with patch.object(lw.requests, "get", side_effect=lw.requests.exceptions.RequestException("panne")):
            resultat = lw._lead_existant_par_siren("123456789")

        self.assertIsNone(resultat)


class TestInsererLeadRedirigeVersLaFicheExistante(unittest.TestCase):
    def test_nom_different_meme_siren_redirige_upsert(self):
        with patch.object(lw, "_lead_existant_par_siren", return_value={"id": "l1", "company": "SARL Dupont"}), \
             patch.object(lw.requests, "post", return_value=_reponse(200, [{"id": "l1"}])) as mock_post:
            lw.inserer_lead(_lead(company_name="Dupont SARL"), pitch="x", envoi_reussi=False)

        payload_envoye = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            payload_envoye["company"], "SARL Dupont",
            "Quand un lead de même SIREN existe déjà sous un autre nom, "
            "l'upsert doit cibler la fiche EXISTANTE (constat m16) — sinon "
            "une variation de formulation crée une seconde fiche et double "
            "la sollicitation commerciale de la même entreprise réelle.",
        )

    def test_meme_nom_ne_declenche_aucune_redirection(self):
        with patch.object(lw, "_lead_existant_par_siren", return_value={"id": "l1", "company": "Dupont SARL"}), \
             patch.object(lw.requests, "post", return_value=_reponse(200, [{"id": "l1"}])) as mock_post:
            lw.inserer_lead(_lead(company_name="Dupont SARL"), pitch="x", envoi_reussi=False)

        payload_envoye = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload_envoye["company"], "Dupont SARL")

    def test_aucun_lead_existant_upsert_normal(self):
        with patch.object(lw, "_lead_existant_par_siren", return_value=None), \
             patch.object(lw.requests, "post", return_value=_reponse(200, [{"id": "l2"}])) as mock_post:
            lw.inserer_lead(_lead(company_name="Nouvelle Entreprise"), pitch="x", envoi_reussi=False)

        payload_envoye = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload_envoye["company"], "Nouvelle Entreprise")


if __name__ == "__main__":
    unittest.main()
