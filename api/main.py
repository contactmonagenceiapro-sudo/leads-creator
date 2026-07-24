import logging
import os
import subprocess
import sys
import glob
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Import du CEO Agent (le volume docker-compose monte la racine du projet sur /app)
sys.path.append("/app")
from ceo_agent import run_ceo_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [API] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ai_ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

if not API_SECRET_KEY:
    log.warning(
        "API_SECRET_KEY n'est pas définie : tous les endpoints protégés "
        "renverront 500 tant que cette variable n'est pas configurée dans .env."
    )

# === SÉCURITÉ ===
def verifier_cle_api(x_api_key: str | None = Header(default=None)) -> None:
    """Protège les endpoints mutants/sensibles. Cette API relaie la clé
    Supabase service_role : sans cette vérification, quiconque atteint le
    port 8000 a un accès en lecture/écriture total sur la base de données."""
    if not API_SECRET_KEY:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY non configurée côté serveur")
    if x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante (header X-API-Key)")


# === SCHEDULER ===
scheduler = AsyncIOScheduler()

async def ceo_agent_job():
    log.info("Cron : lancement automatique du CEO Agent")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_ceo_analysis)

async def leads_agent_job():
    log.info("Cron : lancement automatique du Lead Worker")
    subprocess.Popen([sys.executable, "lead_worker.py"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(ceo_agent_job, CronTrigger(day_of_week="mon", hour=8, minute=0))
    scheduler.add_job(leads_agent_job, CronTrigger(hour="*/4"))
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

# === HELPERS SUPABASE ===
def supabase_headers(on_conflict: str | None = None) -> dict:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if on_conflict:
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    return headers

def supabase_get(table: str, params: str = "") -> list:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=supabase_headers(), timeout=10)
        return res.json() if res.status_code == 200 else []
    except requests.exceptions.RequestException as e:
        log.error(f"Supabase GET {table} : {e}")
        return []

# === TÂCHE DE FOND ===
def execution_differee(payload: dict):
    """Exécute la génération de l'article en arrière-plan."""
    try:
        from agents.workers.content_writer import write_article
        result = write_article(payload)
        log.info(f"Article généré avec succès pour : {payload.get('keyword')} (qualité {result.get('quality_score')}/100)")
    except Exception as e:
        log.error(f"Erreur génération article en fond : {e}")

# === ENDPOINTS ===
@app.get("/")
def read_root():
    return {"status": "running", "version": "2.1.0"}

@app.get("/stats", dependencies=[Depends(verifier_cle_api)])
def get_stats():
    leads_count = len(supabase_get("leads", "select=id"))
    articles_count = len(supabase_get("articles", "select=id"))
    reports = supabase_get("ceo_reports", "select=*&order=created_at.desc&limit=1")
    return {
        "leads_total": leads_count,
        "articles_generes": articles_count,
        "last_ceo_report": reports[0].get("date") if reports else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.post("/tasks/content", dependencies=[Depends(verifier_cle_api)])
async def endpoint_content(payload: dict, background_tasks: BackgroundTasks):
    """Délègue la génération à une tâche de fond pour éviter le timeout dans n8n."""
    background_tasks.add_task(execution_differee, payload)
    return {"status": "accepted", "message": "Génération lancée en arrière-plan"}

@app.post("/sb_insert", dependencies=[Depends(verifier_cle_api)])
def sb_insert(table: str, payload: dict, on_conflict: str | None = None):
    """Insère (ou upsert si `on_conflict` est fourni, ex: on_conflict=email)
    une ligne dans Supabase. Le paramètre on_conflict permet d'éviter les
    doublons via `Prefer: resolution=merge-duplicates`."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Missing Supabase credentials")
    try:
        params = {"on_conflict": on_conflict} if on_conflict else {}
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=supabase_headers(on_conflict),
            params=params,
            json=payload,
            timeout=15,
        )
        return {"status_code": res.status_code, "response": res.text}
    except requests.exceptions.RequestException as e:
        return {"status_code": 500, "response": str(e)}

@app.post("/leads", dependencies=[Depends(verifier_cle_api)])
def add_lead(payload: dict):
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    return sb_insert("leads", payload)

@app.get("/leads", dependencies=[Depends(verifier_cle_api)])
def get_leads():
    return {"leads": supabase_get("leads", "select=*&order=created_at.desc&limit=100")}

@app.post("/prospection/send-all", dependencies=[Depends(verifier_cle_api)])
def send_prospection(background_tasks: BackgroundTasks):
    def _prospect():
        leads = supabase_get("leads", "select=*&email=not.is.null&limit=20")
        log.info(f"Prospection sur {len(leads)} leads")
    background_tasks.add_task(_prospect)
    return {"status": "prospection_started"}

@app.post("/ceo/run", dependencies=[Depends(verifier_cle_api)])
def run_ceo_agent(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ceo_analysis)
    return {"status": "ceo_agent_started"}

@app.get("/contents", dependencies=[Depends(verifier_cle_api)])
def get_contents_list():
    files = sorted(glob.glob("article*.md"))
    contents = []
    for f in files:
        with open(f, "r", encoding="utf-8") as file:
            contents.append({"filename": f, "content": file.read()})
    return contents

@app.post("/agent/trigger", dependencies=[Depends(verifier_cle_api)])
def trigger_agent(payload: dict):
    action = payload.get("action")
    if action == "leads":
        subprocess.Popen([sys.executable, "lead_worker.py"])
        return {"status": "success", "action_triggered": "leads worker started"}
    return {"status": "error", "message": "Unknown action"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
