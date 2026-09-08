"""
Test de caractérisation pour le constat m9 (voir audit/audit_verification_2026-09-08.md) :
la clé de dédoublonnage BDD de publier_en_base() est (client_final,
nom_entreprise), alors que le filtrage amont (sourcing_acteurs_pro.py::
recuperer_sirens_deja_connus) exclut par SIREN — une divergence documentée
(voir le commentaire juste avant l'appel requests.post dans
scorer_et_publier.py) plutôt que corrigée : migrer la contrainte UNIQUE
vers (client_final, siren) nécessiterait de vérifier au préalable l'état
réel des données en production, hors de portée d'une correction de code
seule.

Ce test ne prouve pas un bug qu'on corrige — il DOCUMENTE le comportement
actuel par un test qui échouerait si quelqu'un changeait silencieusement
la clé de dédoublonnage sans mettre à jour ce commentaire/cette limite
connue. Si ce test échoue un jour parce que la clé a changé, c'est le
signal qu'il faut aussi mettre à jour (ou retirer) la documentation de
cette limite dans scorer_et_publier.py.

Toute interaction réseau (requests.post) est mockée : aucun appel réel.

Usage : python3 -m unittest tests.test_dedoublonnage_siren_vs_nom -v
"""
import unittest
from unittest.mock import MagicMock, patch

import outbound_chantiers.scorer_et_publier as sep


def _acteur(nom_entreprise, siren, client_final="Client A"):
    return {
        "type_acteur": "architecte", "nom_entreprise": nom_entreprise, "siren": siren,
        "commune": "Lyon", "score_activite_chantiers": 0.5, "score_final": 0.6,
    }


class TestCleDeDedoublonnageIgnoreLeSiren(unittest.TestCase):
    def _reponse_ok(self):
        r = MagicMock()
        r.status_code = 200
        return r

    def test_on_conflict_ne_porte_pas_sur_siren(self):
        """Caractérise la limite connue : le paramètre on_conflict cible
        exactement (client_final, nom_entreprise), jamais siren."""
        with patch.object(sep, "CLIENT_FINAL", "Client A"), \
             patch.object(sep.requests, "post", return_value=self._reponse_ok()) as mock_post:
            sep.publier_en_base(_acteur("Cabinet Dupont", "111111111"))

        self.assertEqual(mock_post.call_args.kwargs["params"]["on_conflict"], "client_final,nom_entreprise")

    def test_deux_acteurs_homonymes_siren_differents_ciblent_la_meme_cle(self):
        """Deux entreprises RÉELLEMENT différentes (SIREN différents) mais
        partageant le même nom normalisé produisent le même on_conflict —
        preuve que rien côté requête sortante ne les distingue : côté
        Postgres, resolution=merge-duplicates les fusionnerait bel et bien
        (constat m9). C'est exactement la limite documentée, pas une
        assertion sur ce que fait Postgres lui-même (non testable ici sans
        base réelle)."""
        with patch.object(sep, "CLIENT_FINAL", "Client A"), \
             patch.object(sep.requests, "post", return_value=self._reponse_ok()) as mock_post:
            sep.publier_en_base(_acteur("Cabinet Dupont", "111111111"))
            appel_1 = dict(mock_post.call_args.kwargs)
            sep.publier_en_base(_acteur("Cabinet Dupont", "222222222"))
            appel_2 = dict(mock_post.call_args.kwargs)

        self.assertEqual(appel_1["params"]["on_conflict"], appel_2["params"]["on_conflict"])
        self.assertNotEqual(appel_1["json"]["siren"], appel_2["json"]["siren"])

    def test_resolution_merge_duplicates_toujours_active(self):
        with patch.object(sep, "CLIENT_FINAL", "Client A"), \
             patch.object(sep.requests, "post", return_value=self._reponse_ok()) as mock_post:
            sep.publier_en_base(_acteur("Cabinet Dupont", "111111111"))

        self.assertIn("merge-duplicates", mock_post.call_args.kwargs["headers"]["Prefer"])


if __name__ == "__main__":
    unittest.main()
