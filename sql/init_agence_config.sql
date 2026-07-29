-- Configuration de l'agence (nom, adresse, e-mail, statut SIRET), utilisée
-- pour pré-remplir les bons de commande générés depuis le dashboard
-- ("Administration & Contrats" — voir dashboard/app_pages/administration_contrats.py).
--
-- Remplace un fichier JSON local (dashboard/data/agence_config.json) qui ne
-- survivait pas à un redéploiement/redémarrage sur Streamlit Community
-- Cloud (filesystem éphémère) : la config saisie par l'admin disparaissait
-- silencieusement. Une seule ligne, identifiée par la clé fixe 'agence'
-- (upsert on_conflict=cle).

create table if not exists agence_config (
    cle text primary key default 'agence',
    nom text not null default 'Expertise Digitale',
    adresse text not null default '',
    email text not null default '',
    siret_statut text not null default 'en_cours' check (siret_statut in ('en_cours', 'definitif')),
    siret_numero text not null default '',
    updated_at timestamptz default now()
);
