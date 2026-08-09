-- Extension additive de `leads` : taille d'entreprise (code INSEE de tranche
-- d'effectif salarié de l'établissement) — même principe et même finalité
-- que sql/init_taille_entreprise.sql côté leads_professionnels. Déjà
-- présente dans la réponse SIRENE que scraper_batiment.py interroge (aucun
-- nouvel appel API), juste un champ capturé en plus.
--
-- Reste NULL pour les leads sourcés via le fallback PagesJaunes (pas
-- d'appel SIRENE dans cette méthode) : jamais de valeur fabriquée.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS tranche_effectif_salarie TEXT;
