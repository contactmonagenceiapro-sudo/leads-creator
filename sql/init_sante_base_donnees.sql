-- Surveillance continue de la base — voir scripts/controle_sante_bdd.py et
-- .github/workflows/controle_sante_bdd.yml (cron quotidien). Objectif :
-- détecter par exemple une régression RLS ou des demandes de devis
-- bloquées en attente de confirmation (voir
-- sql/init_demandes_devis_particuliers_confirmation.sql) AVANT qu'un
-- utilisateur ou un test manuel ne les découvre par hasard.
--
-- Une ligne par contrôle et par exécution (pas un upsert par type_controle)
-- : conservée volontairement en historique complet pour permettre le
-- contrôle "croissance anormale d'une table" (scripts/controle_sante_bdd.py)
-- de se comparer à l'exécution précédente, et pour que la page dashboard
-- (dashboard/app_pages/sante_bdd.py) puisse afficher une tendance sur 30
-- jours plutôt qu'un seul statut instantané.

CREATE TABLE IF NOT EXISTS sante_base_donnees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_controle TIMESTAMPTZ NOT NULL DEFAULT now(),
    type_controle TEXT NOT NULL,
    statut TEXT NOT NULL CHECK (statut IN ('ok', 'attention', 'critique')),
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sante_bdd_type_date ON sante_base_donnees (type_controle, date_controle DESC);
CREATE INDEX IF NOT EXISTS idx_sante_bdd_statut ON sante_base_donnees (statut);
CREATE INDEX IF NOT EXISTS idx_sante_bdd_date ON sante_base_donnees (date_controle DESC);

-- Même doctrine RLS que toutes les autres tables de ce projet : ENABLE ROW
-- LEVEL SECURITY sans aucune politique bloque anon/authenticated par
-- défaut, accès réservé à service_role (voir sql/fix_rls_leads_kpis.sql
-- pour le raisonnement complet). Particulièrement important ici : cette
-- table contiendrait sinon le détail de failles de sécurité potentielles
-- (RLS manquant, fonctions exposées...) lisible publiquement.
ALTER TABLE sante_base_donnees ENABLE ROW LEVEL SECURITY;
