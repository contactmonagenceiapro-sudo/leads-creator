-- Extension additive du schéma existant (sql/init.sql) : table dédiée au
-- département "Outbound & Génération de Chantiers", volontairement séparée
-- de la table `leads` (artisans sans site, cœur de métier Expertise
-- Digitale) pour ne jamais mélanger deux segments commerciaux distincts.
--
-- Cible : acteurs PROFESSIONNELS (architectes, promoteurs, maîtres
-- d'œuvre). Aucune colonne de donnée personnelle de particulier ici — voir
-- outbound_chantiers/README.md pour la justification légale.

create table if not exists leads_professionnels (
    id uuid primary key default gen_random_uuid(),
    client_final text not null,                 -- ex: 'SBG Travaux' (le client de la campagne)
    type_acteur text not null check (type_acteur in ('architecte', 'promoteur', 'maitre_oeuvre')),
    nom_entreprise text not null,
    siren text,
    commune text,
    code_postal text,
    adresse text,
    site_web text,
    email text,
    telephone text,
    score_activite_chantiers numeric default 0.5,  -- signal agrégé module 1b (0 à 1)
    score_final numeric default 0,                 -- score pondéré module 2/3
    statut text default 'a_contacter'
        check (statut in ('a_contacter', 'contacte_attente_reponse', 'interested', 'decline', 'sans_reponse')),
    contacted boolean default false,
    contacted_at timestamptz,
    relance_count int default 0,
    last_relance_at timestamptz,
    notes text,
    created_at timestamptz default now(),
    unique (client_final, nom_entreprise)
);

create index if not exists idx_leads_pro_statut on leads_professionnels (statut);
create index if not exists idx_leads_pro_client on leads_professionnels (client_final);
