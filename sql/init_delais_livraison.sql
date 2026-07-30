-- Trace des délais de livraison temporaires (bounce 4xx / "Delay") sur un
-- lead — voir mail_processor.py::enregistrer_delai_livraison, appelée
-- depuis traiter_bounce() sur la branche 'delayed'. Ne change JAMAIS le
-- statut du lead ni ne bloque la campagne (contrairement au hard bounce
-- définitif, qui invalide via sql/init_bounces.sql) : sert uniquement à
-- repérer, visible dans le dashboard, un lead dont les emails échouent
-- temporairement de façon répétée (boîte pleine, greylisting persistant...).

ALTER TABLE leads ADD COLUMN IF NOT EXISTS nb_delais_livraison INTEGER DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS dernier_delai_livraison_at TIMESTAMPTZ;

ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS nb_delais_livraison INTEGER DEFAULT 0;
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS dernier_delai_livraison_at TIMESTAMPTZ;
