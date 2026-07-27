-- Système de remboursement automatisé — plateforme multi-clients/secteurs.
--
-- Deux origines bien distinctes, jamais confondues :
--
-- 1. REMBOURSEMENT STRIPE RÉEL (type_remboursement='stripe') : lié à un
--    contrat artisan payé (table `contracts`, cœur de métier Expertise
--    Digitale — devis site vitrine 990€). De l'argent bouge réellement via
--    l'API Stripe. Nécessite le payment_intent du paiement d'origine.
--
-- 2. AVOIR COMMERCIAL (type_remboursement='avoir_commercial') : lié à un
--    lead professionnel B2B (table `leads_professionnels`) signalé invalide
--    ou non qualifié par le client final (garantie déjà annoncée, ex.
--    échange avec S.B.G Travaux). C'est une écriture comptable/un crédit,
--    PAS un remboursement Stripe — il n'existe aujourd'hui aucune
--    facturation/paiement réel pour les leads B2B dans ce système. Ne pas
--    confondre les deux : un avoir n'engage aucun mouvement bancaire tant
--    qu'un système de facturation B2B n'existe pas.

-- Le webhook Stripe (checkout.session.completed) capturait le
-- payment_link mais jamais le payment_intent — impossible d'émettre un
-- remboursement API sans lui (stripe.Refund.create nécessite payment_intent
-- ou charge). Ajout rétroactif, sans impact sur les contrats déjà payés
-- (colonne nullable ; ces anciens contrats devront être remboursés
-- manuellement depuis le dashboard Stripe si besoin, faute de payment_intent connu).
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;

-- Marqueur de signalement d'un lead B2B invalide/non qualifié par le client
-- final — c'est ce qui déclenche automatiquement l'avoir commercial.
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS signale_invalide BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS motif_invalidite TEXT;

CREATE TABLE IF NOT EXISTS remboursements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id UUID REFERENCES contracts(id),
    lead_professionnel_id UUID REFERENCES leads_professionnels(id),
    client_final TEXT,                    -- toujours renseigné, même pour un remboursement Stripe (facilite le filtrage dashboard)
    montant_centimes INTEGER NOT NULL,
    motif TEXT NOT NULL,
    type_remboursement TEXT NOT NULL DEFAULT 'stripe'
        CHECK (type_remboursement IN ('stripe', 'avoir_commercial')),
    statut TEXT NOT NULL DEFAULT 'en_attente'
        CHECK (statut IN ('en_attente', 'valide', 'rembourse', 'echoue', 'rejete')),
    stripe_refund_id TEXT,
    demande_par TEXT,                     -- 'client', 'systeme_automatique', ou identifiant de l'opérateur
    created_at TIMESTAMPTZ DEFAULT NOW(),
    traite_at TIMESTAMPTZ,
    CONSTRAINT remboursement_a_une_origine CHECK (
        contract_id IS NOT NULL OR lead_professionnel_id IS NOT NULL OR client_final IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_remboursements_statut ON remboursements (statut);
CREATE INDEX IF NOT EXISTS idx_remboursements_contract ON remboursements (contract_id);
CREATE INDEX IF NOT EXISTS idx_remboursements_client ON remboursements (client_final);
