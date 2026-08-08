-- Suivi des RÉPONSES reçues (taux de réponse par lead/campagne) — voir
-- mail_processor.py::journaliser_reponse(), seul appelant.
--
-- Réutilise la table email_events (sql/init_email_tracking.sql), déjà
-- pensée comme un journal d'événements par lead (type_evenement 'envoye'
-- déjà en place, 'ouvert'/'clique' prévus mais jamais alimentés depuis le
-- retrait du pixel de tracking) : ajoute simplement une nouvelle valeur
-- 'repondu', sans nouvelle table ni colonne sur leads/leads_professionnels.
-- Taux de réponse = distinct lead_id ('repondu') / distinct lead_id ('envoye'),
-- groupé par lead_type / client_final.
--
-- 'repondu' est posé UNIQUEMENT sur une vraie réponse humaine (mots-clés
-- positifs/négatifs/sans mot-clé) — jamais sur un bounce ni sur une
-- notification d'absence automatique (OOF), qui ne sont pas un signal
-- d'engagement du lead (voir _scanner_boite, ces deux cas font `continue`
-- avant tout appel à journaliser_reponse).
--
-- Recherche dynamique du nom de la contrainte CHECK existante (auto-générée
-- par Postgres, jamais nommée explicitement dans le CREATE TABLE d'origine)
-- plutôt que de deviner un nom fixe — même méthode que sql/init_bounces.sql
-- pour leads_professionnels_statut_check.
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'email_events'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%type_evenement%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE email_events DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE email_events
    ADD CONSTRAINT email_events_type_evenement_check
    CHECK (type_evenement IN ('envoye', 'ouvert', 'clique', 'repondu'));
