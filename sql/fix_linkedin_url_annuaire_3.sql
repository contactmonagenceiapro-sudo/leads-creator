-- Suite de sql/fix_linkedin_url_annuaire.sql et
-- sql/fix_linkedin_url_annuaire_2.sql, traitement 2026-08-14.
--
-- 1) FLORENCE BRAILLON ARCHITECTE — traitée par PRUDENCE (pas de bug
--    confirmé, contrairement aux 9 leads précédents) : le linkedin_url a
--    été extrait du domaine PROPRE de l'agence (atelierbraillon.com), donc
--    "remychassignol" pourrait être un associé/collaborateur légitime.
--    Vidé quand même par précaution ; site_web/email/telephone volontairement
--    INCHANGÉS (cohérents, pas de doute sur ces champs-là).
--
-- 2) Les 6 leads déjà signalés avec un linkedin_url d'annuaire ont
--    ÉGALEMENT un email d'annuaire (info@dataprospects.fr,
--    support@polesocietes.com, contact@libramemoria.com) — plus grave que
--    le LinkedIn, puisqu'un email envoyé à ces adresses ne touche jamais
--    le bon interlocuteur. Vidé ici ; statut déjà 'necessite_contact_manuel'
--    depuis le traitement précédent (inchangé), note explicative ajoutée
--    dans le champ `notes` (déjà existant, générique) pour tracer le motif.
--
-- Recherche élargie à tout le reste de leads_professionnels (75 lignes) sur
-- ces mêmes domaines + tous les domaines d'annuaire déjà connus du projet
-- (voir outbound_chantiers/enrichir_acteurs_pro.py::DOMAINES_A_IGNORER) :
-- aucun autre lead affecté.
--
-- ATTENTION — risque de régression NON traité par cette migration :
-- outbound_chantiers/enrichir_acteurs_pro.py::DOMAINES_A_IGNORER ne
-- contient TOUJOURS PAS dataprospects.fr / polesocietes.com /
-- libramemoria.com. Si le sourcing B2B re-scrute ces mêmes entreprises
-- (même nom_entreprise + client_final), scorer_et_publier.py::publier_en_base
-- réécrit email/site_web/linkedin_url à CHAQUE republish (upsert
-- inconditionnel sur ces colonnes, contrairement à `statut` qui n'est
-- inclus dans le payload que pour un acteur jamais vu) — le prochain cycle
-- de sourcing pourrait donc silencieusement restaurer les valeurs fausses.
-- Correctif root-cause (ajouter ces domaines à DOMAINES_A_IGNORER) non
-- appliqué ici, hors périmètre de cette migration.

UPDATE leads_professionnels
SET linkedin_url = NULL,
    statut = 'necessite_contact_manuel'
WHERE nom_entreprise = 'FLORENCE BRAILLON ARCHITECTE'
  AND linkedin_url = 'http://www.linkedin.com/in/remychassignol';

UPDATE leads_professionnels
SET email = NULL,
    notes = 'Email et LinkedIn erronés (source : annuaire tiers) — nécessite une recherche manuelle du bon contact.'
WHERE nom_entreprise IN (
      'A2J CONCEPTS', 'CAPUCINE SERENNES', 'ALI ABOU HAMDAN (ECOARCHITECTURE)',
      'MAXIME POIRIER', 'BLEZAT ARCHITECTES ASSOCIES', 'OREA'
  )
  AND email IN ('info@dataprospects.fr', 'support@polesocietes.com', 'contact@libramemoria.com');
