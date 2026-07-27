# Guide des commandes — observer le système ai-company / Expertise Digitale

*Récapitulatif pratique pour observer manuellement l'état du système, sans rien modifier.*

## Vue d'ensemble

4 conteneurs Docker orchestrés par `docker-compose.yml` :
- **`ai_api`** (port 8000) : le cerveau — API FastAPI + planificateur interne qui déclenche scraping, pitch IA, envoi/relève d'e-mails, relances, à intervalles fixes.
- **`ai_ollama`** (port 11435) : IA locale qui génère les pitchs commerciaux et rapports.
- **`ai_dashboard`** (port 8501) : interface web de pilotage (Streamlit).
- **`ai_n8n`** (port 5678) : automatisation no-code (génération de contenu SEO planifiée).

Tout communique avec Supabase (base de données distante) et Zoho (e-mails).

---

## 1. Vérifier que tout tourne

```bash
cd ~/ai-company
docker compose ps
```
Regarde la colonne `STATUS` : tu dois voir `Up ... (healthy)` pour les 4 services.

---

## 2. Suivre les logs en direct

```bash
# Tout ce que fait le cerveau (API + planificateur) en temps réel
docker logs -f ai_api

# Uniquement les actions automatiques du planificateur (cron interne)
docker logs ai_api --since 1h | grep "Cron :"

# Uniquement la veille des réponses e-mails
docker logs ai_api --since 6h | grep -i "mail_processor\|📬\|🎯\|🛑"

# Le dashboard
docker logs -f ai_dashboard
```

---

## 3. Interroger l'API directement

```bash
# Santé des dépendances (Supabase, Ollama, config Zoho/Discord)
curl -s http://localhost:8000/health | python3 -m json.tool

# Statistiques globales (nécessite la clé API du .env)
API_KEY=$(python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env')['API_SECRET_KEY'])")
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/stats | python3 -m json.tool

# Liste des leads en base
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/leads | python3 -m json.tool | less
```

---

## 4. Voir la base de données directement (Supabase)

```bash
set -a && source .env && set +a
curl -s "$SUPABASE_URL/rest/v1/leads?select=company,status,contacted,contacted_at&order=created_at.desc&limit=15" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" | python3 -m json.tool
```
Change `leads` par `contracts`, `intake_responses`, `ceo_reports` pour voir les autres tables. Tu peux aussi ouvrir directement l'interface web Supabase (Table Editor) avec l'URL de ton projet — plus confortable pour parcourir visuellement.

---

## 5. Dashboard web (Streamlit)

Ouvre **http://localhost:8501** dans ton navigateur : vue d'ensemble, leads, contenus, et un bouton pour déclencher une action manuellement (scraping, traitement leads, vérif mails, campagne CEO, relances) — c'est la façon la plus sûre de tester une action à la main.

---

## 6. n8n (automatisation contenu)

**http://localhost:5678** — identifiants dans `N8N_USER`/`N8N_PASSWORD` du `.env`. Utile pour vérifier si le workflow de génération d'article quotidien est bien actif.

---

## 7. Logs locaux sur disque (hors Docker)

```bash
tail -f ~/ai-company/cron.log          # historique long terme du planificateur
tail -f ~/ai-company/crm.log           # traitement des réponses
tail -f ~/ai-company/erreurs_envoi.log # échecs d'envoi d'e-mails
```

---

## ⚠️ Avertissements de sécurité

- **Ne jamais lancer manuellement** `python3 lead_worker.py` ou `python3 ceo_agent.py` juste "pour voir" — ces scripts envoient de **vrais e-mails à de vrais prospects** immédiatement, sans confirmation ni mode simulation.
- Pour tester une action sans risque, utiliser le bouton du dashboard (§5) ou lire d'abord les logs pour voir ce qui s'est passé lors de la dernière exécution automatique.
- La clé Supabase utilisée (`SUPABASE_KEY`) est une clé **`service_role`** : accès total lecture/écriture sur toute la base. Ne jamais l'exposer côté client/navigateur, ne jamais la coller dans un message public, un ticket, ou un dépôt Git.
- La clé `API_SECRET_KEY` protège tous les endpoints mutants de l'API : à traiter avec la même confidentialité qu'un mot de passe.
- Éviter de partager le contenu brut des commandes du §3/§4 (elles contiennent potentiellement des données personnelles de prospects) en dehors d'un usage interne.
