-- Enrichissement automatique des acteurs professionnels (extension additive
-- de sql/init_leads_professionnels.sql) : liens réseaux sociaux + traçabilité
-- de l'état d'enrichissement, pour ne jamais perdre silencieusement un
-- acteur dont le contact n'a pas pu être trouvé (voir
-- outbound_chantiers/enrichir_acteurs_pro.py::enrichir_un_acteur).
--
-- reste "best-effort/gratuit" : ces colonnes viennent du scraping du site
-- public de l'entreprise, jamais d'une donnée nominative achetée ou déduite.
*
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS linkedin_url TEXT;
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS reseaux_sociaux JSONB DEFAULT '{}'::jsonb;

ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS enrichissement_statut TEXT NOT NULL DEFAULT 'non_tente'
    CHECK (enrichissement_statut IN ('non_tente', 'reussi', 'partiel', 'echec'));
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS enrichi_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_leads_pro_enrichissement ON leads_professionnels (enrichissement_statut);
