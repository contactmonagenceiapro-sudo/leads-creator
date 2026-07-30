-- Détection des réponses automatiques d'absence (OOF) — voir
-- mail_processor.py::est_reponse_automatique_absence /
-- extraire_contact_alternatif. Un lead qui répond "actuellement absent(e)"
-- n'est ni un refus ni un intérêt : il obtient son propre statut, distinct
-- de 'contacte_attente_reponse', pour ne PAS être relancé automatiquement
-- par relance_prospects.py (qui filtre strictement sur ce statut) tant
-- qu'il n'a pas été recontacté manuellement.

-- email_alternatif conserve l'adresse de repli trouvée dans le message (pour
-- traçabilité), en plus du remplacement direct de la colonne email/eq
-- utilisée par les prochains envois (ceo_agent.py/relance_prospects.py lisent
-- lead['email']) — voir update_lead_absence/update_lead_professionnel_absence.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS email_alternatif TEXT;
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS email_alternatif TEXT;

-- Ajoute le statut 'absent_a_recontacter' au CHECK sur leads_professionnels.statut
-- (déjà étendu une première fois par sql/init_bounces.sql pour 'invalide') —
-- la table leads (artisans) n'a volontairement AUCUN CHECK sur status (texte
-- libre), donc aucune migration n'y est nécessaire pour ce même changement.
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
    DROP CONSTRAINT IF EXISTS leads_professionnels_statut_check;

ALTER TABLE leads_professionnels
    ADD CONSTRAINT leads_professionnels_statut_check
    CHECK (statut IN ('a_contacter', 'contacte_attente_reponse', 'interested', 'decline', 'sans_reponse', 'invalide', 'absent_a_recontacter'));
