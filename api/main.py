import hashlib
import hmac
import html
import json
import logging
import re
import os
import subprocess
import sys
import glob
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import bcrypt
import jwt
import requests
import stripe
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fpdf import FPDF

# Import du CEO Agent et du CRM mail (le volume docker-compose monte la
# racine du projet sur /app)
sys.path.append("/app")
from ceo_agent import run_ceo_analysis
from mail_processor import check_for_replies
from relance_prospects import relancer_prospects

logging.basicConfig(level=logging.INFO, format="%(asctime)s [API] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ai_ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL_MAIN", "qwen2.5:7b")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")
AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# === SIGNATURE ÉLECTRONIQUE (Yousign) + PAIEMENT (Stripe) ===
YOUSIGN_API_KEY = os.getenv("YOUSIGN_API_KEY", "")
YOUSIGN_API_URL = os.getenv("YOUSIGN_API_URL", "https://api-sandbox.yousign.app/v3")
YOUSIGN_WEBHOOK_SECRET = os.getenv("YOUSIGN_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
CONTRACT_AMOUNT_EUR = float(os.getenv("CONTRACT_AMOUNT_EUR", "990"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# Cadence des tâches de fond automatiques, configurable sans toucher au code.
# NOTE : ce scheduler interne à l'API est désormais l'UNIQUE source de
# planification (le crontab hôte a été retiré) — il faisait auparavant
# doublon avec des entrées crontab séparées (ceo_agent.py quotidien à 20h,
# mail_processor.py toutes les 5 min), ce qui rendait la cadence réelle du
# pipeline impossible à connaître avec certitude sans vérifier deux endroits.
CRON_LEADS_INTERVAL_HOURS = int(os.getenv("CRON_LEADS_INTERVAL_HOURS", "1"))
CRON_MAIL_INTERVAL_MINUTES = int(os.getenv("CRON_MAIL_INTERVAL_MINUTES", "5"))
CRON_CEO_AGENT_HOUR = int(os.getenv("CRON_CEO_AGENT_HOUR", "20"))
# Département "Outbound & Génération de Chantiers" : "Pipeline Automatique"
# planifié, une fois par jour, pour CHAQUE campagne active (table campagnes).
CRON_OUTBOUND_AUTO_HOUR = int(os.getenv("CRON_OUTBOUND_AUTO_HOUR", "7"))

# Même variable que outbound_chantiers/config.py (SEUIL_LEAD_ULTRA_QUALIFIE) :
# utilisée ici pour le KPI affiché au dashboard (/campagnes/{x}/stats), là-bas
# pour déclencher l'alerte Discord/e-mail — un seul réglage, deux lectures.
SEUIL_LEAD_ULTRA_QUALIFIE = float(os.getenv("SEUIL_LEAD_ULTRA_QUALIFIE", "0.85"))

if not API_SECRET_KEY:
    log.warning(
        "API_SECRET_KEY n'est pas définie : tous les endpoints protégés "
        "renverront 500 tant que cette variable n'est pas configurée dans .env."
    )

# === PORTAIL CLIENT (authentification) ===
# Réutilise API_SECRET_KEY comme secret de signature JWT : déjà un secret
# fort exclusivement côté serveur (jamais exposé), pas besoin d'en ajouter un.
PORTAIL_JWT_SECRET = API_SECRET_KEY
PORTAIL_SESSION_DUREE_HEURES = int(os.getenv("PORTAIL_SESSION_DUREE_HEURES", "12"))


# === SÉCURITÉ ===
def verifier_cle_api(x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> None:
    """Protège les endpoints STRICTEMENT ADMIN (déclenchement d'actions,
    configuration des campagnes, remboursements, accès brut à la base...).
    Cette API relaie la clé Supabase service_role : sans cette vérification,
    quiconque atteint le port 8000 a un accès en lecture/écriture total.

    Accepte DEUX credentials, jamais mélangés :
    - X-API-Key = clé maître historique (automatisation : n8n, scheduler interne) ;
    - Authorization: Bearer <jwt> avec role='admin' (portail, session interactive).
    Un jeton 'client' est TOUJOURS rejeté ici, même valide — ces endpoints ne
    lui sont jamais accessibles, quelle que soit la campagne."""
    if x_api_key and API_SECRET_KEY and x_api_key == API_SECRET_KEY:
        return
    if authorization and authorization.startswith("Bearer "):
        try:
            payload = jwt.decode(authorization.removeprefix("Bearer ").strip(), PORTAIL_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Session invalide ou expirée, reconnectez-vous.")
        if payload.get("role") == "admin":
            return
        raise HTTPException(status_code=403, detail="Action réservée à l'administrateur.")
    if not API_SECRET_KEY:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY non configurée côté serveur")
    raise HTTPException(status_code=401, detail="Clé API invalide ou manquante (header X-API-Key ou Authorization: Bearer)")


def obtenir_identite_dashboard(
    x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)
) -> dict:
    """Identité de l'appelant pour les endpoints à DOUBLE MODE (lecture
    admin totale OU lecture client restreinte à ses campagnes). Renvoie
    {"role": "admin", "campagnes": None} (None = aucune restriction) ou
    {"role": "client", "campagnes": [...]}. Lève 401 si aucun credential valide."""
    if x_api_key and API_SECRET_KEY and x_api_key == API_SECRET_KEY:
        return {"role": "admin", "campagnes": None}
    if authorization and authorization.startswith("Bearer "):
        try:
            payload = jwt.decode(authorization.removeprefix("Bearer ").strip(), PORTAIL_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Session invalide ou expirée, reconnectez-vous.")
        return {"role": payload.get("role"), "campagnes": payload.get("campagnes") or []}
    raise HTTPException(status_code=401, detail="Authentification requise (clé API ou session invalide)")


# === SCHEDULER ===
scheduler = AsyncIOScheduler()

async def ceo_agent_job():
    log.info("Cron : lancement automatique du CEO Agent")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_ceo_analysis)

async def leads_agent_job():
    log.info("Cron : lancement automatique du Lead Worker")
    subprocess.Popen([sys.executable, "lead_worker.py"])

async def mail_agent_job():
    log.info("Cron : lancement automatique de la surveillance des réponses (mail_processor)")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, check_for_replies)

async def relance_agent_job():
    log.info("Cron : lancement automatique des relances de prospects sans réponse")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, relancer_prospects)

# Mémorise le dernier état connu pour n'alerter que sur un CHANGEMENT d'état
# (down -> ok ou ok -> down), pas à chaque vérification tant que la panne dure.
_DERNIER_ETAT_SANTE = {"unhealthy": False}

def alerter_discord(message: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Échec envoi alerte Discord : {e}")

async def health_watchdog_job():
    """Surveille /health et alerte Discord uniquement lors d'un changement
    d'état (dégradation ou rétablissement) — sans cette veille active, une
    panne (Supabase, Ollama) ne serait visible qu'en consultant l'API
    manuellement."""
    resultat = health_check()
    est_unhealthy = resultat.get("status_global") == "unhealthy"
    if est_unhealthy and not _DERNIER_ETAT_SANTE["unhealthy"]:
        pannes = [f"{k}: {v}" for k, v in resultat.items() if isinstance(v, str) and v.startswith("down")]
        alerter_discord(f"🔴 **ai-company API dégradée** — {' | '.join(pannes)}")
    elif not est_unhealthy and _DERNIER_ETAT_SANTE["unhealthy"]:
        alerter_discord("🟢 **ai-company API rétablie** — tous les services répondent normalement.")
    _DERNIER_ETAT_SANTE["unhealthy"] = est_unhealthy

async def outbound_auto_job():
    """"Pipeline Automatique" planifié : enchaîne sourcing -> enrichissement
    -> scoring -> campagne + relances (modules 1-4) pour CHAQUE campagne
    active en base (table campagnes), sans intervention manuelle. Une
    campagne en pause ou archivée n'est jamais déclenchée automatiquement."""
    campagnes = supabase_get("campagnes", "select=nom_client&statut=eq.active")
    if not campagnes:
        log.info("Cron : pipeline automatique — aucune campagne active configurée")
        return
    for campagne in campagnes:
        nom_client = campagne["nom_client"]
        log.info(f"Cron : lancement automatique du pipeline complet pour la campagne « {nom_client} »")
        env = _construire_env_campagne({"campagne": nom_client})
        _lancer_subprocess_suivi(
            "pipeline_auto", nom_client,
            [sys.executable, "-m", "outbound_chantiers.pipeline_outbound_chantiers", "--avec-envoi"],
            env=env,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(ceo_agent_job, CronTrigger(hour=CRON_CEO_AGENT_HOUR, minute=0))
    scheduler.add_job(leads_agent_job, CronTrigger(hour=f"*/{CRON_LEADS_INTERVAL_HOURS}"))
    scheduler.add_job(mail_agent_job, CronTrigger(minute=f"*/{CRON_MAIL_INTERVAL_MINUTES}"))
    scheduler.add_job(relance_agent_job, CronTrigger(hour=9, minute=30))
    scheduler.add_job(health_watchdog_job, CronTrigger(minute="*/15"))
    scheduler.add_job(outbound_auto_job, CronTrigger(hour=CRON_OUTBOUND_AUTO_HOUR, minute=0))
    scheduler.start()
    log.info(
        f"Scheduler démarré : leads toutes les {CRON_LEADS_INTERVAL_HOURS}h, "
        f"mails toutes les {CRON_MAIL_INTERVAL_MINUTES}min, campagne CEO tous les "
        f"jours à {CRON_CEO_AGENT_HOUR}h, relances tous les jours à 9h30, "
        f"surveillance santé toutes les 15min, pipeline automatique (toutes "
        f"campagnes actives) tous les jours à {CRON_OUTBOUND_AUTO_HOUR}h"
    )
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


@app.get("/health")
def health_check():
    """Vérifie la disponibilité réelle des dépendances externes (pas
    seulement que le process FastAPI répond). Utilisé par le healthcheck
    Docker et par le dashboard de monitoring."""
    resultat = {"api": "ok"}

    if SUPABASE_URL and SUPABASE_KEY:
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/", headers=supabase_headers(), timeout=5)
            resultat["supabase"] = "ok" if r.status_code < 500 else "degraded"
        except requests.exceptions.RequestException as e:
            resultat["supabase"] = f"down ({e})"
    else:
        resultat["supabase"] = "non configuré"

    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        modeles = [m["name"] for m in r.json().get("models", [])] if r.status_code == 200 else []
        resultat["ollama"] = "ok" if r.status_code == 200 and modeles else "degraded (aucun modèle installé)"
    except requests.exceptions.RequestException as e:
        resultat["ollama"] = f"down ({e})"

    resultat["zoho_configure"] = bool(os.getenv("ZOHO_USER") and os.getenv("ZOHO_PASSWORD"))
    resultat["discord_configure"] = bool(os.getenv("DISCORD_WEBHOOK_URL"))

    en_panne = [k for k, v in resultat.items() if isinstance(v, str) and v.startswith("down")]
    resultat["status_global"] = "unhealthy" if en_panne else "healthy"
    return resultat


@app.post("/auth/login")
def login(payload: dict):
    """Point d'entrée PUBLIC du Portail Client — c'est le seul endroit où
    aucune authentification n'est requise (par définition, on ne l'a pas
    encore). Vérifie email + mot de passe (hash bcrypt), émet un jeton JWT
    contenant le rôle et les campagnes autorisées (vide pour un admin =
    aucune restriction), valable PORTAIL_SESSION_DUREE_HEURES heures."""
    email = (payload.get("email") or "").strip().lower()
    mot_de_passe = payload.get("mot_de_passe") or ""
    if not email or not mot_de_passe:
        raise HTTPException(status_code=400, detail="email et mot_de_passe requis")

    utilisateurs = supabase_get("utilisateurs_dashboard", f"select=*&email=eq.{quote(email)}&actif=eq.true")
    if not utilisateurs:
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    utilisateur = utilisateurs[0]

    try:
        mot_de_passe_valide = bcrypt.checkpw(
            mot_de_passe.encode("utf-8"), utilisateur["mot_de_passe_hash"].encode("utf-8")
        )
    except ValueError:
        # bcrypt >=4.0 lève ValueError (au lieu de renvoyer False) pour un
        # mot de passe > 72 octets ou un hash stocké malformé — jamais une
        # 500 brute pour un cas qui reste, du point de vue du client, un
        # simple échec d'authentification.
        mot_de_passe_valide = False
    if not mot_de_passe_valide:
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    liens = supabase_get("utilisateur_campagnes", f"select=client_final&utilisateur_id=eq.{utilisateur['id']}")
    campagnes_autorisees = [l["client_final"] for l in liens]

    if utilisateur["role"] == "client" and not campagnes_autorisees:
        raise HTTPException(
            status_code=403,
            detail="Aucune campagne associée à ce compte — contactez l'administrateur.",
        )

    expiration = datetime.now(timezone.utc) + timedelta(hours=PORTAIL_SESSION_DUREE_HEURES)
    jeton = jwt.encode(
        {
            "sub": utilisateur["id"],
            "email": utilisateur["email"],
            "role": utilisateur["role"],
            "campagnes": campagnes_autorisees,
            "exp": expiration,
        },
        PORTAIL_JWT_SECRET,
        algorithm="HS256",
    )
    return {
        "token": jeton,
        "role": utilisateur["role"],
        "campagnes": campagnes_autorisees,
        "email": utilisateur["email"],
        "expire_a": expiration.isoformat(),
    }


@app.post("/auth/signup")
def signup(payload: dict):
    """Point d'entrée PUBLIC de création de compte du Portail Client — comme
    /auth/login, aucune authentification n'est requise. Crée TOUJOURS un
    compte de rôle 'client' (jamais 'admin' : ce endpoint public ne doit
    jamais permettre d'auto-promotion), sans campagne associée — un
    administrateur doit ensuite le rattacher à une ou plusieurs campagnes
    (table utilisateur_campagnes, via creer_utilisateur_dashboard.py ou la
    page Administration & Contrats) avant que le compte puisse se connecter
    (voir /auth/login : un rôle client sans campagne est rejeté en 403)."""
    email = (payload.get("email") or "").strip().lower()
    mot_de_passe = payload.get("mot_de_passe") or ""
    if not email or not mot_de_passe:
        raise HTTPException(status_code=400, detail="email et mot_de_passe requis")
    if len(mot_de_passe) < 8:
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins 8 caractères")
    if len(mot_de_passe.encode("utf-8")) > 72:
        # bcrypt (>=4.0) lève ValueError et non plus une simple troncature
        # silencieuse au-delà de 72 octets — mieux vaut le rejeter proprement
        # ici que de laisser hashpw() planter en 500 plus bas.
        raise HTTPException(status_code=400, detail="Le mot de passe ne doit pas dépasser 72 caractères")

    existants = supabase_get("utilisateurs_dashboard", f"select=id&email=eq.{quote(email)}")
    if existants:
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet e-mail")

    mot_de_passe_hash = bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    resultat = sb_insert(
        "utilisateurs_dashboard",
        {"email": email, "mot_de_passe_hash": mot_de_passe_hash, "role": "client", "actif": True},
    )
    if resultat["status_code"] not in (200, 201):
        raise HTTPException(status_code=500, detail=f"Échec de la création du compte : {resultat['response']}")

    return {
        "message": "Compte créé. Un administrateur doit encore vous rattacher à une campagne avant que vous puissiez vous connecter.",
        "email": email,
    }


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


@app.get("/leads_pro")
def get_leads_pro(client_final: str = "S.B.G Travaux", identite: dict = Depends(obtenir_identite_dashboard)):
    """Acteurs professionnels (architectes, promoteurs, maîtres d'œuvre)
    sourcés par le département outbound_chantiers/ pour un client final
    donné — table séparée des artisans (leads), voir sql/init_leads_professionnels.sql.

    Portail Client : un compte 'client' ne peut interroger que les campagnes
    qui lui sont explicitement liées (utilisateur_campagnes) — 403 sinon."""
    if identite["role"] == "client" and client_final not in identite["campagnes"]:
        raise HTTPException(status_code=403, detail="Cette campagne ne vous est pas accessible.")
    filtre = f"select=*&client_final=eq.{quote(client_final)}&order=score_final.desc&limit=200"
    return {"leads_pro": supabase_get("leads_professionnels", filtre)}


@app.post("/leads_pro/{lead_pro_id}/enrichir", dependencies=[Depends(verifier_cle_api)])
def relancer_enrichissement_lead_pro(lead_pro_id: str):
    """Ré-enrichissement à la demande (bouton dashboard, admin uniquement) —
    réutilise exactement la même logique que le pipeline automatique (module
    2, voir outbound_chantiers/enrichir_acteurs_pro.py::enrichir_un_acteur),
    pour un seul acteur déjà en base. Ne remplace JAMAIS une donnée déjà
    connue par un résultat vide : seuls les champs manquants sont mis à
    jour, même si cette tentative échoue totalement."""
    leads_pro = supabase_get("leads_professionnels", f"select=*&id=eq.{lead_pro_id}")
    if not leads_pro:
        raise HTTPException(status_code=404, detail="Lead professionnel introuvable")
    lead_pro = leads_pro[0]

    from outbound_chantiers.enrichir_acteurs_pro import enrichir_un_acteur
    resultat = enrichir_un_acteur(lead_pro["nom_entreprise"], lead_pro.get("commune") or "")

    changements = {
        cle: valeur for cle, valeur in resultat.items()
        if cle != "contact_exploitable" and valeur and not lead_pro.get(cle)
    }
    changements["enrichissement_statut"] = resultat["enrichissement_statut"]
    changements["enrichi_at"] = datetime.now(timezone.utc).isoformat()

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads_professionnels",
        headers=supabase_headers(),
        params={"id": f"eq.{lead_pro_id}"},
        json=changements,
        timeout=10,
    )
    log.info(f"Ré-enrichissement de « {lead_pro['nom_entreprise']} » : statut={resultat['enrichissement_statut']}")
    return {"status": "success", "resultat": resultat}


def _slugifier(texte: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (texte or "").lower()).strip("-")
    return slug or "campagne"


# === CAMPAGNES (département outbound_chantiers/, générique multi-clients/secteurs) ===
# Une campagne = un client + son secteur + sa zone + ses types d'acteurs
# cibles avec leurs codes NAF et poids. Remplace les constantes en dur
# (CLIENT_FINAL, COMMUNES_CIBLES, NAF_CODES_CIBLES) qui limitaient le
# département à S.B.G Travaux / BTP uniquement. Voir sql/init_campagnes.sql.
@app.get("/campagnes")
def get_campagnes(identite: dict = Depends(obtenir_identite_dashboard)):
    """Portail Client : un compte 'client' ne voit que les campagnes qui lui
    sont explicitement liées, jamais la liste complète des clients de l'agence."""
    toutes = supabase_get("campagnes", "select=*&order=created_at.desc")
    if identite["role"] == "client":
        toutes = [c for c in toutes if c["nom_client"] in identite["campagnes"]]
    return {"campagnes": toutes}


@app.post("/campagnes", dependencies=[Depends(verifier_cle_api)])
def creer_ou_modifier_campagne(payload: dict):
    """Crée ou met à jour (upsert sur nom_client) une campagne.

    Champs attendus : nom_client, secteur, description_services,
    communes_cibles (liste de str), types_acteur_cibles (liste de
    {cle, libelle, codes_naf: [...], poids})."""
    nom_client = (payload.get("nom_client") or "").strip()
    if not nom_client:
        raise HTTPException(status_code=400, detail="nom_client est requis")

    corps = {
        "nom_client": nom_client,
        "slug": payload.get("slug") or _slugifier(nom_client),
        "secteur": payload.get("secteur") or "",
        "description_services": payload.get("description_services") or "",
        "communes_cibles": payload.get("communes_cibles") or [],
        "types_acteur_cibles": payload.get("types_acteur_cibles") or [],
        "statut": payload.get("statut") or "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resultat = sb_insert("campagnes", corps, on_conflict="nom_client")
    if resultat.get("status_code") not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Échec enregistrement campagne : {resultat.get('response')}")
    return resultat


@app.get("/campagnes/{nom_client}/stats")
def get_campagne_stats(nom_client: str, identite: dict = Depends(obtenir_identite_dashboard)):
    """Indicateurs de performance d'une campagne : volume de leads générés,
    taux de contact, nombre d'opportunités (réponses positives détectées).
    Portail Client : accès restreint à ses propres campagnes."""
    if identite["role"] == "client" and nom_client not in identite["campagnes"]:
        raise HTTPException(status_code=403, detail="Cette campagne ne vous est pas accessible.")
    filtre = f"select=contacted,statut,score_final&client_final=eq.{quote(nom_client)}"
    leads = supabase_get("leads_professionnels", filtre)
    total = len(leads)
    contactes = sum(1 for l in leads if l.get("contacted"))
    interesses = sum(1 for l in leads if l.get("statut") == "interested")
    ultra_qualifies = sum(1 for l in leads if (l.get("score_final") or 0) >= SEUIL_LEAD_ULTRA_QUALIFIE)
    return {
        "nom_client": nom_client,
        "leads_total": total,
        "contactes": contactes,
        "taux_contact": round(contactes / total, 3) if total else 0,
        "opportunites": interesses,
        "leads_ultra_qualifies": ultra_qualifies,
    }

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

def get_lead_by_id(lead_id: str) -> dict | None:
    resultats = supabase_get("leads", f"select=*&id=eq.{lead_id}")
    return resultats[0] if resultats else None


# === SIGNATURE ÉLECTRONIQUE (Yousign) ===
def generer_pdf_devis(lead: dict, intake: dict) -> bytes:
    """Génère un devis PDF minimal à partir des infos du lead et de l'intake,
    pour transmission en signature électronique (aucune saisie manuelle)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Devis - {AGENCY_NAME}", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(6)
    pdf.multi_cell(0, 7,
        f"Client : {lead.get('company', '')}\n"
        f"Description du projet : {intake.get('description', '')}\n\n"
        f"Prestation : Création de site vitrine + optimisation SEO local (Done For You)\n"
        f"Montant : {CONTRACT_AMOUNT_EUR:.2f} EUR TTC\n\n"
        f"En signant électroniquement ce document, le client accepte les "
        f"conditions ci-dessus et le lancement de la prestation."
    )
    return bytes(pdf.output(dest="S"))


def yousign_headers() -> dict:
    return {"Authorization": f"Bearer {YOUSIGN_API_KEY}"}


def nettoyer_nom_yousign(nom: str, repli: str = "Client") -> str:
    """Yousign rejette les prénoms/noms contenant certains caractères (ex:
    underscore) avec l'erreur 'This value has unauthorized chars.' — cas réel
    rencontré avec une raison sociale brute utilisée telle quelle comme
    prénom. Ne garde que lettres/espaces/tirets/apostrophes, jamais vide."""
    nettoye = re.sub(r"[^a-zA-ZÀ-ÿ\s'-]", " ", nom).strip()
    nettoye = re.sub(r"\s+", " ", nettoye)
    return nettoye or repli


def envoyer_contrat_signature(lead: dict, intake: dict) -> None:
    """Génère le devis et l'envoie automatiquement en signature électronique
    via Yousign, dès réception du formulaire d'intake (aucune action manuelle).
    Yousign se charge lui-même de l'email de signature envoyé au client."""
    lead_id = lead["id"]
    pdf_bytes = generer_pdf_devis(lead, intake)

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests",
        headers=yousign_headers(),
        json={"name": f"Devis {lead.get('company', lead_id)}", "delivery_mode": "email"},
        timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign création demande échouée pour {lead_id} : {r.status_code} {r.text}")
        return
    signature_request_id = r.json()["id"]

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/documents",
        headers=yousign_headers(),
        files={"file": (f"devis_{lead_id}.pdf", pdf_bytes, "application/pdf")},
        data={"nature": "signable_document"},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign upload document échoué pour {lead_id} : {r.status_code} {r.text}")
        return
    document_id = r.json()["id"]

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/signers",
        headers=yousign_headers(),
        json={
            "info": {
                "first_name": nettoyer_nom_yousign(lead.get("name") or lead.get("company") or "Client"),
                "last_name": "Client",
                "email": lead["email"],
                "locale": "fr",
            },
            "signature_level": "electronic_signature",
            "signature_authentication_mode": "no_otp",
            "fields": [{"document_id": document_id, "type": "signature", "page": 1, "x": 100, "y": 700}],
        },
        timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign ajout signataire échoué pour {lead_id} : {r.status_code} {r.text}")
        return

    r = requests.post(
        f"{YOUSIGN_API_URL}/signature_requests/{signature_request_id}/activate",
        headers=yousign_headers(), timeout=15,
    )
    if r.status_code not in (200, 201):
        log.error(f"Yousign activation échouée pour {lead_id} : {r.status_code} {r.text}")
        return

    sb_insert("contracts", {
        "lead_id": lead_id,
        "yousign_request_id": signature_request_id,
        "yousign_status": "envoye",
        "montant_centimes": int(CONTRACT_AMOUNT_EUR * 100),
    })
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=supabase_headers(),
        params={"id": f"eq.{lead_id}"},
        json={"status": "contrat_envoye"},
        timeout=10,
    )
    log.info(f"Contrat envoyé en signature à {lead['email']} (Yousign id={signature_request_id})")


# === PAIEMENT (Stripe) ===
def creer_et_envoyer_lien_paiement(lead_id: str, contract_id: str) -> None:
    """Dès le contrat signé (déclenché par le webhook Yousign), génère un lien
    de paiement Stripe et l'envoie par email à l'artisan. Aucune action manuelle."""
    from ceo_agent import send_email_prospect  # import différé : évite un cycle au chargement du module

    lead = get_lead_by_id(lead_id)
    contrats = supabase_get("contracts", f"select=*&id=eq.{contract_id}")
    if not lead or not contrats:
        log.error(f"Lien de paiement impossible : lead ou contrat introuvable ({lead_id})")
        return
    contrat = contrats[0]

    try:
        prix = stripe.Price.create(
            unit_amount=contrat["montant_centimes"],
            currency="eur",
            product_data={"name": f"Prestation Done For You — {lead.get('company', '')}"},
        )
        lien = stripe.PaymentLink.create(
            line_items=[{"price": prix.id, "quantity": 1}],
            metadata={"lead_id": lead_id, "contract_id": contract_id},
        )
    except stripe.error.StripeError as e:
        log.error(f"Erreur création lien de paiement Stripe pour {lead_id} : {e}")
        return

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contracts",
        headers=supabase_headers(),
        params={"id": f"eq.{contract_id}"},
        json={"stripe_payment_link_id": lien.id, "stripe_payment_url": lien.url},
        timeout=10,
    )
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=supabase_headers(),
        params={"id": f"eq.{lead_id}"},
        json={"status": "lien_paiement_envoye"},
        timeout=10,
    )

    corps = (
        f"Bonjour,\n\nVotre contrat est bien signé, merci !\n\n"
        f"Pour lancer la production, voici votre lien de paiement sécurisé :\n{lien.url}\n\n"
        f"Dès le paiement reçu, on démarre immédiatement.\n\nCordialement"
    )
    if send_email_prospect(
        lead["email"], f"Contrat signé — lien de paiement {lead.get('company', '')}", corps, lead_id=lead_id
    ):
        log.info(f"Lien de paiement envoyé à {lead['email']}")
    else:
        log.error(f"Échec envoi du lien de paiement à {lead['email']}")


PAGE_HTML_BASE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background: #0f172a; color: #e2e8f0;
         margin: 0; padding: 0; }}
  .conteneur {{ max-width: 640px; margin: 0 auto; padding: 40px 24px; }}
  h1 {{ color: #fff; font-size: 1.6rem; }}
  h2 {{ color: #93c5fd; font-size: 1.1rem; margin-top: 28px; }}
  p {{ line-height: 1.6; }}
  .carte {{ background: #1e293b; border-radius: 12px; padding: 24px; margin: 20px 0; }}
  .bouton {{ display: inline-block; background: #2563eb; color: #fff; text-decoration: none;
             padding: 14px 28px; border-radius: 8px; font-weight: 600; margin-top: 16px; }}
  label {{ display: block; margin-top: 16px; font-weight: 600; color: #cbd5e1; }}
  input, textarea {{ width: 100%; padding: 10px; margin-top: 6px; border-radius: 6px;
                      border: 1px solid #334155; background: #0f172a; color: #e2e8f0; box-sizing: border-box; }}
  .pitch {{ white-space: pre-wrap; color: #cbd5e1; }}
  footer {{ opacity: 0.6; font-size: 0.85rem; margin-top: 32px; }}
</style>
</head>
<body>
<div class="conteneur">
{contenu}
</div>
</body>
</html>"""


@app.get("/presentation/{lead_id}", response_class=HTMLResponse)
def voir_presentation(lead_id: str):
    """Page publique (pas de clé API : consultée directement par l'artisan
    depuis son email) présentant l'offre Done For You personnalisée."""
    lead = get_lead_by_id(lead_id)
    if not lead:
        return HTMLResponse("<h1>Présentation introuvable.</h1>", status_code=404)

    company = html.escape(lead.get("company") or "votre entreprise")
    pitch = html.escape(lead.get("pitch_commercial") or lead.get("weakness") or "")
    contenu = f"""
<h1>{html.escape(AGENCY_NAME)} — Votre projet clé en main</h1>
<div class="carte">
  <p>Bonjour <strong>{company}</strong>,</p>
  <p class="pitch">{pitch}</p>
</div>
<h2>Ce qui est inclus, sans aucune action technique de votre part :</h2>
<div class="carte">
  <p>✅ Refonte complète de votre site vitrine<br>
     ✅ Optimisation de votre fiche Google Maps / SEO local<br>
     ✅ Capture des demandes de devis directement sur le site<br>
     ✅ Aucun appel, aucune compétence technique requise de votre côté</p>
</div>
<a class="bouton" href="/intake/{lead_id}">Démarrer mon projet (2 minutes)</a>
<footer>{AGENCY_NAME} — réponse par email uniquement.</footer>
"""
    return HTMLResponse(PAGE_HTML_BASE.format(titre=f"Votre projet — {company}", contenu=contenu))


@app.get("/intake/{lead_id}", response_class=HTMLResponse)
def voir_formulaire_intake(lead_id: str):
    """Formulaire asynchrone de collecte du contenu nécessaire à la
    production du site (aucun appel : tout est déclaratif, par écrit)."""
    lead = get_lead_by_id(lead_id)
    if not lead:
        return HTMLResponse("<h1>Formulaire introuvable.</h1>", status_code=404)

    company = html.escape(lead.get("company") or "votre entreprise")
    contenu = f"""
<h1>Démarrons votre projet, {company}</h1>
<p>Ce formulaire remplace tout appel téléphonique : remplissez-le à votre rythme.</p>
<form method="post" action="/intake/{lead_id}">
  <label>Décrivez votre activité en quelques phrases</label>
  <textarea name="description" rows="4" required></textarea>

  <label>Zone d'intervention (villes/rayon)</label>
  <input type="text" name="zone_activite" placeholder="Ex : Reims et 30km alentour">

  <label>Lien vers vos photos (Google Drive, WeTransfer...)</label>
  <input type="url" name="lien_photos" placeholder="https://...">

  <label>Lien de votre site actuel (si vous en avez un)</label>
  <input type="url" name="lien_site_actuel" placeholder="https://...">

  <label>Lien de votre fiche Google Maps / Google Business Profile</label>
  <input type="url" name="lien_gbp" placeholder="https://...">

  <label>Téléphone à afficher publiquement sur le site (pas pour vous appeler)</label>
  <input type="text" name="telephone_public" placeholder="0X XX XX XX XX">

  <button class="bouton" type="submit" style="border:none; cursor:pointer;">Envoyer</button>
</form>
"""
    return HTMLResponse(PAGE_HTML_BASE.format(titre=f"Formulaire — {company}", contenu=contenu))


@app.post("/intake/{lead_id}", response_class=HTMLResponse)
def soumettre_formulaire_intake(
    lead_id: str,
    background_tasks: BackgroundTasks,
    description: str = Form(...),
    zone_activite: str = Form(""),
    lien_photos: str = Form(""),
    lien_site_actuel: str = Form(""),
    lien_gbp: str = Form(""),
    telephone_public: str = Form(""),
):
    lead = get_lead_by_id(lead_id)
    if not lead:
        return HTMLResponse("<h1>Lead introuvable.</h1>", status_code=404)

    payload = {
        "lead_id": lead_id,
        "description": description,
        "zone_activite": zone_activite,
        "lien_photos": lien_photos,
        "lien_site_actuel": lien_site_actuel,
        "lien_gbp": lien_gbp,
        "telephone_public": telephone_public,
    }
    resultat = sb_insert("intake_responses", payload)
    if resultat.get("status_code") not in (200, 201):
        log.error(f"Échec enregistrement intake pour lead {lead_id} : {resultat}")
        return HTMLResponse("<h1>Erreur lors de l'enregistrement, réessayez plus tard.</h1>", status_code=500)

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=supabase_headers(),
        params={"id": f"eq.{lead_id}"},
        json={"status": "intake_recu"},
        timeout=10,
    )

    background_tasks.add_task(envoyer_contrat_signature, lead, payload)

    contenu = """
<h1>Merci, c'est enregistré !</h1>
<div class="carte">
  <p>Nous avons bien reçu toutes les informations. On prépare votre site et
  on revient vers vous par email dès qu'il y a du nouveau — toujours sans
  appel.</p>
</div>
"""
    return HTMLResponse(PAGE_HTML_BASE.format(titre="Merci !", contenu=contenu))


# === CANAL INBOUND PARTICULIERS (conforme RGPD/CPCE — voir outbound_chantiers/) ===
# Formulaire rempli à l'initiative du particulier : c'est le SEUL canal par
# lequel un maître d'ouvrage privé arrive dans le système. Le client doit
# correspondre à une campagne ACTIVE en base (table campagnes) — une URL
# arbitraire (/devis/n-importe-quoi) ne doit jamais faire passer une page
# pour une demande officielle d'une société tierce non configurée.
def _campagne_active_par_slug(slug: str) -> str | None:
    resultats = supabase_get(
        "campagnes", f"select=nom_client&slug=eq.{quote(slug)}&statut=eq.active&limit=1"
    )
    return resultats[0]["nom_client"] if resultats else None


@app.get("/devis/{client_slug}", response_class=HTMLResponse)
def voir_formulaire_devis_public(client_slug: str):
    client_final = _campagne_active_par_slug(client_slug)
    if not client_final:
        return HTMLResponse("<h1>Page introuvable.</h1>", status_code=404)

    contenu = f"""
<h1>Demande de devis — {html.escape(client_final)}</h1>
<p>Décrivez votre projet, nous revenons vers vous rapidement.</p>
<form method="post" action="/devis/{client_slug}">
  <label>Votre nom</label>
  <input type="text" name="nom" required>

  <label>E-mail</label>
  <input type="email" name="email">

  <label>Téléphone</label>
  <input type="text" name="telephone" placeholder="0X XX XX XX XX">

  <label>Type de projet</label>
  <input type="text" name="type_projet" placeholder="Construction, rénovation lourde, extension...">

  <label>Commune du projet</label>
  <input type="text" name="commune">

  <label>Budget estimé (optionnel)</label>
  <input type="text" name="budget_estime">

  <label>Votre message</label>
  <textarea name="message" rows="4"></textarea>

  <label style="display:flex; align-items:center; gap:8px; font-weight:normal;">
    <input type="checkbox" name="consentement" value="oui" required style="width:auto;">
    J'accepte d'être recontacté(e) par {html.escape(client_final)} au sujet de ma demande.
  </label>

  <button class="bouton" type="submit" style="border:none; cursor:pointer;">Envoyer ma demande</button>
</form>
<footer>Vos informations ne sont utilisées que pour traiter cette demande de devis.</footer>
"""
    return HTMLResponse(PAGE_HTML_BASE.format(titre=f"Devis — {client_final}", contenu=contenu))


@app.post("/devis/{client_slug}", response_class=HTMLResponse)
def soumettre_devis_public(
    client_slug: str,
    nom: str = Form(...),
    email: str = Form(""),
    telephone: str = Form(""),
    type_projet: str = Form(""),
    commune: str = Form(""),
    budget_estime: str = Form(""),
    message: str = Form(""),
    consentement: str = Form(None),
):
    client_final = _campagne_active_par_slug(client_slug)
    if not client_final:
        return HTMLResponse("<h1>Page introuvable.</h1>", status_code=404)
    if not consentement:
        return HTMLResponse("<h1>Merci de cocher la case de consentement pour continuer.</h1>", status_code=400)

    payload = {
        "client_final": client_final,
        "nom": nom,
        "email": email or None,
        "telephone": telephone or None,
        "type_projet": type_projet or None,
        "commune": commune or None,
        "budget_estime": budget_estime or None,
        "message": message or None,
        "consentement": True,
    }
    resultat = sb_insert("demandes_devis_particuliers", payload)
    if resultat.get("status_code") not in (200, 201):
        log.error(f"Échec enregistrement demande de devis pour {client_final} : {resultat}")
        return HTMLResponse("<h1>Erreur lors de l'enregistrement, réessayez plus tard.</h1>", status_code=500)

    # Lead entrant chaud (le particulier vient de faire la démarche lui-même) :
    # alerte immédiate, contrairement aux prospects sortants qui suivent la
    # veille des réponses e-mail habituelle.
    alerter_discord(f"🔥 Nouvelle demande de devis entrante pour {client_final} : {nom}")

    contenu = """
<h1>Merci pour votre demande !</h1>
<div class="carte">
  <p>Nous avons bien reçu votre demande et revenons vers vous très rapidement.</p>
</div>
"""
    return HTMLResponse(PAGE_HTML_BASE.format(titre="Merci !", contenu=contenu))


# Suivi des subprocess lancés depuis /agent/trigger : gardé en mémoire dans
# ce process API (uvicorn tourne en un seul worker, cf. docker-compose.yml)
# pour que le dashboard puisse interroger /agent/status et afficher une
# vraie progression plutôt qu'un simple "lancé en arrière-plan" sans suite.
# Clé = (action, nom_client) et non juste l'action : plusieurs campagnes
# peuvent tourner en parallèle (multi-clients), il ne faut pas que le suivi
# de l'une écrase celui d'une autre. Perdu si l'API redémarre pendant qu'une
# tâche tourne — le subprocess continue mais redevient invisible.
_PROCESSUS_EN_COURS: dict[str, dict] = {}


def _cle_suivi(action: str, nom_client: str | None) -> str:
    return f"{action}::{nom_client or '_default_'}"


def _lancer_subprocess_suivi(action: str, nom_client: str | None, commande: list[str], env: dict | None = None) -> None:
    processus = subprocess.Popen(commande, env=env)
    _PROCESSUS_EN_COURS[_cle_suivi(action, nom_client)] = {
        "processus": processus, "demarre_a": datetime.now(timezone.utc),
    }


@app.get("/agent/status", dependencies=[Depends(verifier_cle_api)])
def get_agent_status(action: str, campagne: str | None = None):
    """État d'une tâche lancée via /agent/trigger — interrogé par le
    dashboard pour afficher une progression réelle plutôt qu'un simple
    message statique. `campagne` distingue les runs concurrents de
    plusieurs clients sous la même action (pipeline_pro, envoi_pro...)."""
    info = _PROCESSUS_EN_COURS.get(_cle_suivi(action, campagne))
    if not info:
        return {"action": action, "state": "jamais_lance"}

    ecoule = (datetime.now(timezone.utc) - info["demarre_a"]).total_seconds()
    code_retour = info["processus"].poll()
    if code_retour is None:
        return {"action": action, "state": "en_cours", "elapsed_seconds": round(ecoule)}
    return {
        "action": action,
        "state": "termine" if code_retour == 0 else "erreur",
        "elapsed_seconds": round(ecoule),
        "returncode": code_retour,
    }


def _construire_env_campagne(options: dict) -> dict:
    """Construit les variables d'environnement du subprocess à partir d'une
    campagne enregistrée (`options["campagne"]` = nom_client), éventuellement
    complétée par des surcharges ponctuelles (communes/types_acteur) pour ce
    run précis, SANS jamais modifier la configuration persistée en base."""
    env = os.environ.copy()
    nom_client = options.get("campagne")
    if not nom_client:
        return env

    resultats = supabase_get("campagnes", f"select=*&nom_client=eq.{quote(nom_client)}&limit=1")
    campagne = resultats[0] if resultats else None
    if not campagne:
        return env

    if options.get("communes"):
        campagne = {**campagne, "communes_cibles": options["communes"]}
    if options.get("types_acteur"):
        cles_gardees = set(options["types_acteur"])
        campagne = {
            **campagne,
            "types_acteur_cibles": [
                t for t in (campagne.get("types_acteur_cibles") or []) if t.get("cle") in cles_gardees
            ],
        }

    env["OUTBOUND_CAMPAGNE_JSON"] = json.dumps(campagne, ensure_ascii=False)
    return env


@app.post("/agent/trigger", dependencies=[Depends(verifier_cle_api)])
def trigger_agent(payload: dict, background_tasks: BackgroundTasks):
    action = payload.get("action")
    if action == "scraping":
        _lancer_subprocess_suivi("scraping", None, [sys.executable, "scraper_batiment.py"])
        return {"status": "success", "action_triggered": "scraper started"}
    if action == "leads":
        _lancer_subprocess_suivi("leads", None, [sys.executable, "lead_worker.py"])
        return {"status": "success", "action_triggered": "leads worker started"}
    if action == "mail_check":
        background_tasks.add_task(check_for_replies)
        return {"status": "success", "action_triggered": "mail check started"}
    if action == "ceo_report":
        background_tasks.add_task(run_ceo_analysis)
        return {"status": "success", "action_triggered": "ceo report started"}
    if action == "relance":
        background_tasks.add_task(relancer_prospects)
        return {"status": "success", "action_triggered": "relance started"}
    # Département "Outbound & Génération de Chantiers" (outbound_chantiers/,
    # générique multi-clients/secteurs — voir table campagnes) :
    # - pipeline_pro : modules 1-3 (sourcing -> enrichissement -> scoring)
    # - envoi_pro : module 4 seul (campagne + relances)
    # - pipeline_auto : "Pipeline Automatique", modules 1-4 enchaînés en un
    #   clic ou depuis une tâche planifiée (voir scheduler ci-dessous)
    if action in ("pipeline_pro", "envoi_pro", "pipeline_auto"):
        options = payload.get("payload", {}) or {}
        nom_client = options.get("campagne")
        env = _construire_env_campagne(options)
        commande_base = [sys.executable, "-m", "outbound_chantiers.pipeline_outbound_chantiers"]
        arguments = {
            "pipeline_pro": [],
            "envoi_pro": ["--envoi-seul"],
            "pipeline_auto": ["--avec-envoi"],
        }[action]
        _lancer_subprocess_suivi(action, nom_client, commande_base + arguments, env=env)
        return {"status": "success", "action_triggered": f"{action} started", "campagne": nom_client}
    return {"status": "error", "message": f"Unknown action: {action!r}"}


# === SUIVI DES E-MAILS (ouvertures/clics) ===
# Endpoints PUBLICS (aucune X-API-Key ni JWT : le client mail du destinataire
# ne peut évidemment pas les fournir) — pixel invisible + redirection de
# lien, voir email_tracking.py (côté envoi) et sql/init_email_tracking.sql.
# Un tracking_id invalide/forgé ne doit JAMAIS faire échouer l'affichage
# d'un e-mail ni casser un lien chez le destinataire : on ignore
# silencieusement plutôt que de renvoyer une erreur.
PIXEL_TRANSPARENT_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def _evenement_origine(tracking_id: str) -> dict | None:
    """Retrouve le contexte (lead_type/lead_id/client_final) posé par
    email_tracking.py::demarrer_tracking() au moment de l'envoi. None si
    l'identifiant est inconnu (jamais journalisé, ou forgé)."""
    resultats = supabase_get(
        "email_events",
        f"select=lead_type,lead_id,client_final&tracking_id=eq.{quote(tracking_id)}&type_evenement=eq.envoye&limit=1",
    )
    return resultats[0] if resultats else None


def _journaliser_ouverture(tracking_id: str) -> None:
    origine = _evenement_origine(tracking_id)
    if not origine:
        return
    deja_ouvert = supabase_get(
        "email_events",
        f"select=id&tracking_id=eq.{quote(tracking_id)}&type_evenement=eq.ouvert&limit=1",
    )
    sb_insert("email_events", {"tracking_id": tracking_id, "type_evenement": "ouvert", **origine})
    if not deja_ouvert:
        # Une seule alerte par envoi (première ouverture) : un client mail
        # qui recharge l'image à chaque consultation ne doit pas spammer Discord.
        alerter_discord(
            f"📬 E-mail ouvert — {origine['lead_type']} (client : {origine.get('client_final') or '—'})"
        )


@app.get("/track/open/{tracking_id}.png")
async def track_open(tracking_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(_journaliser_ouverture, tracking_id)
    return Response(content=PIXEL_TRANSPARENT_1X1, media_type="image/png")


def _journaliser_clic(tracking_id: str, url: str) -> None:
    origine = _evenement_origine(tracking_id)
    if not origine:
        return
    sb_insert("email_events", {"tracking_id": tracking_id, "type_evenement": "clique", "url_cible": url, **origine})
    alerter_discord(
        f"🖱️ Clic détecté — {origine['lead_type']} (client : {origine.get('client_final') or '—'}) → {url}"
    )


@app.get("/track/click/{tracking_id}")
async def track_click(tracking_id: str, url: str, background_tasks: BackgroundTasks):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL cible invalide")
    background_tasks.add_task(_journaliser_clic, tracking_id, url)
    return RedirectResponse(url, status_code=302)


@app.get("/email_events")
def get_email_events(
    type_evenement: str | None = None,
    client_final: str | None = None,
    limit: int = 50,
    identite: dict = Depends(obtenir_identite_dashboard),
):
    """Flux d'activité (envois/ouvertures/clics) pour le dashboard — double
    mode comme /remboursements : un client ne voit jamais que ses propres
    campagnes."""
    if identite["role"] == "client" and client_final and client_final not in identite["campagnes"]:
        raise HTTPException(status_code=403, detail="Cette campagne ne vous est pas accessible.")

    filtres = ["select=*", "order=created_at.desc", f"limit={min(limit, 200)}"]
    if type_evenement:
        filtres.append(f"type_evenement=eq.{quote(type_evenement)}")
    if client_final:
        filtres.append(f"client_final=eq.{quote(client_final)}")
    resultats = supabase_get("email_events", "&".join(filtres))

    if identite["role"] == "client":
        resultats = [e for e in resultats if e.get("client_final") in identite["campagnes"]]

    return {"email_events": resultats}


# === WEBHOOKS (Yousign / Stripe) ===
# Routes publiques (pas de X-API-Key : Yousign/Stripe ne peuvent pas la connaître).
# La sécurité repose entièrement sur la vérification de signature ci-dessous —
# ne jamais la retirer, même temporairement.
def verifier_signature_yousign(payload_brut: bytes, signature_recue: str) -> bool:
    if not YOUSIGN_WEBHOOK_SECRET:
        return False
    signature_calculee = hmac.new(YOUSIGN_WEBHOOK_SECRET.encode(), payload_brut, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_calculee, signature_recue or "")


@app.post("/webhooks/yousign")
async def webhook_yousign(request: Request, background_tasks: BackgroundTasks):
    """Déclenché par Yousign quand le contrat est signé. Passe le lead à
    'contrat_signe' puis lance la création du lien de paiement Stripe."""
    payload_brut = await request.body()
    if not verifier_signature_yousign(payload_brut, request.headers.get("X-Yousign-Signature-256", "")):
        raise HTTPException(status_code=401, detail="Signature webhook invalide")

    event = json.loads(payload_brut)
    if event.get("event_name") != "signature_request.done":
        return {"status": "ignored"}

    signature_request_id = event["data"]["signature_request"]["id"]
    contrats = supabase_get("contracts", f"select=*&yousign_request_id=eq.{signature_request_id}")
    if not contrats:
        log.warning(f"Webhook Yousign : contrat introuvable pour {signature_request_id}")
        return {"status": "contract_not_found"}

    contrat = contrats[0]
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contracts",
        headers=supabase_headers(),
        params={"id": f"eq.{contrat['id']}"},
        json={"yousign_status": "signe", "signed_at": datetime.now(timezone.utc).isoformat()},
        timeout=10,
    )
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=supabase_headers(),
        params={"id": f"eq.{contrat['lead_id']}"},
        json={"status": "contrat_signe"},
        timeout=10,
    )

    background_tasks.add_task(creer_et_envoyer_lien_paiement, contrat["lead_id"], contrat["id"])
    log.info(f"Contrat signé (Yousign id={signature_request_id}), lien de paiement en cours de génération")
    return {"status": "ok"}


@app.post("/webhooks/stripe")
async def webhook_stripe(request: Request):
    """Déclenché par Stripe quand le paiement est confirmé. Passe le lead et
    le contrat au statut 'paye'."""
    payload_brut = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload_brut, request.headers.get("stripe-signature", ""), STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=401, detail="Signature webhook Stripe invalide")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # Identification du contrat via payment_link (fiable) plutôt que via
        # metadata seul : la propagation des metadata payment_link -> session
        # n'est pas garantie selon la version d'API Stripe.
        contrats = supabase_get("contracts", f"select=*&stripe_payment_link_id=eq.{session.get('payment_link')}")
        if contrats:
            contrat = contrats[0]
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/contracts",
                headers=supabase_headers(),
                params={"id": f"eq.{contrat['id']}"},
                json={
                    "payment_status": "paye",
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                    # Indispensable pour pouvoir émettre un remboursement réel
                    # plus tard (stripe.Refund.create nécessite payment_intent
                    # ou charge) — jamais capturé jusqu'ici, voir sql/init_remboursements.sql.
                    "stripe_payment_intent_id": session.get("payment_intent"),
                },
                timeout=10,
            )
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/leads",
                headers=supabase_headers(),
                params={"id": f"eq.{contrat['lead_id']}"},
                json={"status": "paye"},
                timeout=10,
            )
            log.info(f"🎉 Paiement reçu pour lead {contrat['lead_id']}, statut -> 'paye'")
        else:
            log.warning(f"Webhook Stripe : contrat introuvable pour payment_link {session.get('payment_link')}")
    return {"status": "ok"}


@app.get("/contracts", dependencies=[Depends(verifier_cle_api)])
def get_contracts():
    """Contrats artisans (devis site vitrine, cœur de métier Expertise
    Digitale) — jamais exposés jusqu'ici via l'API, nécessaires pour piloter
    les remboursements depuis le dashboard."""
    return {"contracts": supabase_get("contracts", "select=*,leads(company,email)&order=created_at.desc&limit=100")}


# === REMBOURSEMENTS ===
# Deux origines, jamais confondues (voir sql/init_remboursements.sql) :
# - "stripe" : remboursement RÉEL d'un contrat artisan payé (de l'argent bouge) ;
# - "avoir_commercial" : crédit pour un lead B2B signalé invalide/non qualifié
#   (aucun paiement réel n'existe côté B2B à ce jour — pure écriture).
def _valider_eligibilite_remboursement_stripe(contrat: dict, montant_centimes: int) -> tuple[bool, str]:
    """Règles métier de validation automatique. Ne déclenche jamais le
    remboursement lui-même (ça reste une action explicite, voir
    /remboursements/{id}/executer) — seulement l'éligibilité."""
    if contrat.get("payment_status") != "paye":
        return False, "Le contrat n'est pas marqué comme payé."
    if not contrat.get("stripe_payment_intent_id"):
        return False, (
            "Paiement antérieur à la capture du payment_intent (voir migration "
            "sql/init_remboursements.sql) — à rembourser manuellement depuis le dashboard Stripe."
        )
    if montant_centimes > (contrat.get("montant_centimes") or 0):
        return False, "Le montant demandé dépasse le montant réellement payé sur ce contrat."
    return True, ""


@app.get("/remboursements")
def get_remboursements(
    statut: str | None = None,
    client_final: str | None = None,
    identite: dict = Depends(obtenir_identite_dashboard),
):
    """Portail Client : un compte 'client' ne voit jamais les remboursements
    Stripe des contrats artisans (sans rapport avec sa campagne — ces lignes
    n'ont d'ailleurs pas de client_final), et seulement ses propres avoirs
    commerciaux. Filtrage fait côté Python (liste de campagnes potentiellement
    contenant des espaces/points, plus simple et plus sûr qu'un filtre
    PostgREST `in.()` à échapper correctement)."""
    if identite["role"] == "client" and client_final and client_final not in identite["campagnes"]:
        raise HTTPException(status_code=403, detail="Cette campagne ne vous est pas accessible.")

    filtres = ["select=*", "order=created_at.desc", "limit=200"]
    if statut:
        filtres.append(f"statut=eq.{quote(statut)}")
    if client_final:
        filtres.append(f"client_final=eq.{quote(client_final)}")
    resultats = supabase_get("remboursements", "&".join(filtres))

    if identite["role"] == "client":
        resultats = [r for r in resultats if r.get("client_final") in identite["campagnes"]]

    return {"remboursements": resultats}


@app.post("/remboursements", dependencies=[Depends(verifier_cle_api)])
def creer_remboursement(payload: dict):
    """Crée une demande de remboursement et applique IMMÉDIATEMENT les règles
    métier de validation automatique (déclenchement/validation automatisés,
    mais jamais l'exécution du virement Stripe lui-même — voir /executer)."""
    contract_id = payload.get("contract_id")
    lead_professionnel_id = payload.get("lead_professionnel_id")
    client_final = payload.get("client_final")
    motif = (payload.get("motif") or "").strip()

    if not motif:
        raise HTTPException(status_code=400, detail="motif est requis")
    if not contract_id and not lead_professionnel_id and not client_final:
        raise HTTPException(status_code=400, detail="contract_id, lead_professionnel_id ou client_final requis")

    if contract_id:
        contrats = supabase_get("contracts", f"select=*&id=eq.{contract_id}")
        if not contrats:
            raise HTTPException(status_code=404, detail="Contrat introuvable")
        contrat = contrats[0]
        montant_centimes = payload.get("montant_centimes") or contrat.get("montant_centimes") or 0
        eligible, raison = _valider_eligibilite_remboursement_stripe(contrat, montant_centimes)

        corps = {
            "contract_id": contract_id,
            "client_final": client_final,
            "montant_centimes": montant_centimes,
            "motif": motif if eligible else f"{motif} — REJETÉ automatiquement : {raison}",
            "type_remboursement": "stripe",
            "statut": "valide" if eligible else "rejete",
            "demande_par": payload.get("demande_par") or "operateur",
        }
    else:
        corps = {
            "lead_professionnel_id": lead_professionnel_id,
            "client_final": client_final,
            "montant_centimes": payload.get("montant_centimes") or 0,
            "motif": motif,
            "type_remboursement": "avoir_commercial",
            "statut": "valide",
            "demande_par": payload.get("demande_par") or "operateur",
        }

    resultat = sb_insert("remboursements", corps)
    if resultat.get("status_code") not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Échec enregistrement remboursement : {resultat.get('response')}")
    return resultat


@app.post("/remboursements/{remboursement_id}/executer", dependencies=[Depends(verifier_cle_api)])
def executer_remboursement(remboursement_id: str):
    """Exécute RÉELLEMENT le remboursement Stripe — action explicite
    uniquement (bouton dashboard ou appel API délibéré), jamais automatique :
    un mouvement d'argent réel ne doit jamais partir sans décision humaine,
    même quand l'éligibilité a déjà été validée automatiquement à la création."""
    rembs = supabase_get("remboursements", f"select=*&id=eq.{remboursement_id}")
    if not rembs:
        raise HTTPException(status_code=404, detail="Remboursement introuvable")
    remb = rembs[0]

    if remb["type_remboursement"] != "stripe":
        raise HTTPException(
            status_code=400,
            detail="Seuls les remboursements de type 'stripe' s'exécutent ici — "
                   "un avoir commercial n'a aucun mouvement d'argent à déclencher.",
        )
    if remb["statut"] != "valide":
        raise HTTPException(
            status_code=400,
            detail=f"Statut actuel '{remb['statut']}' : seul un remboursement 'valide' peut être exécuté.",
        )

    contrats = supabase_get("contracts", f"select=*&id=eq.{remb['contract_id']}")
    contrat = contrats[0] if contrats else None
    if not contrat or not contrat.get("stripe_payment_intent_id"):
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/remboursements", headers=supabase_headers(),
            params={"id": f"eq.{remboursement_id}"}, json={"statut": "echoue"}, timeout=10,
        )
        raise HTTPException(status_code=422, detail="Contrat ou payment_intent introuvable — remboursement impossible via l'API.")

    try:
        refund = stripe.Refund.create(
            payment_intent=contrat["stripe_payment_intent_id"],
            amount=remb["montant_centimes"],
        )
    except stripe.error.StripeError as e:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/remboursements", headers=supabase_headers(),
            params={"id": f"eq.{remboursement_id}"}, json={"statut": "echoue"}, timeout=10,
        )
        log.error(f"Échec remboursement Stripe {remboursement_id} : {e}")
        raise HTTPException(status_code=502, detail=f"Échec Stripe : {e}")

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/remboursements",
        headers=supabase_headers(),
        params={"id": f"eq.{remboursement_id}"},
        json={
            "statut": "rembourse",
            "stripe_refund_id": refund.id,
            "traite_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=10,
    )
    log.info(f"💸 Remboursement Stripe exécuté : {remboursement_id} (refund={refund.id})")
    return {"status": "success", "stripe_refund_id": refund.id}


@app.post("/leads_pro/{lead_pro_id}/signaler_invalide")
def signaler_lead_pro_invalide(
    lead_pro_id: str, payload: dict, identite: dict = Depends(obtenir_identite_dashboard)
):
    """Signale un lead B2B invalide/non qualifié (garantie déjà annoncée aux
    clients, ex. échange avec S.B.G Travaux).

    Portail Client : un compte 'client' ne peut signaler que les leads de ses
    propres campagnes (403 sinon), le montant de l'avoir est TOUJOURS ignoré
    (un client ne fixe pas lui-même son propre crédit) et la demande atterrit
    en 'en_attente' — une auto-déclaration ne doit jamais s'auto-valider sans
    revue humaine. Un admin garde le comportement d'origine : montant libre,
    validation immédiate, aucune étape manuelle supplémentaire (aucun argent
    réel ne bouge de toute façon, pas de facturation B2B à ce jour)."""
    motif = (payload.get("motif") or "signalé invalide par le client").strip()

    leads_pro = supabase_get("leads_professionnels", f"select=*&id=eq.{lead_pro_id}")
    if not leads_pro:
        raise HTTPException(status_code=404, detail="Lead professionnel introuvable")
    lead_pro = leads_pro[0]

    if identite["role"] == "client" and lead_pro.get("client_final") not in identite["campagnes"]:
        raise HTTPException(status_code=403, detail="Ce lead ne vous est pas accessible.")

    est_admin = identite["role"] == "admin"
    montant_credit_centimes = payload.get("montant_credit_centimes", 0) if est_admin else 0

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads_professionnels",
        headers=supabase_headers(),
        params={"id": f"eq.{lead_pro_id}"},
        json={"signale_invalide": True, "motif_invalidite": motif},
        timeout=10,
    )

    resultat = sb_insert("remboursements", {
        "lead_professionnel_id": lead_pro_id,
        "client_final": lead_pro.get("client_final"),
        "montant_centimes": montant_credit_centimes,
        "motif": f"Avoir {'(saisi admin)' if est_admin else '(auto-signalé par le client, à valider)'} — "
                 f"lead « {lead_pro.get('nom_entreprise')} » signalé invalide : {motif}",
        "type_remboursement": "avoir_commercial",
        "statut": "valide" if est_admin else "en_attente",
        "demande_par": "admin" if est_admin else "client",
    })
    if resultat.get("status_code") not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Échec création avoir : {resultat.get('response')}")

    log.info(f"Lead pro « {lead_pro.get('nom_entreprise')} » signalé invalide (par {'admin' if est_admin else 'client'})")
    return resultat


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
