-- Portail Client : authentification + contrôle d'accès par campagne.
--
-- Deux rôles :
-- - admin : accès total (vue actuelle du dashboard, inchangée).
-- - client : accès restreint en lecture aux campagnes qui lui sont
--   explicitement liées (table utilisateur_campagnes), + une seule action
--   d'écriture autorisée (signaler un lead invalide, voir api/main.py).
--
-- Les mots de passe ne sont JAMAIS stockés en clair : mot_de_passe_hash
-- contient un hash bcrypt, généré par creer_utilisateur_dashboard.py.

CREATE TABLE IF NOT EXISTS utilisateurs_dashboard (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    mot_de_passe_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'client' CHECK (role IN ('admin', 'client')),
    actif BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Liaison many-to-many : un compte client peut être rattaché à plusieurs
-- campagnes (ex: un même client avec deux secteurs différents) ; client_final
-- reprend le nom exact tel qu'utilisé dans campagnes.nom_client / leads_professionnels.client_final.
CREATE TABLE IF NOT EXISTS utilisateur_campagnes (
    utilisateur_id UUID NOT NULL REFERENCES utilisateurs_dashboard(id) ON DELETE CASCADE,
    client_final TEXT NOT NULL,
    PRIMARY KEY (utilisateur_id, client_final)
);

CREATE INDEX IF NOT EXISTS idx_utilisateurs_email ON utilisateurs_dashboard (email);
CREATE INDEX IF NOT EXISTS idx_utilisateur_campagnes_client ON utilisateur_campagnes (client_final);
