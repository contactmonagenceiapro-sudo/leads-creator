-- Espace Artisans — auto-inscription / connexion via Supabase Auth
-- (landing/artisan-inscription.html, artisan-connexion.html,
-- artisan-tableau-de-bord.html).
--
-- À exécuter une fois dans l'éditeur SQL Supabase (même méthode que les
-- autres fichiers sql/init_*.sql de ce dépôt — aucun outil de migration
-- automatisé ici).
--
-- Distinct de utilisateurs_dashboard (sql/init_portail_client.sql, auth
-- JWT maison de dashboard/) : ceci concerne les ARTISANS eux-mêmes
-- (prospects/clients du site vitrine "Done For You"), pas les clients B2B
-- de l'agence ni l'admin. Authentification gérée entièrement par Supabase
-- Auth (auth.users) ; cette table ne stocke QUE les infos de profil
-- complémentaires, jamais de mot de passe.

CREATE TABLE IF NOT EXISTS artisans (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    nom_entreprise TEXT NOT NULL,
    nom_complet TEXT NOT NULL,
    siret TEXT,
    corps_metier TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE artisans ENABLE ROW LEVEL SECURITY;

-- Un artisan ne peut lire que sa propre ligne. Pas de politique
-- INSERT/UPDATE côté client : la ligne est créée uniquement par le trigger
-- ci-dessous (SECURITY DEFINER, contourne RLS) au moment de l'inscription —
-- un artisan ne peut donc jamais créer/modifier un profil pour un autre id
-- que le sien, ni forger sa propre ligne sans passer par auth.signUp().
DROP POLICY IF EXISTS "artisans_select_own" ON artisans;
CREATE POLICY "artisans_select_own"
    ON artisans FOR SELECT
    USING (auth.uid() = id);

-- Alimente automatiquement `artisans` à chaque inscription Supabase Auth,
-- à partir des métadonnées passées par supabase.auth.signUp({ options:
-- { data: {...} } }) côté landing/artisan-inscription.html. Tourne en
-- SECURITY DEFINER (contourne RLS) car au moment de l'INSERT dans
-- auth.users, la session cliente n'est pas forcément encore active
-- (confirmation email éventuellement requise selon la config du projet) —
-- on ne peut donc pas compter sur une politique RLS classique ici.
--
-- Garde défensive : n'insère que si nom_entreprise est présent dans les
-- métadonnées, pour ne rien casser si un autre flux d'inscription Supabase
-- Auth (hors artisans) est ajouté plus tard sans ce champ.
CREATE OR REPLACE FUNCTION public.handle_new_artisan()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public
AS $$
BEGIN
    IF NEW.raw_user_meta_data ->> 'nom_entreprise' IS NOT NULL THEN
        INSERT INTO public.artisans (id, email, nom_entreprise, nom_complet, siret, corps_metier)
        VALUES (
            NEW.id,
            NEW.email,
            NEW.raw_user_meta_data ->> 'nom_entreprise',
            NEW.raw_user_meta_data ->> 'nom_complet',
            NULLIF(NEW.raw_user_meta_data ->> 'siret', ''),
            NULLIF(NEW.raw_user_meta_data ->> 'corps_metier', '')
        )
        ON CONFLICT (id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created_artisan ON auth.users;
CREATE TRIGGER on_auth_user_created_artisan
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_artisan();

CREATE INDEX IF NOT EXISTS idx_artisans_email ON artisans (email);
