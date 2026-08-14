-- Suite de sql/fix_linkedin_url_annuaire.sql / _2.sql / _3.sql, traitement
-- 2026-08-14. 3 domaines supplémentaires repérés en repassant en revue
-- tous les site_web de leads_professionnels (75 lignes) : pas des
-- "annuaires d'entreprises" à proprement parler, mais le même symptôme —
-- une page tierce sans rapport, retenue à tort comme "site propre" par
-- coïncidence de nom + commune (voir outbound_chantiers/enrichir_acteurs_pro.py
-- ::DOMAINES_A_IGNORER pour le détail de chaque cas, vérifié par
-- récupération réelle de la page avant traitement, pas supposé) :
--   - ATELIER 6 CS  : site_web = procedurecollective.fr (registre tiers
--     des procédures collectives), email = celui du registre lui-même.
--   - NOVA          : site_web = bricolage.fr (portail grand public),
--     fiche d'une tout autre entreprise (SIRET différent) — simple
--     homonymie de nom commercial ("Nova Piscines").
--   - RENAISSANCE   : site_web = theatrelarenaissance.com, un vrai
--     théâtre homonyme situé par coïncidence dans la même commune
--     (Oullins-Pierre-Bénite).
--
-- NOVA et RENAISSANCE étaient déjà en necessite_contact_manuel avec
-- email/linkedin_url déjà NULL (l'email n'a jamais pu être extrait de ces
-- pages sans rapport) : seule la note explicative est réellement nouvelle
-- pour ces deux-là. ATELIER 6 CS, en revanche, était statut='a_contacter'
-- avec un email actif (celui du registre tiers) : activement éligible à
-- la prochaine campagne d'envoi automatique avant cette correction.
--
-- Recherche élargie aux 75 lignes de leads_professionnels sur ces 3
-- nouveaux domaines : aucun autre lead affecté.

UPDATE leads_professionnels
SET email = NULL,
    linkedin_url = NULL,
    statut = 'necessite_contact_manuel',
    notes = 'Email et LinkedIn erronés (source : annuaire tiers) — nécessite une recherche manuelle du bon contact.'
WHERE nom_entreprise IN ('ATELIER 6 CS', 'NOVA', 'RENAISSANCE');
