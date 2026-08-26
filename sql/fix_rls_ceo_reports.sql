-- URGENT — découvert automatiquement le 27/08/2026 par le tout premier run
-- de scripts/controle_sante_bdd.py (voir sql/init_sante_base_donnees.sql) :
-- `ceo_reports` (rapports d'analyse générés par ceo_agent.py — title,
-- content, summary, stats) était intégralement lisible via la clé anon
-- publique, avec des données réelles (vérifié directement : plusieurs
-- lignes retournées). Absente de l'audit manuel du 18-26/08/2026 (créée ou
-- repérée après coup) — exactement le type de régression que ce contrôle
-- automatisé quotidien est censé attraper avant qu'elle ne traîne.
--
-- Même mécanisme que toutes les autres tables de ce projet (voir
-- sql/fix_rls_leads_kpis.sql pour le raisonnement complet) : ENABLE ROW
-- LEVEL SECURITY sans aucune politique bloque anon/authenticated par
-- défaut, accès réservé à service_role.

ALTER TABLE ceo_reports ENABLE ROW LEVEL SECURITY;
