-- Module 10 (pilotage) — journal d'audit des actions admin sensibles.
-- Branché directement dans les fonctions existantes qui font déjà ces
-- actions (dashboard/data_access.py::traiter_reclamation,
-- maj_statut_verification_pro, marquer_contrat_signe, marquer_contrat_paye,
-- executer_remboursement) plutôt qu'un système de log séparé — voir
-- dashboard/data_access.py::journaliser_action_admin.
--
-- utilisateur_email dénormalisé (en plus de utilisateur_id) : reste lisible
-- même si le compte utilisateurs_dashboard est supprimé plus tard
-- (ON DELETE SET NULL sur utilisateur_id) — traçabilité avant tout, un
-- historique d'audit qui perd l'identité de l'auteur en cas de suppression
-- de compte n'aurait plus grand intérêt.
--
-- cible_id est UUID nullable et volontairement SANS contrainte FK (comme
-- reclamations.lead_id, voir sql/init_reclamations.sql) : cible_type varie
-- selon l'action (reclamation/lead/contract/remboursement), une seule FK
-- vers une seule table n'est pas possible ici.

CREATE TABLE IF NOT EXISTS journal_audit_admin (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    utilisateur_id UUID REFERENCES utilisateurs_dashboard(id) ON DELETE SET NULL,
    utilisateur_email TEXT,
    action TEXT NOT NULL,
    cible_type TEXT NOT NULL,
    cible_id UUID,
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_journal_audit_utilisateur ON journal_audit_admin (utilisateur_id);
CREATE INDEX IF NOT EXISTS idx_journal_audit_action ON journal_audit_admin (action);
CREATE INDEX IF NOT EXISTS idx_journal_audit_cible ON journal_audit_admin (cible_type, cible_id);
CREATE INDEX IF NOT EXISTS idx_journal_audit_date ON journal_audit_admin (created_at DESC);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated. Particulièrement
-- important ici : un journal d'audit lisible publiquement révélerait qui
-- traite quoi, quand, avec quel détail.
ALTER TABLE journal_audit_admin ENABLE ROW LEVEL SECURITY;
