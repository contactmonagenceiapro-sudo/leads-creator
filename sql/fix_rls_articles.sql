-- Audit sécurité Supabase du 26/08/2026 (advisors) : `articles` était la
-- seule table du projet sans RLS activé, exposée en lecture/écriture à la
-- clé anon publique. Recherche dans tout le repo (grep "articles") :
-- aucune référence à cette table — les seuls résultats trouvés
-- (construire_articles_cgv / construire_articles_cgv_b2c dans
-- generation_contrats.py) sont des fonctions Python qui génèrent le texte
-- des CGV des contrats, sans lien avec cette table. Table vide (0 ligne).
--
-- Décision (26/08/2026) : sécurisée plutôt que supprimée, en prévision d'un
-- usage futur (ex. génération de contenu SEO) — suppression envisageable
-- plus tard si son inutilité se confirme. Même mécanisme que toutes les
-- autres tables de ce projet (voir sql/fix_rls_leads_kpis.sql pour le
-- raisonnement complet) : ENABLE ROW LEVEL SECURITY sans aucune politique
-- bloque anon/authenticated par défaut, accès réservé à service_role.

ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
