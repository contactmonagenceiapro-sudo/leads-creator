-- Audit sécurité/perf Supabase du 26/08/2026 (advisor "unindexed foreign
-- keys") : remboursements.lead_professionnel_id (FK vers
-- leads_professionnels, voir sql/init_remboursements.sql) n'avait pas
-- d'index, contrairement aux deux autres colonnes de filtrage de cette
-- table (idx_remboursements_statut, idx_remboursements_client) — ralentit
-- toute jointure/lookup avoir commercial <-> lead B2B à mesure que le
-- volume grandit.

CREATE INDEX IF NOT EXISTS idx_remboursements_lead_professionnel
    ON remboursements (lead_professionnel_id);
