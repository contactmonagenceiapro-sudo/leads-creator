import requests
import os

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("OLLAMA_MODEL_EMBED", "nomic-embed-text")

def generate_embedding(text: str) -> list:
    """Génère un embedding vectoriel via Ollama (100% gratuit, local)"""
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30
        )
        return response.json()["embedding"]
    except Exception as e:
        print(f"Erreur embedding: {e}")
        return [0.0] * 768
