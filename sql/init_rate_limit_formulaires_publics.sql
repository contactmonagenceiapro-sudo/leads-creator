-- Rate-limit par IP sur les formulaires publics sans authentification
-- (dashboard/pages_publiques.py::afficher_devis, afficher_demande_devis,
-- afficher_devenir_client) — avant cette table, aucune limite de fréquence
-- n'existait : chaque soumission déclenche une écriture DB + une alerte
-- Discord + (pour 2 des 3) un envoi SMTP réel vers l'e-mail saisi, et
-- afficher_devenir_client enchaîne en plus sur afficher_intake (appel API
-- SIRENE + génération PDF + signature électronique) — voir
-- audit/audit_verification_2026-09-08.md, constat M5.
--
-- Table Postgres plutôt qu'un compteur en mémoire du process Streamlit :
-- Streamlit Community Cloud peut redémarrer/s'endormir à tout moment (déjà
-- documenté comme non fiable dans ce projet pour les crons, voir
-- guide_commandes_complet.md) — un compteur en mémoire serait remis à zéro
-- à chaque redémarrage, protection illusoire contre un abus soutenu.
--
-- Fenêtre FIXE (pas glissante) : une soumission à 14h09 et une autre à
-- 14h11 avec une fenêtre de 10 min tombent dans deux tranches différentes
-- ([14h00-14h10[ et [14h10-14h20[) — approximation volontaire, suffisante
-- pour un anti-abus/anti-spam best-effort (pas une garantie de facturation
-- ou de sécurité stricte, voir data_access.py::verifier_rate_limit_public).
--
-- Nettoyage : aucune purge automatique des vieilles lignes pour l'instant
-- (volume attendu faible — un visiteur public normal ne soumet qu'une
-- poignée de fois). À revoir si le volume de formulaires publics augmente
-- significativement (voir scripts/controle_sante_bdd.py pour un futur
-- contrôle de croissance anormale de cette table, même principe que les
-- autres tables déjà surveillées).

CREATE TABLE IF NOT EXISTS rate_limit_formulaires_publics (
    ip TEXT NOT NULL,
    formulaire TEXT NOT NULL,
    fenetre_debut TIMESTAMPTZ NOT NULL,
    nb_appels INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (ip, formulaire, fenetre_debut)
);

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement (dashboard, via data_access.py), aucune policy
-- anon/authenticated — ces formulaires publics passent par le backend
-- Streamlit (clé service_role), jamais par un appel Supabase direct
-- depuis le navigateur du visiteur.
ALTER TABLE rate_limit_formulaires_publics ENABLE ROW LEVEL SECURITY;
