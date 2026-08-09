-- Vue en LECTURE SEULE, additive (CREATE VIEW, aucune nouvelle table,
-- aucune écriture) : rapproche le score calculé par le pipeline
-- (outbound_chantiers/scorer_et_publier.py) du signal d'engagement réel
-- disponible aujourd'hui (email_events, sql/init_email_tracking.sql +
-- sql/init_email_reponses.sql), pour pouvoir un jour vérifier si le score
-- prédit effectivement la conversion — pas encore mesurable à ce stade
-- (0 réponse enregistrée sur les leads pro au moment de la création de
-- cette vue), mais la vue restera valable telle quelle une fois le volume
-- suffisant.
--
-- Consultable directement depuis l'éditeur SQL Supabase, ex :
--   SELECT * FROM v_score_vs_conversion_pro ORDER BY score_final DESC;
--   SELECT statut, COUNT(*), AVG(score_final) FROM v_score_vs_conversion_pro GROUP BY statut;

CREATE OR REPLACE VIEW v_score_vs_conversion_pro AS
SELECT
    lp.id,
    lp.client_final,
    lp.nom_entreprise,
    lp.type_acteur,
    lp.commune,
    lp.score_activite_chantiers,
    lp.score_final,
    lp.statut,
    lp.contacted,
    lp.contacted_at,
    lp.relance_count,
    COALESCE(ev.nb_envoye, 0) AS nb_envoye,
    COALESCE(ev.nb_repondu, 0) AS nb_repondu,
    (COALESCE(ev.nb_repondu, 0) > 0) AS a_repondu
FROM leads_professionnels lp
LEFT JOIN (
    SELECT
        lead_id,
        COUNT(*) FILTER (WHERE type_evenement = 'envoye') AS nb_envoye,
        COUNT(*) FILTER (WHERE type_evenement = 'repondu') AS nb_repondu
    FROM email_events
    WHERE lead_type = 'lead_professionnel'
    GROUP BY lead_id
) ev ON ev.lead_id = lp.id;
