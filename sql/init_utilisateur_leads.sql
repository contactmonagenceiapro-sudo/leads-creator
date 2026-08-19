-- Extension du Portail Client (sql/init_portail_client.sql) aux clients B2C
-- (artisans acheteurs de devis, table `leads`) — jusqu'ici utilisateur_campagnes
-- ne couvrait que le B2B (client_final/campagnes, table leads_professionnels).
--
-- Décision du 20/08/2026 : construire l'espace client B2C DANS le dashboard
-- Streamlit existant plutôt que dans le portail HTML séparé (landing/,
-- Supabase Auth, table `artisans`) — ce dernier n'est qu'un stub non
-- connecté aux données ("Bientôt disponible", voir
-- landing/artisan-tableau-de-bord.html) alors que utilisateurs_dashboard/
-- auth.py est déjà en production pour le B2B. Un même compte
-- utilisateurs_dashboard peut donc désormais être lié à des campagnes
-- (B2B), à des leads (B2C), ou aux deux — voir dashboard/auth.py::
-- mes_leads_autorises() et dashboard/app_pages/portail_client.py.
--
-- Même modèle exact que utilisateur_campagnes : liaison many-to-many
-- (un compte peut suivre plusieurs artisans, ex: un commercial gérant
-- plusieurs profils ; un artisan pourrait aussi être lié à un second
-- compte). ON DELETE CASCADE des deux côtés : la suppression d'un artisan
-- (RGPD, voir dashboard/app_pages/suppression_rgpd.py) ne doit jamais
-- laisser un accès orphelin, la suppression d'un compte utilisateur ne doit
-- jamais laisser un lien mort.
CREATE TABLE IF NOT EXISTS utilisateur_leads (
    utilisateur_id UUID NOT NULL REFERENCES utilisateurs_dashboard(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    PRIMARY KEY (utilisateur_id, lead_id)
);

CREATE INDEX IF NOT EXISTS idx_utilisateur_leads_lead ON utilisateur_leads (lead_id);

-- RLS : même doctrine que sql/init_reclamations.sql / sql/fix_rls_leads_kpis.sql
-- — accès exclusivement via service_role (dashboard/), aucune policy n'a
-- d'effet réel ici. Activé immédiatement.
ALTER TABLE utilisateur_leads ENABLE ROW LEVEL SECURITY;
