"""
Constantes partagées entre scripts racine ET dashboard/ — module minimal
volontairement sans dépendance (pas d'import Supabase/Streamlit ici), pour
rester importable aussi bien par un cron autonome que par le dashboard.

Voir audit/audit_verification_2026-09-08.md, constat m1 : LEADS_TEST_A_EXCLURE
n'existait jusqu'ici QUE dans dashboard/data_access.py (13 requêtes KPI/stats),
sans tiers-lieu partagé accessible aux scripts racine — scorer_leads.py et
livraison_devis.py ne l'appliquaient pas, un risque théorique (nécessite que
le fixture atteigne un statut normalement réservé aux vrais clients) mais
réel en l'absence de tout filtre explicite dans ces deux scripts.
"""

# Leads de test/démo créés manuellement dans `leads` (jamais de vrais
# prospects) : __TEST_E2E_TUNNEL__ (id fixe, réutilisé pour dérouler le
# tunnel de vente à blanc — voir mémoire e2e_tunnel_test_fixture) boucle ses
# e-mails vers la boîte de l'agence elle-même, ce qui gonflait artificiellement
# le taux de réponse artisans. Exclus des KPIs/vues admin ET de la logique
# métier réelle (scoring, round-robin de livraison) pour ne jamais fausser
# une lecture des vraies statistiques ni traiter ce fixture comme un artisan
# client actif — jamais supprimé pour autant, ce lead sert toujours à
# valider le tunnel.
LEADS_TEST_A_EXCLURE = (
    "a51d80c8-8363-42a6-87c8-7481911ecc2b",  # __TEST_E2E_TUNNEL__
    # "entreprise de test"/"test" (ce66b08a.../b45fc2f7...) supprimées le
    # 27/08/2026 (nettoyage données de test résiduelles, voir contrôle
    # santé donnees_test_residuelles) — retirées d'ici plutôt que laissées
    # en référence à des lignes qui n'existent plus.
)
