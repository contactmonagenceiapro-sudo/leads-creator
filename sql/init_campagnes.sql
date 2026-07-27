-- Table centrale de configuration multi-clients / multi-secteurs pour le
-- département "Outbound & Génération de Chantiers" (devenu générique).
--
-- Chaque ligne = un client/campagne à part entière : plus aucun script
-- (sourcing_acteurs_pro.py, enrichir_acteurs_pro.py, scorer_et_publier.py,
-- outbound_pro_btp.py) ne code en dur de secteur, de zone géographique ou
-- de type d'acteur — tout est lu dynamiquement d'ici.
--
-- types_acteur_cibles : tableau de {"cle","libelle","codes_naf","poids"} —
-- ex: [{"cle":"architecte","libelle":"Architectes","codes_naf":["71.11Z"],"poids":1.0}]
-- Les codes NAF doivent être au format POINTÉ ("71.11Z"), exigé par
-- recherche-entreprises.api.gouv.fr (voir incident documenté dans
-- outbound_chantiers/config.py).

create table if not exists campagnes (
    id uuid primary key default gen_random_uuid(),
    nom_client text not null unique,
    slug text not null unique,              -- utilisé par /devis/{slug}, dérivé de nom_client
    secteur text not null,
    description_services text,              -- utilisé pour personnaliser le pitch IA (module 4)
    communes_cibles jsonb not null default '[]',
    types_acteur_cibles jsonb not null default '[]',
    statut text not null default 'active' check (statut in ('active', 'en_pause', 'archivee')),
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_campagnes_statut on campagnes (statut);

-- Migration de la configuration S.B.G Travaux existante (précédemment en
-- dur dans outbound_chantiers/config.py) pour ne rien perdre du travail déjà en cours.
insert into campagnes (nom_client, slug, secteur, description_services, communes_cibles, types_acteur_cibles, statut)
values (
    'S.B.G Travaux',
    'sbg-travaux',
    'Bâtiment - Gros Œuvre',
    'Entreprise de gros œuvre : construction, rénovation lourde, extension. Intervient comme exécutant sur les chantiers apportés par des prescripteurs (architectes, promoteurs, maîtres d''œuvre).',
    '["Lyon 1er","Lyon 2e","Lyon 3e","Lyon 4e","Lyon 5e","Lyon 6e","Lyon 7e","Lyon 8e","Lyon 9e","Villeurbanne","Vénissieux","Saint-Priest","Bron","Vaulx-en-Velin","Caluire-et-Cuire","Oullins","Rillieux-la-Pape","Écully"]'::jsonb,
    '[
        {"cle":"architecte","libelle":"Architectes","codes_naf":["71.11Z"],"poids":1.0},
        {"cle":"maitre_oeuvre","libelle":"Maîtres d''œuvre","codes_naf":["71.12B"],"poids":0.7},
        {"cle":"promoteur","libelle":"Promoteurs immobiliers","codes_naf":["41.10A","41.10C","41.10D"],"poids":0.9}
    ]'::jsonb,
    'active'
)
on conflict (nom_client) do nothing;
