"""
Test de régression pour le bug C1 (voir audit/audit_verification_2026-09-08.md) :
sourcer_acteurs_pro()/filtrer_et_enrichir() n'écrivaient leur résultat sur
disque QUE dans leur bloc `if __name__ == "__main__":`, jamais quand elles
étaient appelées comme fonctions par pipeline_outbound_chantiers.py — le
seul chemin réellement emprunté en production (cron + bouton dashboard).
Résultat : le sourcing B2B hebdomadaire s'affichait "succeeded" sans plus
rien sourcer/enrichir/publier depuis le 04/09/2026, en silence.

Ce test appelle les fonctions exactement comme le fait
pipeline_outbound_chantiers.py (jamais via __main__) pour vérifier que ce
chemin de production écrit et relit bien les fichiers intermédiaires, et
qu'une étape sans fichier d'entrée échoue bruyamment plutôt que de renvoyer
silencieusement une liste vide.

Aucun appel réseau réel (API SIRENE, DuckDuckGo, Supabase) : tout est mocké.

Usage : python3 -m unittest tests.test_outbound_pipeline_io -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from outbound_chantiers import enrichir_acteurs_pro, scorer_et_publier, sourcing_acteurs_pro


class TestChainementFichiersIntermediaires(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.fichier_bruts = tmp / "acteurs_pro_bruts_test.json"
        self.fichier_enrichis = tmp / "acteurs_pro_enrichis_test.json"

        # Redirige les 3 modules vers des fichiers temporaires isolés —
        # jamais les vrais chemins de production (acteurs_pro_*_<CLIENT_FINAL>.json).
        for cible, module, attribut in (
            (self.fichier_bruts, sourcing_acteurs_pro, "FICHIER_SORTIE"),
            (self.fichier_bruts, enrichir_acteurs_pro, "FICHIER_ENTREE"),
            (self.fichier_enrichis, enrichir_acteurs_pro, "FICHIER_SORTIE"),
            (self.fichier_enrichis, scorer_et_publier, "FICHIER_ENTREE"),
        ):
            patcher = patch.object(module, attribut, cible)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_sourcer_acteurs_pro_ecrit_le_fichier_quand_appelee_comme_fonction(self):
        """Reproduit exactement le chemin d'appel de
        pipeline_outbound_chantiers.py:70 (appel direct de la fonction,
        jamais via __main__) — c'est ce chemin précis qui était cassé."""
        with patch.object(sourcing_acteurs_pro, "COMMUNES_CIBLES", ["Commune Test"]), \
             patch.object(sourcing_acteurs_pro, "NAF_CODES_CIBLES", {"architecte": ["71.11Z"]}), \
             patch.object(sourcing_acteurs_pro, "recuperer_sirens_deja_connus", return_value=set()), \
             patch.object(sourcing_acteurs_pro, "recuperer_activite_par_commune", return_value={}), \
             patch.object(sourcing_acteurs_pro, "score_pour_commune", return_value=0.5), \
             patch.object(sourcing_acteurs_pro, "interroger_sirene",
                          return_value={"results": [], "total_pages": 1}), \
             patch("time.sleep"):
            resultat = sourcing_acteurs_pro.sourcer_acteurs_pro()

        self.assertEqual(resultat, [])
        self.assertTrue(
            self.fichier_bruts.exists(),
            "sourcer_acteurs_pro() doit écrire FICHIER_SORTIE même appelée "
            "comme fonction (pas seulement depuis son bloc __main__) — "
            "régression du bug C1 sinon.",
        )
        self.assertEqual(json.loads(self.fichier_bruts.read_text(encoding="utf-8")), [])

    def test_filtrer_et_enrichir_leve_une_erreur_si_fichier_entree_absent(self):
        """Un cron cassé doit s'afficher rouge, pas vert : plus de []
        silencieux quand l'étape précédente n'a rien produit."""
        self.assertFalse(self.fichier_bruts.exists())
        with self.assertRaises(FileNotFoundError):
            enrichir_acteurs_pro.filtrer_et_enrichir()

    def test_scorer_et_publier_leve_une_erreur_si_fichier_entree_absent(self):
        self.assertFalse(self.fichier_enrichis.exists())
        with self.assertRaises(FileNotFoundError):
            scorer_et_publier.scorer_et_publier()

    def test_chainement_sourcing_vers_enrichissement(self):
        """Un acteur écrit par sourcer_acteurs_pro() (appelée comme fonction)
        doit être lisible par filtrer_et_enrichir() sans jamais repasser par
        le CLI/__main__ des deux modules — c'est le chemin réel du pipeline."""
        acteur_brut = {
            "type_acteur": "architecte",
            "nom_entreprise": "Cabinet Test Architecture",
            "siren": "123456789",
            "commune": "Commune Test",
        }
        self.fichier_bruts.write_text(json.dumps([acteur_brut]), encoding="utf-8")

        with patch.object(enrichir_acteurs_pro, "COMMUNES_CIBLES", ["Commune Test"]), \
             patch.object(enrichir_acteurs_pro, "commune_correspond", return_value=True), \
             patch.object(enrichir_acteurs_pro, "enrichir_un_acteur", return_value={
                 "site_web": None,
                 "email": "contact@cabinet-test.fr",
                 "telephone": None,
                 "linkedin_url": None,
                 "reseaux_sociaux": {},
                 "enrichissement_statut": "reussi",
                 "contact_exploitable": True,
             }):
            resultat = enrichir_acteurs_pro.filtrer_et_enrichir()

        self.assertEqual(len(resultat), 1)
        self.assertTrue(self.fichier_enrichis.exists())
        contenu = json.loads(self.fichier_enrichis.read_text(encoding="utf-8"))
        self.assertEqual(contenu[0]["email"], "contact@cabinet-test.fr")


if __name__ == "__main__":
    unittest.main()
