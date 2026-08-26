-- Étend le contrôle "couverture_rls" de scripts/controle_sante_bdd.py :
-- l'incident ceo_reports (27/08/2026) avait bien RLS ENABLED, mais une
-- policy "Allow all" (roles={public}, cmd=ALL, qual=true) qui annulait
-- entièrement la protection — un simple test "RLS actif ?" ne peut pas
-- détecter ça, il faut lire la définition des policies elles-mêmes.
--
-- pg_policies (pg_catalog) n'est pas accessible via l'API REST Supabase
-- (PostgREST n'expose que le schéma public) : cette vue republie les
-- colonnes utiles dans public, pour que le script puisse les lire avec la
-- même clé service_role que le reste du projet — pas d'accès direct au
-- catalogue Postgres possible autrement depuis ce dépôt (voir le même
-- constat dans scripts/generer_architecture.py pour les FK/PK).
--
-- security_invoker = true : exécutée avec les droits de l'appelant
-- (service_role), pas du créateur — même doctrine que
-- sql/fix_view_score_conversion_security_invoker.sql. Accès restreint à
-- service_role explicitement ci-dessous : le détail des policies (qual,
-- with_check) est lui-même une information sensible côté sécurité, pas
-- moins que les données qu'elles protègent.

CREATE OR REPLACE VIEW public.v_policies_rls
WITH (security_invoker = true) AS
SELECT schemaname, tablename, policyname, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE schemaname = 'public';

REVOKE ALL ON public.v_policies_rls FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.v_policies_rls TO service_role;
