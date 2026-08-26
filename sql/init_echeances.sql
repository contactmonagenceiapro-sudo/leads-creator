-- Module 5 (pilotage) — échéances légales et administratives. Remplies
-- manuellement (statut légal définitif, renouvellement nom de domaine une
-- fois acheté, validité assurance décennale une fois qu'une date sera
-- disponible — aucune des trois n'a de date connue/fiable aujourd'hui,
-- volontairement AUCUNE ligne insérée par cette migration pour ne pas
-- fabriquer une échéance factice) + une échéance récurrente pour la
-- vérification manuelle de l'usage Supabase (fusion du module 11 : son
-- API de gestion — quotas stockage/requêtes — exige un token de gestion
-- distinct de SUPABASE_KEY, inaccessible depuis ce projet, voir l'échange
-- du 27/08/2026 ; vérification manuelle mensuelle plutôt qu'une
-- automatisation complexe pour peu de valeur).
--
-- recurrence_jours (nullable) : si renseigné, clôturer cette échéance
-- (dashboard/data_access.py::terminer_echeance) recrée automatiquement la
-- suivante decalée de N jours À PARTIR DE L'ÉCHÉANCE D'ORIGINE (pas de la
-- date de clôture, pour ne pas dériver au fil des mois si elle est traitée
-- en avance ou en retard) — évite de ré-ajouter à la main la même échéance
-- tous les mois pour un contrôle purement récurrent.

CREATE TABLE IF NOT EXISTS echeances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    date_echeance DATE NOT NULL,
    statut TEXT NOT NULL DEFAULT 'a_traiter' CHECK (statut IN ('a_traiter', 'traite')),
    notes TEXT,
    recurrence_jours INTEGER,
    date_traitement TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_echeances_statut_date ON echeances (statut, date_echeance);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated.
ALTER TABLE echeances ENABLE ROW LEVEL SECURITY;

-- Seule échéance connue avec une date fiable aujourd'hui (voir en-tête) —
-- première vérification dans 30 jours, puis tous les 30 jours
-- automatiquement (recurrence_jours). Comme sql/init.sql pour les KPIs
-- initiaux : cet INSERT n'est PAS ré-exécutable (pas de contrainte unique
-- sur type/description) — à n'exécuter qu'une seule fois, pas à chaque
-- run de ce fichier.
INSERT INTO echeances (type, description, date_echeance, recurrence_jours, notes)
VALUES (
    'infra',
    'Vérifier manuellement l''usage Supabase (stockage, requêtes) vs limites du plan actuel — App settings > Usage dans le dashboard Supabase.',
    CURRENT_DATE + 30,
    30,
    'Fusion module 11 (pilotage) : API de gestion Supabase inaccessible avec SUPABASE_KEY, vérification manuelle retenue plutôt qu''une automatisation complexe.'
);
