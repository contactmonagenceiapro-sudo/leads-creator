-- Ajoute le statut 'brouillon' au CHECK existant sur campagnes.statut
-- (sql/init_campagnes.sql) — permet de préparer à l'avance une campagne
-- "modèle" vide (secteur/description/communes/types de contacts non encore
-- définitifs), à dupliquer ou renommer au nom d'un vrai client au moment de
-- lancer la prospection, sans configurer quoi que ce soit dans l'urgence.
-- Voir dashboard/app_pages/sourcing.py et api/main.py (endpoints
-- /campagnes/{nom_client}/dupliquer et /renommer).
--
-- Une campagne 'brouillon' n'est JAMAIS reprise par le cron quotidien
-- (api/main.py filtre sur statut=eq.active) : aucun risque de sourcing ou
-- d'envoi déclenché sur un modèle vide.
--
-- Recherche dynamique du nom de la contrainte (auto-généré par Postgres),
-- + DROP CONSTRAINT IF EXISTS explicite en filet de sécurité si ce script
-- est rejoué après un échec partiel (même approche que sql/init_bounces.sql).
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'campagnes'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%statut%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE campagnes DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE campagnes
    DROP CONSTRAINT IF EXISTS campagnes_statut_check;

ALTER TABLE campagnes
    ADD CONSTRAINT campagnes_statut_check
    CHECK (statut IN ('brouillon', 'active', 'en_pause', 'archivee'));
