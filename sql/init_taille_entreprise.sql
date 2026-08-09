-- Extension additive de leads_professionnels : taille d'entreprise (code
-- INSEE de tranche d'effectif salarié de l'établissement, ex: "11" = 10 à
-- 19 salariés), déjà présente dans la réponse SIRENE que
-- outbound_chantiers/sourcing_acteurs_pro.py interroge — aucun nouvel
-- appel API, juste un champ capturé en plus. Utilisée comme critère de
-- score BONUS PLAFONNÉ (voir scorer_et_publier.py::score_taille_entreprise
-- et config.py::BONUS_MAX_TAILLE_ENTREPRISE), jamais comme critère
-- éliminatoire.

ALTER TABLE leads_professionnels ADD COLUMN IF NOT EXISTS tranche_effectif_salarie TEXT;
