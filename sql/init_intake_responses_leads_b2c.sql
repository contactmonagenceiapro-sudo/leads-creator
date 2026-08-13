-- Le tunnel B2C vendait par erreur une prestation "site vitrine + SEO local"
-- (voir intake_responses.lien_photos/lien_site_actuel/lien_gbp/telephone_public,
-- sql/init.sql) alors que l'activité réelle est la vente de demandes de
-- devis qualifiées ("leads") aux artisans — diagnostic du 2026-08-14 :
-- lead_worker.py::generer_pitch() promettait déjà ce modèle ("nous leur
-- envoyons régulièrement des demandes de devis... dans leur zone
-- d'activité") sans que le reste du tunnel (offre publique, intake, devis,
-- CGV, Stripe) ne soit jamais aligné dessus.
--
-- corps_metier / type_offre : nouveaux champs nécessaires à la vente de
-- leads (voir dashboard/pages_publiques.py::afficher_intake). zone_activite
-- (déjà présent) est réutilisé tel quel, toujours pertinent pour qualifier
-- la zone d'intervention du Client.
--
-- lien_photos / lien_site_actuel / lien_gbp / telephone_public : supprimés,
-- propres à l'ancien modèle "création de site" (contenu à fournir pour la
-- production d'un site vitrine), sans aucun usage dans le nouveau tunnel.
-- Aucune perte réelle : au moment de cette migration, intake_responses ne
-- contient qu'une seule ligne de test end-to-end (2026-07-26), zéro donnée
-- client réelle.

ALTER TABLE intake_responses ADD COLUMN IF NOT EXISTS corps_metier TEXT;
ALTER TABLE intake_responses ADD COLUMN IF NOT EXISTS type_offre TEXT
    CHECK (type_offre IN ('unite', 'abonnement'));

ALTER TABLE intake_responses DROP COLUMN IF EXISTS lien_photos;
ALTER TABLE intake_responses DROP COLUMN IF EXISTS lien_site_actuel;
ALTER TABLE intake_responses DROP COLUMN IF EXISTS lien_gbp;
ALTER TABLE intake_responses DROP COLUMN IF EXISTS telephone_public;

-- contracts.type_offre : mémorise le type d'offre choisi au moment de la
-- signature. Nécessaire pour que creer_et_envoyer_lien_paiement() (qui n'a
-- accès qu'à la ligne `contracts`, jamais à intake_responses) sache créer
-- un prix Stripe ponctuel (à l'unité) ou récurrent mensuel (abonnement).
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS type_offre TEXT
    CHECK (type_offre IN ('unite', 'abonnement'));
