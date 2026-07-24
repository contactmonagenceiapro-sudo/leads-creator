import os
import json
from datetime import datetime
from supabase import create_client
from memory.embeddings import generate_embedding

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

class VectorMemory:
    
    def store(self, content: str, agent_id: str, metadata: dict = None):
        """Stocke un souvenir avec son embedding"""
        embedding = generate_embedding(content)
        
        supabase.table("agent_memories").insert({
            "agent_id": agent_id,
            "content": content,
            "embedding": embedding,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat()
        }).execute()
    
    def query(self, query: str, agent_id: str = None, limit: int = 5) -> list:
        """Cherche des souvenirs similaires"""
        embedding = generate_embedding(query)
        
        result = supabase.rpc("match_memories", {
            "query_embedding": embedding,
            "match_threshold": 0.7,
            "match_count": limit,
            "filter_agent": agent_id
        }).execute()
        
        return result.data or []
