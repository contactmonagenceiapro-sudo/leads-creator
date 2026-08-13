-- Nettoyage ponctuel : linkedin_url erroné pour 3 leads B2B (A.NOMME, CLAUDE
-- MOUCHIKINE, INSPIRATION — campagne S.B.G Travaux, Oullins-Pierre-Bénite).
--
-- Cause : outbound_chantiers/enrichir_acteurs_pro.py extrait linkedin_url du
-- HTML du "site" retenu pour l'acteur — ici, avant durcissement de
-- DOMAINES_A_IGNORER (commit 7d70ba0, 2026-08-08), ce "site" était en
-- réalité une fiche d'annuaire (entreprises.lagazettefrance.fr,
-- annuaire-pro-btp.fr). Le lien LinkedIn extrait était donc celui de
-- l'ANNUAIRE lui-même (ex: linkedin.com/company/la-gazette-france), pas
-- celui du professionnel — confirmé lors du diagnostic du 2026-08-13.
--
-- Un ré-enrichissement ultérieur ne pouvait pas corriger silencieusement ce
-- champ (déjà non vide) : voir dashboard/data_access.py::enrichir_lead_pro,
-- corrigé dans le même lot pour accepter forcer_reecriture=True.
--
-- Restreint aux 3 lignes concernées ET à la valeur fausse encore présente
-- (jamais un WHERE générique sur nom_entreprise) : idempotent, sans risque
-- d'écraser une correction manuelle déjà faite entre-temps.

UPDATE leads_professionnels
SET linkedin_url = NULL,
    statut = 'necessite_contact_manuel'
WHERE nom_entreprise IN ('A.NOMME', 'CLAUDE MOUCHIKINE', 'INSPIRATION')
  AND linkedin_url IN (
      'https://www.linkedin.com/company/la-gazette-france',
      'https://www.linkedin.com/company/annuaire-pro-btp'
  );
