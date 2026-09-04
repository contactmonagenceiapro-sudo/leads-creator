# Guide des commandes — observer le système leads-creator / Expertise Digitale

*Récapitulatif pratique pour observer manuellement l'état du système, sans rien modifier.*

> Mis à jour le 04/09/2026 pour refléter l'architecture réelle actuelle (voir
> `docs/architecture_globale.md`, généré automatiquement, comme référence
> vivante). La version précédente de ce guide décrivait 4 conteneurs Docker
> (`ai_api`, `ai_ollama`, `ai_dashboard`, `ai_n8n`) avec un backend FastAPI —
> ce backend a été supprimé lors de la migration du dashboard vers Streamlit
> Community Cloud (voir `docker-compose.yml` et `dashboard/process_runner.py`,
> qui remplace `/agent/trigger`/`/agent/status`). Il n'y a plus rien à
> observer via `docker logs` pour le dashboard ou l'API.

## Vue d'ensemble

- **Dashboard** (Streamlit Community Cloud, cloud managé — pas de conteneur
  local) : interface web de pilotage, admin + portail client. Lance les
  scripts de fond (scraping, campagnes, vérif mails...) en subprocess
  directement depuis son propre environnement (`dashboard/process_runner.py`),
  sans API séparée.
- **GitHub Actions** : seul déclencheur cron du projet (11 workflows dans
  `.github/workflows/`) — voir `docs/architecture_globale.md` section 2 pour
  la liste exacte, fréquences et scripts. Streamlit Community Cloud n'a ni
  cron ni garantie de rester éveillé, d'où ce choix.
- **Ollama** : génère les pitchs commerciaux (`llm_config.py`). Tourne en
  local (`http://127.0.0.1:11434` par défaut, ou l'URL fixée par
  `OLLAMA_HOST`/`LLM_API_URL`) — plus conteneurisé par défaut.
- **n8n** (optionnel, via `docker-compose.yml`, seul service qu'il fait
  encore tourner) : prévu pour la génération planifiée de contenu SEO
  uniquement. **Ne sert plus à orchestrer le pipeline B2B** — l'automatisation
  décrite dans `outbound_chantiers/n8n_workflow_outbound_chantiers.md`
  ciblait l'ancien endpoint `/agent/trigger`, supprimé ; le pipeline B2B
  tourne désormais via `scripts/lancer_pipeline_b2b.py` + GitHub Actions (voir
  le docstring de ce script). Statut d'usage réel de n8n pour le contenu :
  non confirmé au 04/09/2026, à vérifier dans l'interface n8n si besoin.
- Tout communique avec **Supabase** (base de données) et **Zoho** (e-mails).
  Les paiements passent par **Stripe** (webhook reçu par
  `supabase/functions/stripe-webhook/`, une Edge Function — le dashboard ne
  peut pas recevoir de requête HTTP entrante).

---

## 1. Suivre l'exécution des automatisations (GitHub Actions)

```bash
# Liste des derniers runs, tous workflows confondus
gh run list --limit 20

# Détail + logs complets d'un run précis
gh run view <run-id> --log

# Ne lister que les runs d'un workflow donné
gh run list --workflow=livraison_devis.yml
```

Sans `gh` (CLI GitHub) installé, l'onglet **Actions** du dépôt sur
github.com donne la même chose : chaque run de cron y est listé avec ses
logs complets, plus fiable que d'essayer de retrouver un log local (les
runners GitHub Actions sont éphémères — rien ne persiste sur ce poste).

---

## 2. Suivre une action lancée manuellement depuis le dashboard

Le dashboard écrit les logs des jobs qu'il lance en subprocess dans
`logs/` (racine du dépôt), et centralise leur statut dans la page
**"Suivi et Résultats des Actions"** (`dashboard/app_pages/suivi_resultats.py`)
— c'est la façon la plus simple de les consulter, plutôt que d'aller chercher
le fichier `logs/*.log` correspondant à la main.

---

## 3. Interroger le dashboard Streamlit Cloud

Ouvre l'URL de déploiement (`PUBLIC_DASHBOARD_URL` dans les secrets) dans un
navigateur : vue d'ensemble, leads, et les boutons pour déclencher une action
manuellement (scraping, traitement leads, vérif mails, campagne CEO,
relances) — la façon la plus sûre de tester une action à la main.

Logs applicatifs de l'app elle-même (pas des subprocess qu'elle lance) :
onglet **"Manage app" → Logs** dans l'interface Streamlit Community Cloud
(accessible depuis le menu de l'app une fois déployée).

---

## 4. Voir la base de données directement (Supabase)

```bash
set -a && source .env && set +a
curl -s "$SUPABASE_URL/rest/v1/leads?select=company,status,contacted,contacted_at&order=created_at.desc&limit=15" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" | python3 -m json.tool
```

Change `leads` par `contracts`, `intake_responses`, `ceo_reports`,
`demandes_devis_particuliers`, `leads_professionnels`... (voir le schéma
complet dans `docs/architecture_globale.md` section 1) pour voir les autres
tables. Tu peux aussi ouvrir directement l'interface web Supabase (Table
Editor) avec l'URL de ton projet — plus confortable pour parcourir
visuellement.

---

## 5. n8n (génération de contenu, si toujours actif)

```bash
docker compose up -d n8n
```

**http://localhost:5678** — identifiants dans `N8N_USER`/`N8N_PASSWORD` du
`.env`. À vérifier dans l'interface n8n si le workflow de génération
d'article quotidien y est encore actif — ne concerne en rien le pipeline B2B
(voir plus haut).

---

## ⚠️ Avertissements de sécurité

- **Ne jamais lancer manuellement** `python3 lead_worker.py`, `python3
  ceo_agent.py` ou `python3 livraison_devis.py` juste "pour voir" — ces
  scripts envoient de **vrais e-mails à de vrais prospects/clients**
  immédiatement, sans confirmation ni mode simulation.
- Pour tester une action sans risque, utiliser le bouton du dashboard (§3)
  ou lire d'abord les logs (§1/§2) pour voir ce qui s'est passé lors de la
  dernière exécution automatique.
- La clé Supabase utilisée (`SUPABASE_KEY`) est une clé **`service_role`** :
  accès total lecture/écriture sur toute la base, RLS bypassée. Ne jamais
  l'exposer côté client/navigateur, ne jamais la coller dans un message
  public, un ticket, ou un dépôt Git.
- Éviter de partager le contenu brut des commandes du §4 (elles contiennent
  potentiellement des données personnelles de prospects/clients) en dehors
  d'un usage interne.
