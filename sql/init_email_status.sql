-- Colonne de validation structurelle de l'email, calculée AVANT tout envoi
-- (voir email_validator.py::email_status) — distincte du statut de cycle de
-- vie du lead (status/statut, qui reflète où en est la relation commerciale,
-- y compris 'invalide' posé sur hard bounce par mail_processor.py). Un lead
-- dont l'email échoue à la validation est conservé et flaggé ici, jamais
-- supprimé ni silencieusement écarté (voir lead_worker.py::inserer_lead et
-- outbound_chantiers/scorer_et_publier.py::publier_en_base).
--
-- 'valid' : format + domaine + MX confirmés.
-- 'invalid_syntax' : format RFC basique non respecté.
-- 'invalid_domain' : domaine jetable connu, ou sans aucun enregistrement MX.
-- 'unknown' : vérification impossible (DNS injoignable/timeout) ou aucun
--             email jamais fourni — jamais traité comme un échec (voir
--             email_validator.py, même principe que possede_enregistrement_mx).
--
-- ADD COLUMN ... DEFAULT s'applique aussi aux lignes déjà existantes
-- (opération de métadonnées seule en Postgres 11+, aucune réécriture de
-- table) : aucune ligne ne reste NULL après cette migration.

-- La table leads (artisans) n'a volontairement AUCUN CHECK sur ses colonnes
-- de statut (voir sql/init_bounces.sql) : même convention conservée ici.
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS email_status TEXT DEFAULT 'unknown';

ALTER TABLE leads_professionnels
    ADD COLUMN IF NOT EXISTS email_status TEXT DEFAULT 'unknown'
        CHECK (email_status IN ('valid', 'invalid_syntax', 'invalid_domain', 'unknown'));

CREATE INDEX IF NOT EXISTS idx_leads_email_status ON leads (email_status);
CREATE INDEX IF NOT EXISTS idx_leads_pro_email_status ON leads_professionnels (email_status);
