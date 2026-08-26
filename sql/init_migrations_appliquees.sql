-- Ce projet n'a pas d'outil de migration automatisé : chaque fichier
-- sql/*.sql est exécuté manuellement dans l'éditeur SQL Supabase (voir par
-- exemple l'en-tête de sql/init_artisans.sql). Rien ne distingue
-- aujourd'hui ce qui existe dans le repo de ce qui a effectivement été
-- appliqué en production — table de suivi minimale, une ligne insérée à la
-- main après chaque exécution.
--
-- Pas de contrainte d'unicité sur `nom` : plusieurs des fichiers sql/ de ce
-- projet sont explicitement ré-exécutables (ex. sql/init_demandes_devis_
-- particuliers_confirmation.sql, doctrine RLS "réaffirmée par doctrine,
-- idempotent") — chaque exécution réelle mérite sa propre ligne plutôt que
-- d'être bloquée ou d'écraser la précédente.
--
-- Usage : après avoir exécuté sql/xxx.sql dans l'éditeur Supabase, exécuter
-- ensuite :
--   INSERT INTO migrations_appliquees (nom) VALUES ('xxx.sql');

CREATE TABLE IF NOT EXISTS migrations_appliquees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nom TEXT NOT NULL,
    appliquee_le TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_migrations_appliquees_nom ON migrations_appliquees (nom);

-- Même doctrine RLS que toutes les autres tables de ce projet : ENABLE ROW
-- LEVEL SECURITY sans aucune politique bloque anon/authenticated par
-- défaut, accès réservé à service_role (voir sql/fix_rls_leads_kpis.sql
-- pour le raisonnement complet).
ALTER TABLE migrations_appliquees ENABLE ROW LEVEL SECURITY;
