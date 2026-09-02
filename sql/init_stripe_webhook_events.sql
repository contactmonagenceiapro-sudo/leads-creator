-- File d'attente des événements webhook Stripe — remplace la confirmation
-- manuelle du paiement (dashboard/data_access.py::marquer_contrat_paye /
-- marquer_demande_devis_payee_et_livree, jusqu'ici cliquées à la main faute
-- de webhook, voir dashboard/contrats_signature.py pour l'historique).
--
-- Architecture en deux temps, pas un webhook synchrone classique :
-- Streamlit Community Cloud (où tourne le dashboard) ne peut pas recevoir
-- de requête HTTP entrante — c'est justement pour ça que l'ancien webhook
-- avait été retiré avec le backend FastAPI. Le vrai récepteur HTTPS est
-- une Supabase Edge Function (supabase/functions/stripe-webhook/), qui se
-- contente de vérifier la signature Stripe et d'insérer l'événement brut
-- ICI — AUCUNE logique métier côté Edge Function (pas de duplication de
-- marquer_contrat_paye/marquer_demande_devis_payee_et_livree en TypeScript).
-- Le traitement réel est fait par scripts/traiter_paiements_stripe.py
-- (cron GitHub Actions, voir .github/workflows/traiter_paiements_stripe.yml),
-- qui réutilise en Python la même logique que les boutons manuels existants
-- — gardés comme filet de secours si un événement est manqué.
--
-- stripe_event_id UNIQUE : idempotence face aux retries automatiques de
-- Stripe (même event.id renvoyé plusieurs fois tant que l'endpoint ne
-- répond pas 2xx) — un upsert ignore-conflict côté Edge Function évite les
-- doublons plutôt qu'un traitement métier appliqué deux fois.
--
-- payload JSONB : événement Stripe complet tel que reçu (après vérification
-- de signature) — évite d'avoir à redéfinir ici quelles colonnes extraire
-- au moment de l'insertion (fait plus tard, côté Python, à la lecture).

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_event_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    payload JSONB NOT NULL,
    statut TEXT NOT NULL DEFAULT 'recu'
        CHECK (statut IN ('recu', 'traite', 'echec', 'ignore')),
    erreur TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    traite_le TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_statut ON stripe_webhook_events (statut);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement (Edge Function ET script Python utilisent la clé
-- service_role), aucune policy anon/authenticated.
ALTER TABLE stripe_webhook_events ENABLE ROW LEVEL SECURITY;
