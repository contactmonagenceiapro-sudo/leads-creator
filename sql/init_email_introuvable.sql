-- Distingue, dans email_status, "email jamais vérifié" ('unknown') de "email
-- activement recherché et introuvable" ('email_introuvable') — évite de
-- retraiter indéfiniment un lead pour lequel une recherche réelle
-- (email_enricher.py / outbound_chantiers/enrichir_acteurs_pro.py) n'a rien
-- trouvé, exactement comme un simple email jamais renseigné.
--
-- La table `leads` (artisans) n'a volontairement AUCUN CHECK sur ses
-- colonnes de statut (voir sql/init_bounces.sql, même convention conservée
-- ici) : aucune migration n'y est nécessaire pour accepter cette nouvelle
-- valeur, un simple UPDATE suffit.
--
-- Recherche dynamique du nom de la contrainte CHECK existante (auto-générée
-- par Postgres) plutôt que de deviner un nom fixe — même méthode que
-- sql/init_bounces.sql / sql/init_email_reponses.sql.
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'leads_professionnels'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%email_status%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE leads_professionnels DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE leads_professionnels
    ADD CONSTRAINT leads_professionnels_email_status_check
    CHECK (email_status IN ('valid', 'invalid_syntax', 'invalid_domain', 'unknown', 'email_introuvable'));
