-- Gestion des rebonds e-mail (bounces) — voir mail_processor.py (détection
-- + parsing DSN) et email_blacklist.py (lecture/écriture de cette table).
--
-- Table INDÉPENDANTE des lignes leads/leads_professionnels : un email peut
-- réapparaître sur une NOUVELLE ligne lead après un re-scraping ultérieur
-- (nouvel id, statut réinitialisé à 'new'/'a_contacter'), sans que le
-- statut 'invalide' posé sur l'ancienne ligne ne protège quoi que ce soit.
-- Seule cette table, consultée avant CHAQUE campagne (ceo_agent.py,
-- relance_prospects.py, outbound_chantiers/outbound_pro_btp.py), survit à
-- ça durablement.

CREATE TABLE IF NOT EXISTS emails_blacklistes (
    email TEXT PRIMARY KEY,
    domaine TEXT,
    raison TEXT NOT NULL,          -- 'hard_bounce' pour l'instant, extensible
    code_statut TEXT,              -- code DSN brut (ex: '5.1.1')
    diagnostic TEXT,               -- texte brut du serveur distant (Diagnostic-Code)
    lead_type TEXT,                -- 'lead_artisan' | 'lead_professionnel' | NULL si inconnu
    lead_id UUID,
    blackliste_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_emails_blacklistes_domaine ON emails_blacklistes (domaine);

-- Ajoute le statut 'invalide' au CHECK existant sur leads_professionnels.statut
-- (sql/init_leads_professionnels.sql) — nécessaire pour marquer un acteur pro
-- dont l'email a fait l'objet d'un hard bounce. La table leads (artisans) n'a
-- volontairement AUCUN CHECK sur sa colonne status (texte libre), donc aucune
-- migration n'y est nécessaire pour ce même changement.
--
-- Recherche dynamique du nom de la contrainte (auto-généré par Postgres,
-- jamais nommé explicitement dans le CREATE TABLE d'origine) plutôt que de
-- deviner un nom fixe qui pourrait ne pas correspondre.
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'leads_professionnels'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%statut%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE leads_professionnels DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE leads_professionnels
    ADD CONSTRAINT leads_professionnels_statut_check
    CHECK (statut IN ('a_contacter', 'contacte_attente_reponse', 'interested', 'decline', 'sans_reponse', 'invalide'));
