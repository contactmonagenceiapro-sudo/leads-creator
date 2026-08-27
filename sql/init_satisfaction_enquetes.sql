-- Module 4 (pilotage) — satisfaction client (B2C, artisans). Une enquête
-- envoyée automatiquement 1 semaine après le PREMIER paiement confirmé
-- (contracts.paid_at, voir scripts/envoyer_enquetes_satisfaction.py) —
-- délai confirmé par l'utilisateur le 27/08/2026 : assez tôt pour capter un
-- avis à chaud, assez tard pour que l'artisan ait reçu ses premiers leads.
--
-- Envoi automatique (cron), réponse et notation SAISIES MANUELLEMENT par un
-- admin (dashboard/app_pages/satisfaction.py) — même doctrine que
-- marquer_contrat_paye / marquer_demande_devis_payee_et_livree dans ce
-- projet (aucun webhook/formulaire public, confirmation humaine) : la
-- réponse arrive par e-mail dans la boîte déjà surveillée par
-- mail_processor.py, dont la classification (interested/decline/OOF) n'est
-- pas conçue pour un texte libre de satisfaction — pas de tentative de
-- l'y raccrocher automatiquement, ni de nouveau formulaire public créé ici
-- (verrouillage volontaire du périmètre, voir aussi confirmer_demande en
-- cours de construction par ailleurs, pour ne pas dupliquer ce mécanisme).
--
-- UNIQUE(contract_id) : un seul envoi par contrat payé, jamais de relance
-- automatique en double si le script tourne plusieurs fois sur la même
-- fenêtre (voir la requête du cron, bornée à J-6/J-8 après paid_at).

CREATE TABLE IF NOT EXISTS satisfaction_enquetes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    contract_id UUID NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    envoyee_le TIMESTAMPTZ NOT NULL DEFAULT now(),
    note INTEGER CHECK (note BETWEEN 0 AND 10),
    commentaire TEXT,
    repondu_le TIMESTAMPTZ,
    UNIQUE (contract_id)
);

CREATE INDEX IF NOT EXISTS idx_satisfaction_enquetes_lead ON satisfaction_enquetes (lead_id);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated.
ALTER TABLE satisfaction_enquetes ENABLE ROW LEVEL SECURITY;
