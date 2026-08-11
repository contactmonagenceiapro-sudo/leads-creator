-- Registre des suppressions RGPD (droit à l'effacement, art. 17 RGPD) —
-- trace CHAQUE suppression effectuée depuis l'outil dashboard dédié (voir
-- dashboard/app_pages/suppression_rgpd.py / dashboard/data_access.py::
-- supprimer_donnees_rgpd), y compris quand AUCUNE ligne n'a été trouvée
-- dans leads/leads_professionnels au moment de la demande (email déjà
-- supprimé, ou jamais présent en base) : la demande doit être tracée et
-- traçable comme traitée dans tous les cas, pas seulement quand une
-- suppression réelle a eu lieu.
--
-- email_hash (SHA256 hex de l'email normalisé strip+lower), PAS l'email en
-- clair : ce registre existe pour PROUVER qu'une suppression a eu lieu
-- (obligation de traçabilité), pas pour reconstituer une liste de
-- contacts — conserver l'email en clair dans un "registre de
-- suppressions" recréerait la donnée personnelle qu'on vient précisément
-- d'effacer ailleurs. Le hash reste directement exploitable pour vérifier
-- "cette personne a-t-elle déjà été traitée ?" : même email -> même hash
-- (déterministe, sans salage), il suffit de hasher l'email fourni et de
-- comparer.
--
-- table_source résume dans QUELLE(S) table(s) une suppression a réellement
-- eu lieu ('aucune' si rien trouvé) — un même email pouvant légitimement
-- exister à la fois côté B2C (leads) et côté B2B (leads_professionnels).

CREATE TABLE IF NOT EXISTS registre_suppressions_rgpd (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    email_hash TEXT NOT NULL,
    table_source TEXT NOT NULL
        CHECK (table_source IN ('leads', 'leads_professionnels', 'leads+leads_professionnels', 'aucune')),
    date_demande TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    date_traitement TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    traite_par TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registre_suppressions_email_hash ON registre_suppressions_rgpd (email_hash);
CREATE INDEX IF NOT EXISTS idx_registre_suppressions_date ON registre_suppressions_rgpd (date_traitement DESC);
