-- Réclamations clients (B2C + B2B), fondées sur l'Article 4 des CGV
-- (construire_articles_cgv_b2c / équivalent B2B) : 5 motifs objectifs
-- d'invalidité d'un lead. Délai de réclamation contractuel : 7 jours à
-- compter de la livraison. L'absence de réponse du prospect est
-- explicitement exclue comme motif valide par les CGV — elle n'apparaît
-- donc PAS dans la contrainte `motif` ci-dessous : un client ne peut
-- techniquement pas la sélectionner.
--
-- `lead_id` est polymorphe (pas de FK unique possible vers deux tables
-- différentes selon type_lead) — vérification d'existence/d'appartenance
-- faite côté application (voir dashboard/data_access.py::creer_reclamation),
-- même doctrine que demandes_devis_particuliers.lead_id_livraison pour la
-- partie qui, elle, EST à FK simple :
--
-- - type_lead='b2c' -> lead_id référence demandes_devis_particuliers.id —
--   c'est CE produit-là qui est livré à un artisan, PAS leads.id (qui est
--   la fiche de l'artisan destinataire lui-même, pas un produit revendu :
--   voir livraison_devis.py::_livrer_directement/_proposer,
--   demandes_devis_particuliers.livree_le/lead_id_livraison). client_lead_id
--   référence alors leads.id (l'artisan destinataire, copié depuis
--   demandes_devis_particuliers.lead_id_livraison au moment de la
--   réclamation) — c'est l'identifiant client utilisé pour le calcul du
--   taux de réclamation par client.
--
-- - type_lead='b2b' -> lead_id référence leads_professionnels.id (le
--   prospect livré au client_final). client_final (texte, même convention
--   que leads_professionnels.client_final / utilisateur_campagnes.client_final)
--   identifie alors le client — ce projet n'a pas de table `clients` B2B
--   dédiée avec un id UUID, l'identité client y est toujours une chaîne
--   (voir sql/init_portail_client.sql). leads_professionnels n'a pas de
--   colonne de date de livraison distincte (le rôle B2B n'a pas de
--   mécanisme de rapprochement différé comme livraison_devis.py) —
--   created_at y fait donc office de date de livraison, la ligne étant
--   déjà scopée à client_final dès sa création par le pipeline
--   outbound_pro_btp.
--
-- Coexiste avec le mécanisme B2B déjà en place (leads_professionnels.
-- signale_invalide / motif_invalidite + table remboursements, motif libre,
-- sans vérification de délai) — volontairement PAS remplacé ni migré ici :
-- cette nouvelle table est le point d'entrée formel/contractuel (délai
-- vérifié, motif contrôlé Article 4, décision accepter/refuser tracée). Le
-- lien vers un remboursement/avoir effectif reste une étape ultérieure
-- distincte, jamais couplée automatiquement (même principe que
-- demandes_devis_particuliers/remboursements aujourd'hui).

CREATE TABLE IF NOT EXISTS reclamations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type_lead TEXT NOT NULL CHECK (type_lead IN ('b2c', 'b2b')),
    lead_id UUID NOT NULL,
    client_lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    client_final TEXT,
    motif TEXT NOT NULL CHECK (motif IN (
        'email_invalide', 'telephone_errone', 'zone_non_conforme', 'type_non_conforme', 'doublon'
    )),
    description_libre TEXT,
    date_livraison_lead TIMESTAMPTZ NOT NULL,
    date_reclamation TIMESTAMPTZ NOT NULL DEFAULT now(),
    dans_les_delais BOOLEAN NOT NULL,
    statut TEXT NOT NULL DEFAULT 'en_attente' CHECK (statut IN ('en_attente', 'acceptee', 'refusee')),
    date_traitement TIMESTAMPTZ,
    traite_par TEXT,
    commentaire_traitement TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Exactement un identifiant client renseigné, cohérent avec type_lead —
    -- pas de FK possible ici (voir plus haut) mais au moins cette
    -- cohérence-là est vérifiable par une contrainte simple.
    CONSTRAINT reclamation_a_un_client CHECK (
        (type_lead = 'b2c' AND client_lead_id IS NOT NULL AND client_final IS NULL) OR
        (type_lead = 'b2b' AND client_final IS NOT NULL AND client_lead_id IS NULL)
    ),
    -- Filet de sécurité en base, en plus du contrôle applicatif (voir
    -- dashboard/data_access.py::traiter_reclamation) : impossible d'avoir
    -- statut='refusee' sans motif de refus tracé.
    CONSTRAINT reclamation_refus_trace CHECK (
        statut <> 'refusee' OR commentaire_traitement IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_reclamations_statut ON reclamations (statut);
CREATE INDEX IF NOT EXISTS idx_reclamations_lead ON reclamations (lead_id);
CREATE INDEX IF NOT EXISTS idx_reclamations_client_lead ON reclamations (client_lead_id);
CREATE INDEX IF NOT EXISTS idx_reclamations_client_final ON reclamations (client_final);
CREATE INDEX IF NOT EXISTS idx_reclamations_date ON reclamations (date_reclamation);

-- RLS : même doctrine que TOUTES les autres tables de ce projet (voir
-- sql/fix_rls_leads_kpis.sql, réaffirmé sur chaque nouvelle table depuis
-- l'audit du 2026-08-14) — le dashboard Streamlit (admin ET Portail
-- Client) n'accède JAMAIS à Supabase autrement que via le rôle
-- service_role (dashboard/supabase_client.py), qui contourne le RLS par
-- conception Supabase ; il n'y a pas de session "authenticated" Supabase
-- Auth dans ce flux (auth maison, table utilisateurs_dashboard). La règle
-- "un client ne peut insérer/lire que SES PROPRES réclamations" est donc
-- appliquée côté application (Python), pas via une politique Postgres —
-- voir dashboard/data_access.py::creer_reclamation (vérifie server-side que
-- le lead appartient bien au compte connecté avant toute écriture) et
-- dashboard/auth.py::mes_leads_autorises(). ENABLE ROW LEVEL SECURITY seul
-- (sans aucune policy) bloque déjà anon/authenticated par défaut ; ajouter
-- une politique ici n'aurait donc aucun effet réel et prêterait à
-- confusion (même raisonnement documenté dans sql/fix_rls_leads_kpis.sql)
-- — RLS activé immédiatement à la création de cette table, pas "plus tard".
ALTER TABLE reclamations ENABLE ROW LEVEL SECURITY;
