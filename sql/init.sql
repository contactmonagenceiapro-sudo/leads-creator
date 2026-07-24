-- Active l'extension vectorielle
CREATE EXTENSION IF NOT EXISTS vector;

-- Table de mémoire des agents
CREATE TABLE IF NOT EXISTS agent_memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB DEFAULT '{}',
    memory_type TEXT DEFAULT 'episodic',
    importance FLOAT DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table des tâches
CREATE TABLE IF NOT EXISTS tasks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    input_data JSONB DEFAULT '{}',
    output_data JSONB DEFAULT '{}',
    quality_score FLOAT,
    tokens_used INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Table des KPIs business
CREATE TABLE IF NOT EXISTS kpis (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_value FLOAT NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table des clients/leads
-- NOTE : les colonnes industry / weakness / pitch_commercial / contacted ont été
-- ajoutées pour correspondre exactement aux champs produits par le pipeline
-- scraper_batiment.py -> lead_worker.py -> ceo_agent.py. Sans elles, l'insertion
-- Supabase échouait silencieusement (colonnes inexistantes).
CREATE TABLE IF NOT EXISTS leads (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT,
    email TEXT,
    company TEXT,
    industry TEXT,
    weakness TEXT,
    pitch_commercial TEXT,
    status TEXT DEFAULT 'new',
    contacted BOOLEAN DEFAULT FALSE,
    score INTEGER DEFAULT 0,
    source TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ajout idempotent pour les bases déjà existantes créées avant cette révision
ALTER TABLE leads ADD COLUMN IF NOT EXISTS industry TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS weakness TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS pitch_commercial TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contacted BOOLEAN DEFAULT FALSE;

-- Empêche les doublons de leads par email et permet l'upsert
-- (Prefer: resolution=merge-duplicates + on_conflict=email côté API).
-- Index partiel : n'impose l'unicité que sur les emails renseignés,
-- pour ne pas bloquer d'éventuelles lignes historiques sans email.
-- ATTENTION : si des doublons d'email existent déjà dans une base existante,
-- cette commande échouera : dédupliquer manuellement avant de la relancer.
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email_unique
ON leads (email)
WHERE email IS NOT NULL;

-- Table des erreurs pour auto-amélioration
CREATE TABLE IF NOT EXISTS error_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    context JSONB DEFAULT '{}',
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index pour la recherche vectorielle
CREATE INDEX IF NOT EXISTS idx_memories_embedding
ON agent_memories USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 50);

-- Index sur les tâches
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- Fonction de recherche sémantique
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 5,
    filter_agent text DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    metadata jsonb,
    similarity float
)
LANGUAGE sql STABLE AS $$
    SELECT
        id, content, metadata,
        1 - (embedding <=> query_embedding) AS similarity
    FROM agent_memories
    WHERE
        (filter_agent IS NULL OR agent_id = filter_agent)
        AND embedding IS NOT NULL
        AND 1 - (embedding <=> query_embedding) > match_threshold
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Données initiales KPIs
-- NOTE : ce script est ré-exécutable (CREATE ... IF NOT EXISTS) mais cet INSERT
-- ne l'est pas : le relancer sur une base déjà initialisée duplique ces lignes
-- (pas de contrainte unique sur metric_name). À n'exécuter qu'une seule fois,
-- ou ajouter une contrainte UNIQUE(metric_name) si des ré-exécutions sont prévues.
INSERT INTO kpis (metric_name, metric_value) VALUES
    ('mrr', 0),
    ('leads_total', 0),
    ('content_published', 0),
    ('clients_active', 0);
