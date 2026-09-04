-- Ajoute le statut 'en_cours' au CHECK existant sur remboursements.statut
-- (sql/init_remboursements.sql) — trouvé par audit le 04/09/2026 :
-- executer_remboursement() (dashboard/data_access.py) lisait statut != 'valide'
-- AVANT d'appeler stripe.Refund.create(), sans qu'aucune écriture atomique
-- ne "réserve" la ligne entre la lecture et l'appel Stripe. Un double-clic
-- sur "💸 Exécuter", ou deux onglets ouverts sur le même remboursement,
-- pouvait déclencher DEUX remboursements Stripe réels pour le même
-- remboursement_id (check-then-act classique — même famille de bug que
-- livraison_devis.py::_quota_disponible, corrigé plus tôt cette session,
-- mais ici avec un vrai mouvement d'argent en jeu).
--
-- 'en_cours' sert de verrou : executer_remboursement() fait désormais un
-- UPDATE conditionnel (WHERE statut = 'valide') vers 'en_cours' AVANT
-- d'appeler Stripe — Postgres sérialise les UPDATE concurrents au niveau
-- de la ligne, donc un seul appelant peut voir cette transition réussir
-- même en cas de double déclenchement simultané. Le second échoue
-- proprement (0 ligne affectée) plutôt que de rappeler Stripe.
--
-- Recherche dynamique du nom de la contrainte (auto-généré par Postgres),
-- + DROP CONSTRAINT IF EXISTS explicite en filet de sécurité si ce script
-- est rejoué après un échec partiel (même approche que
-- sql/init_campagnes_brouillon.sql).
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'remboursements'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%statut%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE remboursements DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

ALTER TABLE remboursements
    DROP CONSTRAINT IF EXISTS remboursements_statut_check;

ALTER TABLE remboursements
    ADD CONSTRAINT remboursements_statut_check
    CHECK (statut IN ('en_attente', 'valide', 'en_cours', 'rembourse', 'echoue', 'rejete'));
