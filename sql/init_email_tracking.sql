-- Suivi des interactions e-mail (ouvertures/clics) — voir email_tracking.py
-- (pixel invisible + redirection maison) et api/main.py (endpoints publics
-- /track/open, /track/click).
--
-- Une ligne par ÉVÉNEMENT (pas par lead) : un même envoi peut être ouvert
-- plusieurs fois, cliqué sur plusieurs liens différents — on garde
-- l'historique complet plutôt qu'un seul booléen "ouvert oui/non", pour
-- pouvoir alimenter un vrai flux d'activité dans le dashboard.
--
-- lead_type distingue les deux segments commerciaux jamais mélangés
-- ailleurs dans le projet (voir sql/init_leads_professionnels.sql) :
-- 'lead_artisan' -> table leads (Expertise Digitale, B2C artisans)
-- 'lead_professionnel' -> table leads_professionnels (B2B, outbound_chantiers/)

CREATE TABLE IF NOT EXISTS email_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_id UUID NOT NULL,
    type_evenement TEXT NOT NULL CHECK (type_evenement IN ('envoye', 'ouvert', 'clique')),
    lead_type TEXT NOT NULL CHECK (lead_type IN ('lead_artisan', 'lead_professionnel')),
    lead_id UUID NOT NULL,
    client_final TEXT,          -- toujours NULL pour lead_artisan (pas de notion de campagne/client_final)
    url_cible TEXT,             -- uniquement renseigné pour type_evenement='clique'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_events_tracking ON email_events (tracking_id);
CREATE INDEX IF NOT EXISTS idx_email_events_lead ON email_events (lead_type, lead_id);
CREATE INDEX IF NOT EXISTS idx_email_events_client ON email_events (client_final);
CREATE INDEX IF NOT EXISTS idx_email_events_type ON email_events (type_evenement);
