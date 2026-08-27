-- Confirmation par e-mail avant toute livraison — une demande peut être
-- rapprochée d'un artisan plusieurs jours après son dépôt initial (délai
-- 48h en mode unité, ou attente de créneau round-robin, voir
-- livraison_devis.py) : rien ne garantissait jusqu'ici qu'à ce moment-là le
-- particulier est toujours joignable/intéressé. Diagnostic + plan validés
-- le 20/08/2026.
--
-- statut_confirmation, 4 états :
--   en_attente_confirmation (défaut) — email de confirmation envoyé (ou pas
--                             encore, voir date_envoi_confirmation),
--                             particulier pas encore cliqué
--   confirme                — cliqué dans les 24h, ÉLIGIBLE au rapprochement
--                             round-robin (voir livraison_devis.py)
--   non_confirme             — réservé pour un futur lien de refus explicite
--                             ("non, annulez ma demande") — AUCUN code de ce
--                             projet ne pose cette valeur à ce jour, gardée
--                             dans la contrainte pour ne pas avoir à migrer
--                             de nouveau le jour où ce lien est ajouté
--   expire                   — 24h écoulées sans clic (voir
--                             livraison_devis.py::expirer_confirmations_perimees,
--                             appelée par le même cron horaire que
--                             expirer_propositions_perimees) — jamais
--                             transmise à un artisan, jamais réattribuée
--
-- token_confirmation : lien à usage unique envoyé par email
-- (?vue=confirmer_demande&token=..., voir dashboard/pages_publiques.py::
-- afficher_confirmation_demande) — "usage unique" appliqué via
-- statut_confirmation (un 2e clic sur un token déjà 'confirme' affiche
-- "déjà confirmé", ne rejoue jamais la confirmation), jamais en supprimant
-- le token lui-même : la ligne reste consultable/traçable dans le dashboard
-- admin. UUID v4 standard (gen_random_uuid(), même génération qu'ailleurs
-- dans ce projet — voir demandes_devis_particuliers.id) : non énumérable,
-- non séquentiel, comme exigé pour un lien secret. Généré aussi côté
-- Python à l'insertion (voir pages_publiques.py, uuid.uuid4()) pour l'avoir
-- immédiatement sous la main sans relecture — le défaut ci-dessous n'est
-- qu'un filet de sécurité pour toute autre voie d'insertion future.
--
-- Table encore vide en production à ce jour (0 ligne, vérifié le
-- 20/08/2026) — même situation que sql/init_demandes_devis_particuliers_livraison.sql
-- lors de sa propre migration : aucune donnée existante à faire migrer,
-- aucun repli/backfill nécessaire pour les lignes déjà là.

ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS statut_confirmation TEXT
    NOT NULL DEFAULT 'en_attente_confirmation';
ALTER TABLE demandes_devis_particuliers DROP CONSTRAINT IF EXISTS demandes_devis_particuliers_statut_confirmation_check;
ALTER TABLE demandes_devis_particuliers ADD CONSTRAINT demandes_devis_particuliers_statut_confirmation_check
    CHECK (statut_confirmation IN ('en_attente_confirmation', 'confirme', 'non_confirme', 'expire'));

ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS token_confirmation UUID
    NOT NULL DEFAULT gen_random_uuid();
-- Ajoutée séparément (pas inline sur la colonne) : IF NOT EXISTS n'existe
-- pas pour un ADD CONSTRAINT UNIQUE, on passe donc par un index unique
-- idempotent — même contrainte, syntaxe simplement compatible avec un
-- ré-exécution sans erreur si déjà en place.
CREATE UNIQUE INDEX IF NOT EXISTS idx_demandes_devis_token_confirmation
    ON demandes_devis_particuliers (token_confirmation);

ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS date_envoi_confirmation TIMESTAMPTZ;
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS date_confirmation TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_demandes_devis_statut_confirmation ON demandes_devis_particuliers (statut_confirmation);

-- RLS : même doctrine que toutes les autres tables de ce projet (déjà actif
-- sur celle-ci, voir sql/init_demandes_devis_particuliers_generique.sql) —
-- réaffirmé par doctrine (idempotent), pas parce qu'il y a un doute. Le
-- token_confirmation n'est donc lisible/écrivable que via le rôle
-- service_role côté serveur (dashboard/supabase_client.py) : la page
-- publique de confirmation (?vue=confirmer_demande) résout le token via ce
-- même client, jamais via une clé anon exposée au navigateur.
ALTER TABLE demandes_devis_particuliers ENABLE ROW LEVEL SECURITY;
