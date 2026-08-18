-- Vérification "papiers professionnels" des artisans clients (SIRET +
-- attestation d'assurance décennale) — objectif : afficher un badge
-- "Vérifié" côté site public comme argument de confiance pour les
-- particuliers (voir dashboard/pages_publiques.py::badge_verification_pro,
-- verification_pro.py, dashboard/app_pages/administration_contrats.py).
--
-- Sur `leads` (pas une table dédiée) : statut_verification_pro et les
-- champs déclaratifs ci-dessous concernent l'ARTISAN lui-même — un état
-- courant (1 ligne = 1 artisan), même cardinalité que `status`/`contacted`
-- déjà présents sur cette table, pas un historique de vérifications.
--
-- siret_declare/assurance_decennale_declaree sont collectés au moment de
-- l'intake (dashboard/pages_publiques.py::afficher_intake — "le processus
-- qu'un artisan suit pour devenir client payant") mais persistés ici,
-- jamais dans intake_responses : cette dernière n'est qu'un historique de
-- soumissions (potentiellement plusieurs lignes par lead_id, voir
-- _get_intake qui ne lit que la plus récente), alors que le statut de
-- vérification doit rester attaché à l'artisan lui-même, un seul état
-- courant, jamais dupliqué ni perdu en cas de resoumission.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS statut_verification_pro TEXT DEFAULT 'non_verifie'
    CHECK (statut_verification_pro IN ('non_verifie', 'en_attente', 'verifie', 'refuse'));

-- Date de la DÉCISION de vérification (positive 'verifie' ou négative
-- 'refuse') prise MANUELLEMENT par un admin après contrôle du justificatif
-- d'assurance (voir maj_statut_verification_pro dans dashboard/data_access.py)
-- — pas la date du contrôle automatique du SIRET (déjà distincte,
-- implicite : siret_verifie_sirene est renseigné dès la soumission de
-- l'intake).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS date_verification TIMESTAMPTZ;

-- Numéro SIRET (14 chiffres, établissement) déclaré par l'artisan à
-- l'intake — distinct de leads.siren (9 chiffres, entreprise, déjà présent
-- depuis sql/init.sql, alimenté automatiquement par le scraping SIRENE en
-- amont de la prospection, jamais saisi par l'artisan lui-même).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS siret_declare TEXT;

-- Engagement PUREMENT DÉCLARATIF (case cochée par l'artisan), sans
-- vérification automatique de document scanné — décision assumée à ce
-- stade (trop complexe). Seule la vérification manuelle du justificatif par
-- un admin (statut_verification_pro='verifie') fait foi pour le badge public.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS assurance_decennale_declaree BOOLEAN DEFAULT FALSE;

-- Résultat du contrôle AUTOMATIQUE via l'API Recherche d'Entreprises
-- (SIRENE, voir verification_pro.py::verifier_siret_sirene), exécuté au
-- moment de la soumission de l'intake. NULL tant qu'aucun contrôle n'a pu
-- être tenté (SIRET absent), TRUE/FALSE ensuite. Ne fait PAS foi à lui seul
-- pour passer statut_verification_pro à 'verifie' (ça reste une décision
-- manuelle admin, après contrôle de l'assurance) — sert de
-- pré-qualification affichée dans la vue admin de vérification.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS siret_verifie_sirene BOOLEAN;

-- Raison sociale renvoyée par SIRENE pour ce SIRET, pour que l'admin
-- puisse comparer visuellement au nom d'entreprise déclaré par l'artisan
-- (`company`) avant de valider manuellement.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS siret_raison_sociale_sirene TEXT;
