-- Audit sécurité/perf Supabase du 26/08/2026 (advisor "auth_rls_initplan") :
-- la politique artisans_select_own (sql/init_artisans.sql) appelait
-- auth.uid() telle quelle dans USING -> Postgres la ré-évalue à CHAQUE
-- ligne scannée. En l'enveloppant dans un sous-select, le planner la
-- traite comme une valeur stable (InitPlan), évaluée une seule fois par
-- requête — même résultat (un artisan ne voit toujours que sa propre
-- ligne), juste plus rapide à mesure que la table grandit. Voir
-- https://supabase.com/docs/guides/database/postgres/row-level-security#call-functions-with-select

DROP POLICY IF EXISTS "artisans_select_own" ON artisans;
CREATE POLICY "artisans_select_own"
    ON artisans FOR SELECT
    USING ((select auth.uid()) = id);
