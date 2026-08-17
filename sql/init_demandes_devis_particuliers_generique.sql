-- Généralise demandes_devis_particuliers (sql/init_demandes_devis_particuliers.sql)
-- d'un formulaire scopé à UNE campagne B2B nommée (/devis/{slug}, client_final
-- obligatoire dès la soumission) vers un formulaire public générique
-- (/demande-devis, sans client destinataire connu à la soumission) — voir
-- diagnostic exploratoire du 18/08/2026 : la table est encore vide (0 ligne)
-- en production, aucune donnée à migrer.
--
-- Deux changements de schéma :
--
-- 1. client_final devient NULLABLE : une soumission générique ne connaît
--    encore aucun artisan/client destinataire au moment où le particulier
--    la remplit (c'est justement le mécanisme de mise en relation, à
--    construire dans une étape ultérieure distincte, qui décidera à qui la
--    transmettre). L'ancien formulaire /devis/{slug} continue de fonctionner
--    à l'identique et continue de renseigner client_final explicitement —
--    ce n'est donc PAS un remplacement de ce flux, seulement un
--    assouplissement pour permettre le nouveau.
--
-- 2. type_projet (texte libre, jamais vraiment exploité en pratique : le
--    formulaire existant est un st.text_input libre malgré le commentaire
--    d'origine qui suggérait une liste fermée) est remplacé par corps_metier
--    (valeur contrôlée, alignée sur scraper_batiment.py::SECTEURS_NAF —
--    mêmes 6 libellés, dupliqués en dur côté Python dans
--    dashboard/pages_publiques.py plutôt qu'importés, même principe que
--    scorer_leads.py::SCORES_TRANCHE_EFFECTIF : les segments commerciaux ne
--    doivent jamais dépendre l'un de l'autre au niveau code). C'est ce champ
--    qui permettra, à l'étape suivante, de rapprocher une demande du corps
--    de métier d'un artisan client — un texte libre non normalisé rendrait
--    ce rapprochement peu fiable. La nature du projet (construction neuve
--    vs rénovation) reste capturable en texte libre dans le champ `message`
--    existant (description du besoin), qui ne change pas.
--
-- RLS : déjà actif sur cette table, confirmé par test réel à la clé anon
-- publique lors de l'audit du 18/08/2026 (voir sql/fix_rls_error_log.sql
-- pour le contexte de cet audit) — AUCUNE des deux modifications ci-dessus
-- ne désactive ni ne réinitialise le RLS (ajouter/retirer une colonne ou
-- assouplir une contrainte NOT NULL n'a aucun effet sur relrowsecurity).
-- Le ENABLE ROW LEVEL SECURITY ci-dessous est donc un no-op idempotent,
-- gardé pour documenter explicitement l'intention plutôt que de la supposer
-- silencieusement (même doctrine que les migrations RLS précédentes de ce
-- dépôt) : le formulaire public écrit toujours via le client Supabase
-- service_role partagé (dashboard/supabase_client.py), jamais via une clé
-- anon côté navigateur — aucune politique dédiée à "anon" n'est donc
-- nécessaire, une politique en ajouterait une sans changer le comportement
-- réel (même raisonnement que sql/fix_rls_leads_kpis.sql).

ALTER TABLE demandes_devis_particuliers ALTER COLUMN client_final DROP NOT NULL;

ALTER TABLE demandes_devis_particuliers ADD COLUMN IF NOT EXISTS corps_metier TEXT
    CHECK (corps_metier IN (
        'Bâtiment - Plâtrerie',
        'Bâtiment - Électricité',
        'Bâtiment - Isolation / Rénovation Énergétique',
        'Bâtiment - Gros Œuvre',
        'Bâtiment - Second Œuvre / Rénovation',
        'Bâtiment - Plomberie / Chauffage'
    ));

ALTER TABLE demandes_devis_particuliers DROP COLUMN IF EXISTS type_projet;

ALTER TABLE demandes_devis_particuliers ENABLE ROW LEVEL SECURITY;
