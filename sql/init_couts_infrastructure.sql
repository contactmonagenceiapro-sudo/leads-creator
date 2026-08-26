-- Module 3 (pilotage) — coûts d'infrastructure, remplis manuellement (pas
-- d'API de facturation branchée dans un premier temps) : Supabase,
-- Streamlit Cloud, Zoho, futur nom de domaine, frais Stripe...
--
-- pourcentage_du_ca (en plus de cout_mensuel_centimes, seul demandé au
-- départ) : les frais Stripe ne sont PAS un montant fixe mensuel mais un
-- pourcentage du CA réellement encaissé (précisé dans la demande d'origine
-- : "frais Stripe (pourcentage, à calculer depuis le CA réel du module
-- 1)") — extension minimale du schéma pour représenter ce cas sans détourner
-- cout_mensuel_centimes de son sens. Exactement une des deux colonnes doit
-- être renseignée par ligne (contrainte ci-dessous) : soit un montant fixe,
-- soit un pourcentage, jamais les deux ni aucun des deux.
--
-- date_fin nullable = coût encore actif ; renseignée = coût terminé
-- (changement de plan, service abandonné) sans supprimer l'historique —
-- permet de reconstituer le coût réel mois par mois même après un
-- changement, plutôt qu'un simple montant "actuel" qui écraserait le passé.

CREATE TABLE IF NOT EXISTS couts_infrastructure (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service TEXT NOT NULL,
    cout_mensuel_centimes INTEGER,
    pourcentage_du_ca NUMERIC(5,2),
    date_debut DATE NOT NULL DEFAULT CURRENT_DATE,
    date_fin DATE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT couts_infrastructure_exactement_un_type CHECK (
        (cout_mensuel_centimes IS NOT NULL AND pourcentage_du_ca IS NULL) OR
        (cout_mensuel_centimes IS NULL AND pourcentage_du_ca IS NOT NULL)
    ),
    CONSTRAINT couts_infrastructure_dates_coherentes CHECK (date_fin IS NULL OR date_fin >= date_debut)
);

CREATE INDEX IF NOT EXISTS idx_couts_infrastructure_actifs ON couts_infrastructure (date_fin) WHERE date_fin IS NULL;

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated.
ALTER TABLE couts_infrastructure ENABLE ROW LEVEL SECURITY;
