-- Audit sécurité Supabase du 26/08/2026 (advisor "security_definer_view") :
-- v_score_vs_conversion_pro (sql/init_vue_score_conversion.sql) s'exécutait
-- avec les droits de son créateur plutôt que de l'appelant, ce qui peut
-- contourner le RLS des tables qu'elle interroge (leads_professionnels,
-- email_events). Vue en lecture seule, pensée pour être consultée à la
-- main dans l'éditeur SQL Supabase (donc en pratique toujours par un rôle
-- à droits élevés) — rien ne justifiait ce comportement par défaut.
-- Décision confirmée le 26/08/2026.

ALTER VIEW v_score_vs_conversion_pro SET (security_invoker = true);
