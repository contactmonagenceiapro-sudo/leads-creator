-- Automatisation de mail_processor.py (relève des bounces/réponses) : voir
-- mail_processor.py::check_for_replies. Le bouton manuel du dashboard et
-- l'exécution automatique (GitHub Actions, voir .github/workflows/mail_check.yml)
-- lancent tous les deux le MÊME script (`python3 mail_processor.py`), sur
-- des machines différentes (conteneur Streamlit Cloud vs runner GitHub) —
-- aucun état en mémoire/fichier local ne peut donc coordonner les deux :
-- Supabase, seul point commun aux deux environnements, sert de verrou et de
-- journal partagés.

-- Verrou mono-ligne : une exécution en cours empêche toute autre exécution
-- (manuelle ou automatique) de démarrer tant qu'elle n'a pas libéré la ligne
-- (ou que locked_at n'est pas devenu "périmé", voir VERROU_STALE_MINUTES
-- dans mail_processor.py — filet de sécurité si un run plante sans passer
-- par le "finally" qui libère normalement le verrou).
CREATE TABLE IF NOT EXISTS mail_check_lock (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- une seule ligne possible
    locked_at TIMESTAMPTZ,
    locked_by TEXT
);
INSERT INTO mail_check_lock (id, locked_at, locked_by)
VALUES (1, NULL, NULL)
ON CONFLICT (id) DO NOTHING;

-- Historique des exécutions (manuelles ET automatiques, distinguées par
-- `source`) — répond au besoin de traçabilité ("chaque exécution
-- automatique : date/heure, nombre de bounces traités, nombre d'adresses
-- blacklistées"), étendu aux runs manuels par cohérence (même code, même
-- table, un seul endroit à consulter).
CREATE TABLE IF NOT EXISTS mail_check_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    executed_at TIMESTAMPTZ DEFAULT NOW(),
    source TEXT NOT NULL DEFAULT 'manuel',   -- 'manuel' (bouton dashboard) | 'auto' (GitHub Actions)
    statut TEXT NOT NULL DEFAULT 'ok',       -- 'ok' | 'ignore_chevauchement' | 'erreur'
    messages_scannes INT DEFAULT 0,
    bounces_total INT DEFAULT 0,
    bounces_blackliste INT DEFAULT 0,        -- hard bounces confirmés (= adresses blacklistées)
    erreur TEXT
);

CREATE INDEX IF NOT EXISTS idx_mail_check_runs_executed_at ON mail_check_runs (executed_at DESC);

