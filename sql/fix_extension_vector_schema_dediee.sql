-- Audit sécurité/perf Supabase du 26/08/2026 (advisors) : l'extension
-- `vector` (pgvector, utilisée par agent_memories.embedding /
-- match_memories, voir sql/init.sql) était installée dans le schéma
-- `public`, alors que Supabase recommande un schéma dédié pour toute
-- extension (limite la surface d'objets non applicatifs exposés dans
-- public — advisor "extension_in_public").
--
-- Déplacement SANS casser match_memories : ALTER EXTENSION ... SET SCHEMA
-- ne modifie ni les types déjà en usage (agent_memories.embedding reste un
-- vector(768) valide, référencé par OID, pas par nom qualifié) ni les
-- données existantes, ni l'index ivfflat déjà créé (idx_memories_embedding,
-- lié à l'opérateur par OID lui aussi). Seule la résolution de l'opérateur
-- <=> AU MOMENT DE L'EXÉCUTION d'une requête dépend du search_path -> donc
-- corrigée ci-dessous en même temps que le search_path mutable de
-- match_memories (avertissement séparé du même audit,
-- function_search_path_mutable) : les deux se règlent par la même clause
-- SET search_path sur la fonction.

CREATE SCHEMA IF NOT EXISTS extensions;
ALTER EXTENSION vector SET SCHEMA extensions;

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
LANGUAGE sql STABLE
SET search_path = public, extensions
AS $$
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
