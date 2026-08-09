-- Ajoute le statut 'necessite_contact_manuel' au CHECK existant sur
-- leads_professionnels.statut (sql/init_leads_professionnels.sql, étendu
-- une première fois par sql/init_bounces.sql pour 'invalide').
--
-- Sert à sortir proprement de la file d'envoi automatique les acteurs dont
-- l'email est structurellement introuvable (email_status='email_introuvable',
-- voir sql/init_email_introuvable.sql) : sans statut dédié, ces acteurs
-- restaient indéfiniment en 'a_contacter', re-sélectionnés à chaque run par
-- outbound_chantiers/outbound_pro_btp.py::lancer_campagne_initiale() puis
-- silencieusement ignorés faute d'email (voir
-- scorer_et_publier.py::publier_en_base) — jamais résolus, jamais visibles
-- comme nécessitant une action différente. Restent filtrables/visibles dans
-- le dashboard (filtre "statut" déjà générique, aucun code dashboard
-- supplémentaire nécessaire) pour un contact manuel (téléphone,
-- quasi systématiquement disponible pour ce cas précis).
--
-- Même méthode que sql/init_bounces.sql / sql/init_email_introuvable.sql :
-- recherche dynamique du nom de la contrainte CHECK existante plutôt que de
-- deviner un nom fixe.
DO $$
DECLARE
    nom_contrainte TEXT;
BEGIN
    SELECT conname INTO nom_contrainte
    FROM pg_constraint
    WHERE conrelid = 'leads_professionnels'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%statut%'
      AND pg_get_constraintdef(oid) ILIKE '%a_contacter%';

    IF nom_contrainte IS NOT NULL THEN
        EXECUTE format('ALTER TABLE leads_professionnels DROP CONSTRAINT %I', nom_contrainte);
    END IF;
END $$;

-- Filet de sécurité (script rejouable), même raison que sql/init_bounces.sql.
ALTER TABLE leads_professionnels
    DROP CONSTRAINT IF EXISTS leads_professionnels_statut_check;

ALTER TABLE leads_professionnels
    ADD CONSTRAINT leads_professionnels_statut_check
    CHECK (statut IN (
        'a_contacter', 'contacte_attente_reponse', 'interested', 'decline',
        'sans_reponse', 'invalide', 'necessite_contact_manuel'
    ));
