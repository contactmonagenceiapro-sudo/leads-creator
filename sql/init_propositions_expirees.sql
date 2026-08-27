-- Module 9 (pilotage) — performance des artisans. Journalise chaque
-- proposition à l'unité qui expire SANS action de l'artisan (ni paiement,
-- ni refus explicite — voir livraison_devis.py::expirer_propositions_perimees,
-- DELAI_EXPIRATION_PROPOSITION_HEURES, 48h par défaut).
--
-- Nécessaire car demandes_devis_particuliers.lead_id_livraison est ÉCRASÉ à
-- chaque ré-attribution (expirer_propositions_perimees remet la demande à
-- 'a_qualifier' et efface lead_id_livraison avant de retenter un autre
-- artisan) : sans cette table, l'information "quel artisan a laissé passer
-- cette proposition" est calculée puis jetée à chaque run, aucune mesure de
-- non-réactivité par artisan n'est possible dans la durée.
--
-- Une ligne par expiration (pas par artisan) : un même artisan peut laisser
-- expirer plusieurs propositions dans le temps, on garde l'historique
-- complet plutôt qu'un compteur agrégé, pour permettre un futur filtre par
-- période dans le dashboard.

CREATE TABLE IF NOT EXISTS propositions_expirees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    demande_id UUID NOT NULL REFERENCES demandes_devis_particuliers(id) ON DELETE CASCADE,
    artisan_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    corps_metier TEXT,
    commune TEXT,
    montant_centimes INTEGER,
    proposee_le TIMESTAMPTZ,
    expire_le TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_propositions_expirees_artisan ON propositions_expirees (artisan_id);
CREATE INDEX IF NOT EXISTS idx_propositions_expirees_demande ON propositions_expirees (demande_id);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated.
ALTER TABLE propositions_expirees ENABLE ROW LEVEL SECURITY;
