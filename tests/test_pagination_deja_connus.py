"""
Test de régression pour le correctif m8 (voir audit/audit_verification_2026-09-08.md) :
recuperer_sirens_deja_connus()/_noms_deja_en_base() n'avaient aucune
pagination explicite — au-delà de la limite par défaut de PostgREST/
Supabase (souvent 1000 lignes), la réponse était silencieusement tronquée,
provoquant un re-sourcing/republication de doublons sans erreur visible.

Toute interaction réseau (requests.get) est mockée : aucun appel réel.

Usage : python3 -m unittest tests.test_pagination_deja_connus -v
"""
import unittest
from unittest.mock import MagicMock, patch

import outbound_chantiers.scorer_et_publier as sep
import outbound_chantiers.sourcing_acteurs_pro as sap


def _reponse(status_code, data):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = data
    return r


class TestPaginationSirensDejaConnus(unittest.TestCase):
    def test_une_seule_page_sous_le_seuil(self):
        page = [{"siren": str(i)} for i in range(5)]
        with patch.object(sap.requests, "get", return_value=_reponse(200, page)) as mock_get:
            resultat = sap.recuperer_sirens_deja_connus("Client A")

        self.assertEqual(resultat, {str(i) for i in range(5)})
        self.assertEqual(mock_get.call_count, 1)

    def test_plusieurs_pages_accumulees(self):
        """Simule > TAILLE_PAGE résultats : deux pages pleines suivies d'une
        page partielle — toutes les données doivent être accumulées, pas
        seulement la première page (constat m8)."""
        taille_page = 1000
        page_pleine_1 = [{"siren": f"a{i}"} for i in range(taille_page)]
        page_pleine_2 = [{"siren": f"b{i}"} for i in range(taille_page)]
        page_partielle = [{"siren": "dernier"}]

        with patch.object(
            sap.requests, "get",
            side_effect=[_reponse(206, page_pleine_1), _reponse(206, page_pleine_2), _reponse(200, page_partielle)],
        ) as mock_get:
            resultat = sap.recuperer_sirens_deja_connus("Client A")

        self.assertEqual(mock_get.call_count, 3)
        self.assertIn("dernier", resultat)
        self.assertEqual(len(resultat), 2 * taille_page + 1)

    def test_pagine_avec_en_tete_range(self):
        # N'extrait/n'affiche JAMAIS le dict headers complet (il contient
        # apikey/Authorization construits depuis le vrai SUPABASE_KEY de
        # l'environnement local) — seule la valeur de "Range" est lue, pour
        # qu'un message d'échec d'assertion ne puisse jamais reproduire le
        # secret en clair dans les logs/la sortie du test.
        with patch.object(sap.requests, "get", return_value=_reponse(200, [])) as mock_get:
            sap.recuperer_sirens_deja_connus("Client A")

        valeur_range = mock_get.call_args.kwargs["headers"].get("Range")
        self.assertEqual(valeur_range, "0-999")

    def test_erreur_http_ne_leve_jamais(self):
        with patch.object(sap.requests, "get", return_value=_reponse(500, [])):
            resultat = sap.recuperer_sirens_deja_connus("Client A")
        self.assertEqual(resultat, set())


class TestPaginationNomsDejaEnBase(unittest.TestCase):
    def test_plusieurs_pages_accumulees(self):
        taille_page = 1000
        page_pleine = [{"nom_entreprise": f"Acteur {i}"} for i in range(taille_page)]
        page_partielle = [{"nom_entreprise": "Dernier Acteur"}]

        with patch.object(
            sep.requests, "get",
            side_effect=[_reponse(206, page_pleine), _reponse(200, page_partielle)],
        ) as mock_get:
            resultat = sep._noms_deja_en_base("Client A")

        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("Dernier Acteur", resultat)
        self.assertEqual(len(resultat), taille_page + 1)

    def test_une_seule_page_sous_le_seuil(self):
        page = [{"nom_entreprise": "Acteur 1"}]
        with patch.object(sep.requests, "get", return_value=_reponse(200, page)) as mock_get:
            resultat = sep._noms_deja_en_base("Client A")

        self.assertEqual(resultat, {"Acteur 1"})
        self.assertEqual(mock_get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
