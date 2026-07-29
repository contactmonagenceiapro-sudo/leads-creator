# Déploiement en production (serveur distant + domaine fixe)

Ce guide remplace l'usage de ngrok par un vrai domaine, HTTPS automatique via
Caddy, et une configuration Docker durcie pour un serveur qui tourne en
continu. Il part du principe que rien n'est encore prêt (pas de serveur, pas
de domaine).

Architecture cible :

```
Internet ──HTTPS──▶ Caddy (80/443) ──▶ api:8000        (api.mondomaine.com)
                                   ├─▶ dashboard:8501   (dashboard.mondomaine.com)
                                   └─▶ n8n:5678         (n8n.mondomaine.com)
```

`api`, `dashboard`, `n8n` et `ai_ollama` ne sont plus joignables directement
depuis Internet (voir `docker-compose.yml` : leurs ports sont bindés sur
`127.0.0.1` ou, pour `ai_ollama`, plus exposés du tout) — seul Caddy expose
les ports 80/443 publiquement.

Deux fichiers Compose : `docker-compose.yml` (base, utilisé tel quel en dev
local — jamais de tentative d'obtenir un certificat HTTPS sans domaine
configuré) et `docker-compose.prod.yml` (override qui AJOUTE Caddy et la
config n8n propre à un domaine public, utilisé UNIQUEMENT en production, en
combinant les deux fichiers — voir étape 7).

## 1. Provisionner un serveur (VPS)

N'importe quel VPS Linux avec au moins **4 Go de RAM** convient (Ollama fait
tourner des modèles 3B-7B en local — en dessous de 4 Go, ça swap et c'est
inutilisable). Fournisseurs courants : Hetzner, OVH, DigitalOcean, Scaleway.
Prenez une image **Ubuntu 22.04/24.04 LTS**.

Une fois le serveur créé, vous obtenez une **adresse IP publique** (notez-la,
elle sert à la config DNS ci-dessous) et un accès SSH.

```bash
ssh root@<IP_DU_SERVEUR>
```

## 2. Acheter un nom de domaine + configurer le DNS

Achetez un domaine chez n'importe quel registrar (Namecheap, OVH, Gandi...).
Dans la zone DNS de ce domaine, ajoutez trois enregistrements **A** pointant
vers l'IP du serveur :

| Type | Nom              | Valeur           |
|------|------------------|------------------|
| A    | api              | `<IP_DU_SERVEUR>` |
| A    | dashboard        | `<IP_DU_SERVEUR>` |
| A    | n8n              | `<IP_DU_SERVEUR>` |

Ça donne `api.mondomaine.com`, `dashboard.mondomaine.com`,
`n8n.mondomaine.com`. La propagation DNS peut prendre de quelques minutes à
quelques heures — vérifiez avec `dig api.mondomaine.com +short` avant de
continuer (Caddy ne pourra pas obtenir de certificat Let's Encrypt tant que
le DNS ne pointe pas correctement vers le serveur).

## 3. Installer Docker sur le serveur

```bash
curl -fsSL https://get.docker.com | sh
# Docker Compose v2 est inclus (plugin "docker compose", sans tiret)
docker compose version
```

## 4. Pare-feu

Seuls SSH, HTTP et HTTPS doivent être joignables depuis Internet — tout le
reste passe par Caddy ou reste local au serveur :

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

## 5. Récupérer le projet sur le serveur

```bash
git clone <url-de-votre-repo> ai-company
cd ai-company
cp .env.example .env
```

## 6. Configurer `.env` pour la production

Éditez `.env` (`nano .env`) et renseignez au minimum :

- **Toutes les clés déjà utilisées en dev** (`SUPABASE_URL`, `SUPABASE_KEY`,
  `ZOHO_USER`/`ZOHO_PASSWORD`, `DISCORD_WEBHOOK_URL`, etc.) — copiez-les
  depuis votre `.env` de dev, ou régénérez-les si vous préférez des
  identifiants dédiés à la prod.
- **`API_SECRET_KEY`** : générez une valeur dédiée à la prod, différente de
  celle du dev (`openssl rand -hex 32`). Elle sert aussi de secret de
  signature JWT (`PORTAIL_JWT_SECRET`, voir `api/main.py`) — la changer
  déconnecte toutes les sessions actives, ce qui est normal ici puisqu'il
  n'y en a pas encore.
- **Section « DOMAINE DE PRODUCTION »** :
  ```
  API_DOMAIN=api.mondomaine.com
  DASHBOARD_DOMAIN=dashboard.mondomaine.com
  N8N_DOMAIN=n8n.mondomaine.com
  CADDY_ACME_EMAIL=vous@mondomaine.com
  ```
- **`PUBLIC_APP_URL`** : remplacez l'URL ngrok par `https://api.mondomaine.com`
  (sans slash final).
- **`NGROK_API_URL`** : laissez vide (aucun agent ngrok ne tourne en
  production — `ngrok_url.py` retombe automatiquement et silencieusement sur
  `PUBLIC_APP_URL`).
- **Stripe** : remplacez `STRIPE_PUBLIC_KEY`/`STRIPE_SECRET_KEY` par vos
  clés **live** (`pk_live_`/`sk_live_`) si vous voulez accepter de vrais
  paiements — les clés `sk_test_`/`pk_test_` actuelles sont en mode test
  (aucun paiement réel possible). Le webhook Stripe doit être recréé après
  le premier démarrage (étape 8) pour pointer vers le nouveau domaine —
  `STRIPE_WEBHOOK_SECRET` change à ce moment-là.
- **Yousign** : si vous utilisez la signature électronique en prod, ajoutez
  `YOUSIGN_API_URL=https://api.yousign.app/v3` (l'actuel retombe sur le
  sandbox par défaut) et une clé API Yousign de production.
- **`N8N_PASSWORD`** : changez-le pour un mot de passe fort dédié à la prod.

```bash
chmod 600 .env
```

## 7. Premier démarrage

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f caddy
```

Dans les logs de Caddy, vous devez voir l'obtention des certificats Let's
Encrypt pour les trois sous-domaines (`certificate obtained successfully`).
Si ça échoue avec une erreur DNS, revérifiez l'étape 2 (propagation DNS) et
relancez `docker compose restart caddy`.

Vérifiez ensuite :

```bash
curl https://api.mondomaine.com/health
```

Et ouvrez `https://dashboard.mondomaine.com` et `https://n8n.mondomaine.com`
dans un navigateur.

## 8. Recréer le webhook Stripe sur le nouveau domaine

L'ancien webhook (créé pour l'URL ngrok) ne fonctionne plus. Dans le
dashboard Stripe (ou via l'API), créez un nouveau webhook endpoint pointant
vers :

```
https://api.mondomaine.com/webhooks/stripe
```

Copiez le nouveau `STRIPE_WEBHOOK_SECRET` dans `.env`, puis :

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
```

## 9. Déploiements suivants

Le code est monté en bind-mount dans le conteneur `api` (`.:/app`, voir
`docker-compose.yml`) — un déploiement se résume à :

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Astuce : pour éviter de retaper `-f docker-compose.yml -f docker-compose.prod.yml`
à chaque commande sur le serveur, exportez `export
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` dans le `.bashrc` du
serveur (Docker Compose la lit automatiquement) — un simple `docker compose
up -d` suffit ensuite. À ne PAS faire sur votre machine de dev, où vous
voulez rester sur `docker-compose.yml` seul.

(`--build` reconstruit l'image si `requirements.txt` a changé ; sinon un
simple `restart api` suffit puisque le code est déjà à jour via le bind-mount).

## 10. Ce qui n'a volontairement PAS été fait

- **Rate-limiting applicatif** : aucun n'est en place sur les endpoints
  publics (`/auth/login`, `/track/*`, `/webhooks/*`...). Pour un usage à
  faible volume ça reste un risque mineur ; si le trafic augmente, ajoutez
  une limite au niveau de Caddy (module `caddy-ratelimit`, nécessite une
  image Caddy custom) plutôt que dans le code Python.
- **Utilisateur non-root dans les conteneurs** : `api`/`dashboard` tournent
  en root à l'intérieur du conteneur. Passer en non-root casserait le
  bind-mount `.:/app` (permissions fichiers hôte vs UID conteneur) sans
  bénéfice de sécurité proportionné pour ce projet (l'isolation Docker
  standard reste en place ; root dans le conteneur ≠ root sur l'hôte). À
  reconsidérer seulement si l'image `api` est un jour construite sans
  bind-mount (COPY figé + rebuild à chaque déploiement).
- **Sauvegardes** : la donnée persistante vit dans Supabase (hébergé), pas
  sur ce serveur — vérifiez le plan de backup Supabase séparément. Les
  volumes Docker locaux (`n8n_data`, `dashboard_data`, `ollama_data`) ne
  contiennent que de la configuration/du cache, pas de données critiques.
