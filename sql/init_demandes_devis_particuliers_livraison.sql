-- Mécanisme de livraison des demandes de devis vers les artisans clients
-- payants (leads.status='paye') — conception validée le 18/08/2026, voir
-- livraison_devis.py pour la logique complète (rapprochement corps de
-- métier + zone, round-robin, proposition/paiement pour l'unité, livraison
-- directe dans le quota pour l'abonnement).
--
-- statut passe de texte libre (aucune contrainte jusqu'ici) à une valeur
-- contrôlée à 4 états :
--   a_qualifier       (défaut, inchangé) — pas encore traitée
--   proposee          — formule à l'unité seulement : candidat identifié,
--                        lien de paiement envoyé, en attente de règlement
--                        (expire après DELAI_EXPIRATION_PROPOSITION_HEURES,
--                        voir livraison_devis.py — retombe alors sur le
--                        candidat round-robin suivant)
--   livree            — livrée définitivement (abonnement : immédiat dans
--                        le quota mensuel ; unité : après confirmation de
--                        paiement, voir dashboard/data_access.py::
--                        marquer_demande_devis_livree)
--   en_attente_artisan — aucun artisan client actif ne correspond au
--                        moment du traitement ; retraitée périodiquement
--                        (un nouvel artisan peut signer entre-temps)
--
-- Avant cette migration, aucune valeur n'était jamais posée hors
-- 'a_qualifier' (défaut) : la conversion des lignes existantes n'a donc
-- rien à faire, la contrainte s'applique seulement aux futures écritures.
ALTER TABLE demandes_devis_particuliers ALTER COLUMN statut SET DEFAULT 'a_qualifier';
ALTER TABLE demandes_devis_particuliers DROP CONSTRAINT IF EXISTS demandes_devis_particuliers_statut_check;
ALTER TABLE demandes_devis_particuliers ADD CONSTRAINT demandes_devis_particuliers_statut_check
    CHECK (statut IN ('a_qualifier', 'proposee', 'livree', 'en_attente_artisan'));

-- Destinataire de la livraison (ou du candidat actuellement en attente de
-- paiement pour 'proposee') — NULL tant que statut='a_qualifier' ou
-- 'en_attente_artisan'. Pas de ON DELETE CASCADE : la suppression d'un
-- lead (RGPD, voir dashboard/app_pages/suppression_rgpd.py) ne doit jamais
-- supprimer silencieusement l'historique de livraison d'une demande —
-- ON DELETE SET NULL fait retomber la ligne à un état cohérent (traçable
-- comme "livrée mais destinataire supprimé depuis" plutôt qu'une ligne
-- orpheline ou une suppression en cascade inattendue).
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS lead_id_livraison UUID
    REFERENCES leads(id) ON DELETE SET NULL;

ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS proposee_le TIMESTAMPTZ;
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS livree_le TIMESTAMPTZ;

-- Paiement de la proposition (formule à l'unité uniquement) — même
-- convention de nommage que contracts.stripe_payment_link_id/
-- stripe_payment_url/montant_centimes (sql/init.sql, sql/init_remboursements.sql).
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS stripe_payment_link_id TEXT;
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS stripe_payment_url TEXT;
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS montant_centimes INTEGER;
-- Optionnel (contrairement à contracts.stripe_payment_intent_id, jamais
-- exigé par data_access.py::marquer_demande_devis_payee_et_livree) : une
-- demande de devis n'est jamais remboursée individuellement dans ce projet
-- (seul un contrat l'est, voir sql/init_remboursements.sql) — gardé quand
-- même pour la traçabilité si l'admin l'a sous la main au moment de
-- confirmer le paiement.
ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;

CREATE INDEX IF NOT EXISTS idx_demandes_devis_statut ON demandes_devis_particuliers (statut);
CREATE INDEX IF NOT EXISTS idx_demandes_devis_lead_livraison ON demandes_devis_particuliers (lead_id_livraison);

-- RLS déjà actif sur cette table (voir sql/init_demandes_devis_particuliers_generique.sql) —
-- aucune des colonnes/contraintes ci-dessus ne le modifie. Réaffirmé
-- explicitement par doctrine (idempotent), pas parce qu'il y a un doute.
ALTER TABLE demandes_devis_particuliers ENABLE ROW LEVEL SECURITY;
