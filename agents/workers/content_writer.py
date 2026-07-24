import os
import json
import requests
from datetime import datetime
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from memory.vector_store import VectorMemory
from dotenv import load_dotenv
load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MAIN_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")

memory = VectorMemory()

CONTENT_WRITER_PROMPT = """Tu es un expert en rédaction SEO pour PME françaises.
Tu rédiges des articles de blog professionnels, clairs, utiles et optimisés SEO.

CONSIGNES :
- Longueur exacte : {length} mots
- Ton : {tone}
- Mot-clé principal : {keyword}
- Intègre le mot-clé dans : titre H1, introduction, 2-3 sous-titres, conclusion
- Structure : H1 > Introduction > 3-4 sections H2 > Conclusion avec CTA
- Format : Markdown
- Pas de formules génériques ou de remplissage

Écris directement l'article, sans preamble ni commentaire."""

def call_ollama(prompt: str, model: str = None) -> str:
    """Appelle Ollama directement via HTTP"""
    model = model or MAIN_MODEL
    
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_ctx": 4096
            }
        },
        timeout=120
    )
    
    return response.json()["response"]

def score_content(content: str, keyword: str) -> int:
    """Score basique de qualité du contenu 0-100"""
    score = 0
    word_count = len(content.split())
    
    # Longueur correcte
    if word_count > 800: score += 20
    if word_count > 1200: score += 10
    
    # Présence du mot-clé
    keyword_count = content.lower().count(keyword.lower())
    if keyword_count >= 3: score += 20
    if keyword_count >= 5: score += 10
    
    # Structure Markdown
    if "# " in content: score += 15
    if "## " in content: score += 15
    if len([l for l in content.split('\n') if l.startswith('## ')]) >= 3: score += 10
    
    return min(score, 100)

def write_article(task: dict) -> dict:
    """
    Génère un article SEO complet.
    
    task = {
        "keyword": "automatisation comptabilité PME",
        "length": 1500,
        "tone": "expert et accessible",
        "client_context": "PME de services",
    }
    """
    print(f"[ContentWriter] Rédaction: {task['keyword']}")
    
    # Vérification cache mémoire
    cached = memory.query(
        f"article SEO {task['keyword']}",
        agent_id="content_writer",
        limit=1
    )
    
    if cached and cached[0].get("similarity", 0) > 0.93:
        print(f"[ContentWriter] Cache hit!")
        return {"content": cached[0]["content"], "source": "cache", "quality_score": 85}
    
    # Génération de l'article
    prompt = CONTENT_WRITER_PROMPT.format(
        keyword=task["keyword"],
        length=task.get("length", 1200),
        tone=task.get("tone", "expert et accessible")
    )
    
    article = call_ollama(prompt)
    quality = score_content(article, task["keyword"])
    
    # Si qualité insuffisante, une deuxième passe
    if quality < 60:
        print(f"[ContentWriter] Qualité {quality}/100, amélioration...")
        improve_prompt = f"""Améliore cet article pour qu'il soit plus structuré, 
        plus détaillé et mieux optimisé pour le mot-clé "{task['keyword']}".
        
        Article actuel :
        {article}
        
        Réécris-le en améliorant : structure, densité mot-clé, valeur pratique."""
        
        article = call_ollama(improve_prompt)
        quality = score_content(article, task["keyword"])
    
    # Sauvegarde en mémoire
    memory.store(
        content=article[:300],
        agent_id="content_writer",
        metadata={"keyword": task["keyword"], "quality": quality}
    )
    
    print(f"[ContentWriter] Article généré — qualité: {quality}/100")
    
    return {
        "content": article,
        "keyword": task["keyword"],
        "word_count": len(article.split()),
        "quality_score": quality,
        "status": "ready"
    }

# Test direct
if __name__ == "__main__":
    result = write_article({
        "keyword": "automatisation tâches répétitives PME",
        "length": 1000,
        "tone": "professionnel et concret"
    })
    print(f"Qualité: {result['quality_score']}/100")
    print(f"Mots: {result['word_count']}")
    print("\n--- Début de l'article ---")
    print(result['content'][:500])
