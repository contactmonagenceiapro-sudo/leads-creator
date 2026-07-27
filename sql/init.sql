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

-- Champs "données d'entreprise" structurés pour un usage commercial du lot
-- (export/vente à un client B2B) : telephone n'est renseigné que lorsqu'un
-- numéro réel a été trouvé sur une page validée (jamais fabriqué, cf.
-- email_enricher.py::extraire_telephone_depuis_html). siren/adresse
-- proviennent directement du registre SIRENE (données publiques officielles).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS telephone TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS siren TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS adresse TEXT;

-- Suivi du cycle de relance (relance_prospects.py) : contacted_at marque le
-- premier envoi (ceo_agent.py), relance_count/last_relance_at permettent de
-- savoir quel palier de relance appliquer et quand, sans jamais harceler un
-- prospect au-delà de MAX_RELANCES (cf. relance_prospects.py).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contacted_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS relance_count INTEGER DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_relance_at TIMESTAMPTZ;

-- Conservé comme garde-fou d'intégrité (deux entreprises distinctes ne
-- devraient pas partager un email réel), mais N'EST PLUS la clé de
-- dé-duplication utilisée par lead_worker.py (voir idx_leads_company_unique
-- ci-dessous). Index partiel : n'impose l'unicité que sur les emails
-- renseignés, pour ne pas bloquer les lignes sans email (majoritaires
-- depuis que scraper_batiment.py ne fabrique plus d'email non vérifié).
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email_unique
ON leads (email)
WHERE email IS NOT NULL;

-- Empêche les doublons de leads par entreprise et permet l'upsert
-- (Prefer: resolution=merge-duplicates + on_conflict=company côté API).
-- Remplace l'ancienne dé-duplication par email : depuis que
-- scraper_batiment.py/email_enricher.py peuvent légitimement laisser
-- email=NULL (aucun domaine réel trouvé), un upsert basé sur email seul ne
-- protège plus contre les doublons — chaque nouveau run recréait une ligne
-- pour chaque entreprise sans email au lieu de mettre à jour l'existante.
-- ATTENTION : si des doublons de company existent déjà dans une base
-- existante, cette commande échouera : dédupliquer manuellement avant de
-- la relancer (cf. script de nettoyage utilisé lors de la migration).
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_company_unique
ON leads (company);

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

-- Réponses au formulaire d'intake asynchrone (étape "Done For You" : dès
-- qu'un artisan répond positivement, on lui envoie une présentation +
-- ce formulaire pour collecter le contenu nécessaire à la production du
-- site, sans jamais l'appeler).
CREATE TABLE IF NOT EXISTS intake_responses (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id UUID REFERENCES leads(id),
    description TEXT,
    zone_activite TEXT,
    lien_photos TEXT,
    lien_site_actuel TEXT,
    lien_gbp TEXT,
    telephone_public TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_intake_lead ON intake_responses(lead_id);

-- Cycle de vie contrat + paiement, déclenché après l'intake (soumettre_formulaire_intake).
-- Statuts leads ajoutés à la suite de "intake_recu" :
--   contrat_envoye -> contrat_signe -> lien_paiement_envoye -> paye
CREATE TABLE IF NOT EXISTS contracts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id UUID REFERENCES leads(id) UNIQUE,
    yousign_request_id TEXT,
    yousign_status TEXT DEFAULT 'a_envoyer',
    stripe_payment_link_id TEXT,
    stripe_payment_url TEXT,
    payment_status TEXT DEFAULT 'en_attente',
    montant_centimes INTEGER,
    signed_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contracts_lead ON contracts(lead_id);
CREATE INDEX IF NOT EXISTS idx_contracts_yousign ON contracts(yousign_request_id);
CREATE INDEX IF NOT EXISTS idx_contracts_stripe_link ON contracts(stripe_payment_link_id);

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
