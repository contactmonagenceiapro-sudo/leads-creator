-- Token public dédié pour les pages afficher_presentation/afficher_intake
-- (dashboard/pages_publiques.py) — avant ce correctif, ces deux vues
-- publiques résolvaient le lead STRICTEMENT par lead_id (clé primaire UUID,
-- routée en clair via st.query_params dans le lien envoyé par email, voir
-- mail_processor.py::envoyer_suivi_positif), sans token dédié imprévisible
-- comme signature_token ou token_confirmation — voir
-- audit/audit_verification_2026-09-08.md, constat M4.
--
-- Additive : n'affecte aucune ligne existante. Généré PARESSEUSEMENT
-- (uniquement au moment de l'envoi du lien, voir
-- mail_processor.py::envoyer_suivi_positif), pas rétroactivement pour tous
-- les leads existants — même principe que contracts.signature_token
-- (signature_interne.py::envoyer_contrat_signature_interne).
--
-- Décision produit du 08/09/2026 (confirmée avec l'utilisateur) : PAS de
-- repli sur lead_id pour les liens déjà envoyés avant ce correctif — ils
-- cesseront de fonctionner (page "introuvable"). Volume attendu faible
-- (ne concerne que les prospects ayant répondu positivement récemment et
-- pas encore cliqué) ; à relancer manuellement si besoin après déploiement.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS token_acces_public TEXT UNIQUE;

CREATE INDEX IF NOT EXISTS idx_leads_token_acces_public ON leads(token_acces_public);
