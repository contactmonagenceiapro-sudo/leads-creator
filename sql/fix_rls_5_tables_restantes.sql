-- Suite de l'audit pré-lancement du 2026-08-14 (voir aussi
-- sql/fix_rls_leads_kpis.sql) : vérification du RLS sur les 5 tables alors
-- vides, non testables en conditions réelles faute de données. Test refait
-- ici en insérant une ligne factice ("TEST_RLS_VERIF") via service_role,
-- lecture tentée via la clé anon publique, puis suppression immédiate de
-- la ligne de test — méthode indépendante du contenu réel de la table.
--
-- Résultat du test :
--   contracts                     : déjà protégée (RLS actif, aucune action)
--   registre_suppressions_rgpd    : déjà protégée (RLS actif, aucune action)
--   demandes_devis_particuliers   : déjà protégée (RLS actif, aucune action)
--   agent_memories                : EXPOSÉE (RLS absent) -> corrigée ci-dessous
--   tasks                         : EXPOSÉE (RLS absent) -> corrigée ci-dessous
--
-- Même mécanisme que sql/fix_rls_leads_kpis.sql : ENABLE ROW LEVEL SECURITY
-- sans aucune politique bloque anon/authenticated par défaut, sans affecter
-- service_role (seul rôle utilisé par ce projet, qui contourne le RLS par
-- conception Supabase). Aucun usage public (landing/) ni même aucun usage
-- côté application trouvé pour agent_memories/tasks au moment de cette
-- migration (tables issues du scaffolding initial du projet, jamais
-- branchées) : aucun risque de casser un accès légitime.

ALTER TABLE agent_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
