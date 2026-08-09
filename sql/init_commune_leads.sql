-- Ajoute une colonne `commune` structurée à `leads` (jusqu'ici présente
-- seulement en texte libre dans `notes`, sous la forme "ville=Lyon 3e | ...").
-- Nécessaire pour croiser proprement chaque artisan avec le signal de
-- dynamisme communal (Sitadel3/SDES, voir
-- outbound_chantiers/signal_activite_chantiers.py) : source NATIONALE, donc
-- directement réutilisable ici malgré les deux zones historiquement
-- scrapées (Grand Est et Métropole de Lyon).
--
-- scraper_batiment.py / lead_worker.py alimentent désormais cette colonne
-- directement pour tout nouveau lead (les deux méthodes de scraping,
-- SIRENE et PagesJaunes) — cette migration ajoute la colonne au schéma ET
-- reconstitue la valeur pour les leads déjà en base, à partir du même texte
-- libre "ville=" déjà présent dans `notes` (fiable sur 595/615 leads
-- existants au moment de cette migration).
--
-- Idempotente : ne touche que les lignes où `commune` est encore NULL, donc
-- rejouable sans écraser une valeur déjà renseignée.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS commune TEXT;

UPDATE leads
SET commune = NULLIF(trim(substring(notes from 'ville=([^|]*)')), '')
WHERE commune IS NULL
  AND notes ~ 'ville=';
