-- Canal INBOUND conforme pour les particuliers (maîtres d'ouvrage privés) :
-- le formulaire est rempli à leur initiative (consentement explicite),
-- jamais à partir d'une donnée collectée sans leur accord (permis de
-- construire, annuaire...). Voir outbound_chantiers/ pour le pipeline B2B
-- séparé (architectes/promoteurs/maîtres d'œuvre), qui lui NE contacte
-- jamais de particulier directement.

create table if not exists demandes_devis_particuliers (
    id uuid primary key default gen_random_uuid(),
    client_final text not null,          -- ex: 'S.B.G Travaux'
    nom text not null,
    email text,
    telephone text,
    type_projet text,                    -- construction / renovation_lourde / extension / autre
    commune text,
    budget_estime text,
    message text,
    consentement boolean not null default true,
    statut text default 'a_qualifier',
    created_at timestamptz default now()
);

create index if not exists idx_devis_particuliers_client on demandes_devis_particuliers (client_final);
