-- Aligne intake_responses.corps_metier (déclaré par l'artisan à la
-- signature, jusqu'ici texte libre — voir dashboard/pages_publiques.py::
-- afficher_intake, st.text_input) sur la même liste contrôlée que
-- demandes_devis_particuliers.corps_metier (sql/init_demandes_devis_particuliers_generique.sql,
-- 6 libellés SECTEURS_NAF) — prérequis du mécanisme de rapprochement
-- devis-artisans (diagnostic + conception validés le 18/08/2026) : sans
-- vocabulaire commun des deux côtés, aucun rapprochement fiable n'est
-- possible (un artisan tapant "Plombier" ne matcherait jamais
-- "Bâtiment - Plomberie / Chauffage").
--
-- Vérifié avant migration : une seule ligne existe dans intake_responses
-- en production (id 7455a302..., ligne de test E2E du 26/07, corps_metier
-- déjà NULL) — un CHECK simple (corps_metier IN (...)) n'a besoin d'aucune
-- clause NULL explicite : Postgres considère une contrainte CHECK
-- satisfaite quand l'expression s'évalue à NULL (donc NULL reste toujours
-- accepté sans traitement particulier), seules les valeurs non-NULL hors
-- liste seraient rejetées.
--
-- communes_couvertes (jsonb, liste de communes) : nouvelle colonne
-- structurée pour permettre un matching géographique fiable, à côté de
-- zone_activite (texte libre, conservé tel quel pour l'affichage humain
-- dans le devis PDF — dashboard/contrats_signature.py — jamais utilisé
-- pour le matching). Même principe que campagnes.communes_cibles côté B2B
-- (sql/init_campagnes.sql) : liste de communes parmi
-- scraper_batiment.py::VILLES_CIBLES (zone couverte par le scraping B2C,
-- Grand Est + Métropole de Lyon).

-- La colonne corps_metier EXISTE déjà (ajoutée par
-- sql/init_intake_responses_leads_b2c.sql, en TEXT libre, sans contrainte) :
-- ADD COLUMN IF NOT EXISTS ne suffit donc pas pour lui ajouter la
-- contrainte manquante — ADD CONSTRAINT explicite à la place. Nom de
-- contrainte fixe (pas de recherche dynamique comme sql/init_bounces.sql,
-- inutile ici : c'est la première contrainte posée sur cette colonne, donc
-- son nom est connu et jamais généré automatiquement par Postgres).
-- DROP puis ADD (pas de IF NOT EXISTS pour les contraintes en Postgres) :
-- rejouable sans erreur si ce script est relancé par erreur.
ALTER TABLE intake_responses DROP CONSTRAINT IF EXISTS intake_responses_corps_metier_check;
ALTER TABLE intake_responses ADD CONSTRAINT intake_responses_corps_metier_check
    CHECK (corps_metier IN (
        'Bâtiment - Plâtrerie',
        'Bâtiment - Électricité',
        'Bâtiment - Isolation / Rénovation Énergétique',
        'Bâtiment - Gros Œuvre',
        'Bâtiment - Second Œuvre / Rénovation',
        'Bâtiment - Plomberie / Chauffage'
    ));

ALTER TABLE intake_responses ADD COLUMN IF NOT EXISTS communes_couvertes JSONB NOT NULL DEFAULT '[]';
