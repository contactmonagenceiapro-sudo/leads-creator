"""
Test de régression pour le correctif M1 (voir audit/audit_verification_2026-09-08.md) :
un compte Zoho bloqué pendant livraison_devis.py devait, avant ce correctif,
s'afficher "succeeded" côté cron GitHub Actions bien qu'aucune
livraison/proposition/ré-attribution n'ait réellement pu partir — même
défaut que celui corrigé le 05/09/2026 (commit e338806) sur
ceo_agent.py/relance_prospects.py, jamais répercuté ici.

Toute interaction Supabase/Zoho est mockée : aucun appel réseau réel,
aucun e-mail envoyé. Ne lance jamais livraison_devis.py en conditions
réelles pour vérifier ce correctif (voir consigne du projet) — ce test
appelle directement les fonctions concernées, avec toutes leurs
dépendances externes remplacées par des doublures de test.

Usage : python3 -m unittest tests.test_livraison_devis_zoho_bloque -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import livraison_devis
from alertes import CompteZohoBloqueError


class _RequeteFactice:
    """Doublure minimaliste du query-builder supabase-py : chaque méthode
    de filtre/écriture se renvoie elle-même pour permettre le chaînage
    (.select().eq().lt()...), et execute() renvoie toujours les mêmes
    données de test — suffisant pour ces tests, qui ne vérifient jamais le
    contenu des écritures (update/insert), seulement le comportement de
    contrôle (interruption sur CompteZohoBloqueError)."""

    def __init__(self, donnees):
        self._donnees = donnees

    def select(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self._donnees)


class _SupabaseFactice:
    def __init__(self, donnees):
        self._donnees = donnees

    def table(self, _nom):
        return _RequeteFactice(self._donnees)


class TestInterruptionCompteZohoBloque(unittest.TestCase):
    def test_traiter_demandes_en_attente_remonte_interruption(self):
        demande = {
            "id": "d1", "corps_metier": "plombier", "commune": "Lyon",
            "statut": "a_qualifier", "statut_confirmation": "confirme",
        }
        with patch.object(livraison_devis, "supabase", _SupabaseFactice([demande])), \
             patch.object(livraison_devis, "_artisans_clients_actifs", return_value=[]), \
             patch.object(livraison_devis, "_envois_livraison_aujourdhui", return_value=0), \
             patch.object(livraison_devis, "traiter_demande", side_effect=CompteZohoBloqueError("bloqué")):
            resultat = livraison_devis.traiter_demandes_en_attente()

        self.assertTrue(
            resultat["interrompu_bloque_compte"],
            "traiter_demandes_en_attente() doit signaler l'interruption pour "
            "compte Zoho bloqué (constat M1) — sinon un blocage passe inaperçu.",
        )
        self.assertEqual(resultat["total"], 1)

    def test_traiter_demandes_en_attente_pas_d_interruption_en_fonctionnement_normal(self):
        demande = {
            "id": "d1", "corps_metier": "plombier", "commune": "Lyon",
            "statut": "a_qualifier", "statut_confirmation": "confirme",
        }
        with patch.object(livraison_devis, "supabase", _SupabaseFactice([demande])), \
             patch.object(livraison_devis, "_artisans_clients_actifs", return_value=[]), \
             patch.object(livraison_devis, "_envois_livraison_aujourdhui", return_value=0), \
             patch.object(livraison_devis, "traiter_demande", return_value="en_attente_artisan"):
            resultat = livraison_devis.traiter_demandes_en_attente()

        self.assertFalse(resultat["interrompu_bloque_compte"])
        self.assertEqual(resultat["en_attente"], 1)

    def test_expirer_propositions_perimees_remonte_interruption(self):
        perimee = {
            "id": "p1", "lead_id_livraison": "artisan-1", "corps_metier": "plombier",
            "commune": "Lyon", "montant_centimes": 5000, "proposee_le": "2026-09-01T00:00:00+00:00",
        }
        with patch.object(livraison_devis, "supabase", _SupabaseFactice([perimee])), \
             patch.object(livraison_devis, "_artisans_clients_actifs", return_value=[]), \
             patch.object(livraison_devis, "_envois_livraison_aujourdhui", return_value=0), \
             patch.object(livraison_devis, "traiter_demande", side_effect=CompteZohoBloqueError("bloqué")):
            resultat = livraison_devis.expirer_propositions_perimees()

        self.assertTrue(
            resultat["interrompu_bloque_compte"],
            "expirer_propositions_perimees() doit signaler l'interruption pour "
            "compte Zoho bloqué (constat M1).",
        )

    def test_main_renvoie_1_si_interrompu_par_compte_zoho_bloque(self):
        with patch.object(livraison_devis, "_acquerir_verrou", return_value=True), \
             patch.object(livraison_devis, "_liberer_verrou"), \
             patch.object(livraison_devis, "expirer_confirmations_perimees", return_value={}), \
             patch.object(livraison_devis, "expirer_propositions_perimees",
                           return_value={"interrompu_bloque_compte": False}), \
             patch.object(livraison_devis, "traiter_demandes_en_attente",
                           return_value={"interrompu_bloque_compte": True}), \
             patch("sys.argv", ["livraison_devis.py"]):
            code_sortie = livraison_devis.main()

        self.assertEqual(
            code_sortie, 1,
            "main() doit renvoyer un code de sortie non-nul si le compte Zoho "
            "est bloqué pendant le run — sinon le cron horaire livraison_devis.yml "
            "s'affiche 'succeeded' malgré un vrai échec (constat M1), en plein "
            "engagement public de délai 48h envers des clients payants.",
        )

    def test_main_renvoie_0_en_fonctionnement_normal(self):
        with patch.object(livraison_devis, "_acquerir_verrou", return_value=True), \
             patch.object(livraison_devis, "_liberer_verrou"), \
             patch.object(livraison_devis, "expirer_confirmations_perimees", return_value={}), \
             patch.object(livraison_devis, "expirer_propositions_perimees",
                           return_value={"interrompu_bloque_compte": False}), \
             patch.object(livraison_devis, "traiter_demandes_en_attente",
                           return_value={"interrompu_bloque_compte": False}), \
             patch("sys.argv", ["livraison_devis.py"]):
            code_sortie = livraison_devis.main()

        self.assertEqual(code_sortie, 0)

    def test_main_expirer_seul_renvoie_1_si_interrompu(self):
        """--expirer-seul ne traite jamais traiter_demandes_en_attente() —
        l'interruption doit malgré tout être remontée si elle survient
        pendant expirer_propositions_perimees()."""
        with patch.object(livraison_devis, "_acquerir_verrou", return_value=True), \
             patch.object(livraison_devis, "_liberer_verrou"), \
             patch.object(livraison_devis, "expirer_confirmations_perimees", return_value={}), \
             patch.object(livraison_devis, "expirer_propositions_perimees",
                           return_value={"interrompu_bloque_compte": True}), \
             patch.object(livraison_devis, "traiter_demandes_en_attente") as mock_traiter, \
             patch("sys.argv", ["livraison_devis.py", "--expirer-seul"]):
            code_sortie = livraison_devis.main()

        mock_traiter.assert_not_called()
        self.assertEqual(code_sortie, 1)


if __name__ == "__main__":
    unittest.main()
