-- Ajoute le statut 'en_cours' au CHECK existant sur stripe_webhook_events.statut
-- (sql/init_stripe_webhook_events.sql) — même famille de correctif que
-- sql/fix_remboursements_statut_en_cours.sql (04/09/2026), appliqué ici à
-- scripts/traiter_paiements_stripe.py::traiter_file_attente(). Voir
-- audit/audit_verification_2026-09-08.md, constat M7 : aucun verrou
-- n'empêchait un run manuel local de chevaucher un run cron */10min — le
-- SELECT statut='recu' puis le traitement métier est un check-then-act
-- classique. Risque déjà qualifié de limité par l'audit (_traiter_contrat/
-- _traiter_demande_devis sont idempotents au niveau métier, jamais de
-- double encaissement Stripe réel), mais une fenêtre TOCTOU réelle
-- subsistait (double écriture journal_audit_admin, double alerte Discord).
--
-- 'en_cours' sert de verrou PAR ÉVÉNEMENT (pas un verrou global de script,
-- inutile ici — chaque ligne de la file s'y prête individuellement) :
-- traiter_file_attente() fait désormais un UPDATE conditionnel
-- (WHERE statut = 'recu') vers 'en_cours' AVANT de traiter chaque
-- événement — Postgres sérialise les UPDATE concurrents au niveau de la
-- ligne, donc un seul appelant peut voir cette transition réussir même en
-- cas de chevauchement. Le second passe simplement à l'événement suivant.
--
-- Recherche dynamique du nom de la contrainte (auto-généré par Postgres),
-- même approche que sql/fix_remboursements_statut_en_cours.sql.
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'stripe_webhook_events'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%statut%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE stripe_webhook_events DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE stripe_webhook_events
    DROP CONSTRAINT IF EXISTS stripe_webhook_events_statut_check;

ALTER TABLE stripe_webhook_events
    ADD CONSTRAINT stripe_webhook_events_statut_check
    CHECK (statut IN ('recu', 'en_cours', 'traite', 'echec', 'ignore'));
