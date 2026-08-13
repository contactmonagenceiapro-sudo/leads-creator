-- Fait évoluer la tarification B2C (vente de leads aux artisans) sur deux
-- axes — voir generation_contrats.py::PRIX_PAR_PALIER_EUR / FORMULES_ABONNEMENT :
--   1. à l'unité : prix variable selon le score (palier Premium/Standard/
--      Basique) du lead réellement livré — connu seulement à la livraison,
--      jamais figé dans intake_responses/contracts.
--   2. abonnement : 3 formules de volume (petit/moyen/grand), à choisir à
--      l'intake — formule_abonnement mémorise LAQUELLE a été choisie.
--
-- contracts.formule_abonnement est nécessaire en plus de
-- intake_responses.formule_abonnement : creer_et_envoyer_lien_paiement()
-- (dashboard/contrats_signature.py) n'a accès qu'à la ligne `contracts` au
-- moment de facturer, jamais à intake_responses directement.
--
-- contracts.montant_centimes reste NULLABLE (déjà le cas, sql/init.sql) :
-- pour une formule "à l'unité", il reste volontairement NULL entre la
-- signature et la première livraison facturée (prix pas encore connu).

ALTER TABLE intake_responses ADD COLUMN IF NOT EXISTS formule_abonnement TEXT
    CHECK (formule_abonnement IN ('petit', 'moyen', 'grand'));

ALTER TABLE contracts ADD COLUMN IF NOT EXISTS formule_abonnement TEXT
    CHECK (formule_abonnement IN ('petit', 'moyen', 'grand'));
