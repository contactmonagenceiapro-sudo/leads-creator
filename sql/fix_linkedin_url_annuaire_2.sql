-- Suite de sql/fix_linkedin_url_annuaire.sql (A.NOMME, CLAUDE MOUCHIKINE,
-- INSPIRATION) : 6 leads supplémentaires identifiés lors de l'audit
-- pré-lancement du 2026-08-14 avec le même bug — linkedin_url extrait de la
-- fiche d'un annuaire tiers (Ediware/dataprospects.fr, Polesocietes,
-- Libramemoria) plutôt que du professionnel lui-même. Confirmé par la
-- répétition du MÊME lien LinkedIn sur des architectes sans rapport entre
-- eux (4 leads distincts partageant linkedin.com/company/ediware).
--
-- Constaté au passage (non corrigé ici, hors périmètre de cette migration
-- qui ne porte que sur linkedin_url) : le champ `email` de ces mêmes 6
-- leads est ÉGALEMENT celui de l'annuaire (info@dataprospects.fr,
-- support@polesocietes.com, contact@libramemoria.com), pas celui du
-- professionnel — à traiter séparément.
--
-- Restreint aux 6 lignes concernées ET aux valeurs fausses précises
-- (jamais un WHERE générique sur nom_entreprise) : idempotent, sans risque
-- d'écraser une correction manuelle déjà faite entre-temps.

UPDATE leads_professionnels
SET linkedin_url = NULL,
    statut = 'necessite_contact_manuel'
WHERE nom_entreprise IN (
      'A2J CONCEPTS', 'CAPUCINE SERENNES', 'ALI ABOU HAMDAN (ECOARCHITECTURE)',
      'MAXIME POIRIER', 'BLEZAT ARCHITECTES ASSOCIES', 'OREA'
  )
  AND linkedin_url IN (
      'https://www.linkedin.com/company/ediware',
      'https://www.linkedin.com/company/polesocietes',
      'https://www.linkedin.com/company/libramemoria'
  );
