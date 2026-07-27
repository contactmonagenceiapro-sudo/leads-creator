>
> **Comment utiliser ce fichier** : ce qui suit est un **prompt complet et autonome**, prêt à copier-coller dans n'importe quelle IA (Claude, ChatGPT, ou autre) capable de produire un document long et bien mis en forme. Il contient déjà tous les faits réels sur le système — l'IA à qui tu le donnes n'a besoin d'aucun accès au code, tout est inclus ci-dessous. Copie tout ce qui suit cette ligne, à partir de "# PROMPT" jusqu'à la fin du fichier.
>

# PROMPT — Génère mon manuel d'utilisation complet en PDF

Tu es un rédacteur technique expert, spécialisé dans la création de manuels d'utilisation clairs pour des dirigeants non-développeurs. Je suis le propriétaire d'une entreprise de prospection automatisée appelée **Expertise Digitale**. Ton unique tâche : transformer toutes les informations factuelles ci-dessous en un **document PDF unique, magnifique, extrêmement bien structuré et facile à comprendre**, qui me servira de **mode d'emploi personnel** de mon propre système.

Ne va pas chercher d'informations ailleurs, n'invente rien : utilise exclusivement les faits fournis ci-dessous. Si un point n'est pas couvert, indique clairement "non documenté" plutôt que d'inventer.

## Consignes de forme (très importantes)

- Langue : français, clair, sans jargon inutile — si un terme technique est nécessaire (Docker, API, webhook, IMAP...), l'expliquer simplement dès sa première apparition.
- Structure obligatoire : page de garde, sommaire cliquable avec numéros de page, sections numérotées, en-têtes cohérents.
- Mise en page professionnelle et aérée : utilise des tableaux pour les données structurées, des blocs de code clairement délimités et lisibles pour toutes les commandes (police à chair fixe, fond distinct), des encadrés visuellement distincts (couleur/bordure) pour tout avertissement de sécurité.
- Public cible : moi-même, dirigeant, pas développeur de formation mais à l'aise avec un terminal si on lui explique clairement quoi taper.
- Longueur : ne pas résumer à l'excès — je veux TOUT, en détail, mais organisé pour qu'on trouve une information en quelques secondes grâce au sommaire.
- Termine par un glossaire des termes techniques utilisés et par une FAQ / section dépannage.
- Le format de sortie final doit être un **PDF**, prêt à imprimer ou consulter à l'écran.

## Structure attendue du document

1. Page de garde ("Expertise Digitale — Manuel d'utilisation du système")
2. Sommaire
3. Résumé en une page : à quoi sert ce système, en langage simple
4. Vue d'ensemble de l'architecture (avec schéma/diagramme)
5. Comment fonctionne le pipeline commercial, étape par étape
6. Structure des fichiers et dossiers du projet, et comment l'explorer soi-même
7. Toutes les commandes de vérification et de suivi
8. Comment déclencher une action manuellement sans risque
9. Avertissements de sécurité
10. Glossaire
11. FAQ / dépannage courant

---

## FAITS RÉELS DU SYSTÈME (base factuelle à utiliser telle quelle)

### A. Résumé du système

Expertise Digitale est un système automatisé qui :
1. Repère des artisans du bâtiment (BTP) sans site web via des données publiques officielles (SIRENE — recherche-entreprises.api.gouv.fr), sur la Métropole de Lyon (Lyon 1-9e, Villeurbanne, Vénissieux, Saint-Priest, Bron, Vaulx-en-Velin, Caluire-et-Cuire, Oullins, Rillieux-la-Pape, Écully).
2. Vérifie que chaque contact (email, téléphone, domaine web) est réel avant tout envoi, pour ne jamais fabriquer une adresse ni générer de retours indésirables (bounces).
3. Génère un e-mail de prospection personnalisé par IA (modèle local Ollama `qwen2.5:7b`, aucun coût par requête).
4. Envoie cet e-mail automatiquement (SMTP Zoho), avec une pause aléatoire entre chaque envoi pour éviter d'être repéré comme spam.
5. Relance automatiquement les sans-réponse (2 relances maximum, puis arrêt définitif).
6. Surveille la boîte mail en continu pour détecter les réponses (mots-clés positifs/négatifs), et déclenche automatiquement la suite (page de présentation + formulaire) sur réponse positive.
7. Gère la suite commerciale : formulaire de qualification, devis PDF, signature électronique (Yousign), paiement (Stripe).
8. Propose aussi un service annexe : export de fichiers de prospection B2B (listes d'entreprises qualifiées, avec ou sans email vérifié) destinés à la revente.

Offres identifiées : (a) génération de leads qualifiés (paiement au contact ou abonnement, à finaliser), (b) site vitrine "Done For You" clé en main à 990 € TTC avec devis/signature/paiement intégrés, (c) revente de fichiers de prospection B2B.

**Statut légal** : aucune structure légale (SIRET, forme juridique) n'existe encore à ce jour — à formaliser en priorité car des prospects réels la demandent déjà.

### B. Architecture technique — les 4 conteneurs Docker

Le système tourne dans 4 conteneurs Docker, démarrés ensemble via `docker-compose.yml`, chacun redémarrant automatiquement en cas de panne :

| Conteneur | Rôle | Port accessible depuis mon ordinateur |
|---|---|---|
| `ai_api` | Le cerveau du système : API (FastAPI) + planificateur de tâches automatiques (scraping, envoi, relève des mails, relances) | http://localhost:8000 |
| `ai_ollama` | Intelligence artificielle locale qui rédige les e-mails de prospection et les rapports | http://localhost:11435 |
| `ai_dashboard` | Interface web de pilotage (tableau de bord) | http://localhost:8501 |
| `ai_n8n` | Outil d'automatisation no-code, utilisé pour la génération planifiée de contenu (articles) | http://localhost:5678 |

Le système communique aussi avec deux services externes :
- **Supabase** : base de données où sont stockés tous les prospects, contrats, réponses.
- **Zoho Mail** : envoi (SMTP) et réception (IMAP) des e-mails de prospection.
- Optionnellement : **Discord** (alertes automatiques : lead intéressé, panne détectée), **Yousign** (signature électronique, actuellement en mode test/sandbox — sans valeur juridique), **Stripe** (paiement).

### C. Planificateur automatique — ce qui tourne en continu, sans intervention

Une seule tâche de fond orchestre tout, à l'intérieur du conteneur `ai_api` :

| Tâche automatique | Fréquence | Ce qu'elle fait |
|---|---|---|
| Vérification des réponses e-mail | toutes les 5 minutes | Se connecte à la boîte mail, détecte les réponses positives/négatives, met à jour le prospect, alerte si besoin |
| Traitement IA des nouveaux prospects | toutes les heures | Génère le pitch commercial et l'enregistre |
| Campagne de prospection + rapport | tous les jours à 20h | Envoie les e-mails de prospection du jour + envoie un rapport de synthèse par e-mail |
| Relance des prospects sans réponse | tous les jours à 9h30 | Envoie une relance à J+4, une seconde à J+8, puis abandonne |
| Surveillance de la santé du système | toutes les 15 minutes | Vérifie que la base de données et l'IA répondent, alerte uniquement en cas de changement d'état (panne ou rétablissement) |

### D. Le parcours complet d'un prospect (pipeline)

```
1. Scraping des données publiques (SIRENE)
        ↓
2. Vérification réelle : le contact a-t-il un email/téléphone/domaine valide ?
        ↓
3. Génération du message de prospection personnalisé par IA
        ↓
4. Envoi de l'e-mail (avec pause aléatoire anti-spam)
        ↓
5. Relance à J+4 puis J+8 en l'absence de réponse (2 relances max)
        ↓
6. Si réponse positive détectée → envoi automatique d'une page de présentation
   personnalisée + d'un formulaire de qualification
        ↓
7. Le prospect remplit le formulaire → un devis PDF est généré automatiquement
        ↓
8. Envoi en signature électronique (Yousign)
        ↓
9. Envoi du lien de paiement (Stripe) → une fois payé, le contrat est actif
```

### E. Structure des dossiers et fichiers du projet

Emplacement du projet sur mon ordinateur : `~/ai-company` (`/home/mohamed/ai-company`).

```
ai-company/
├── docker-compose.yml         → définit les 4 conteneurs et comment ils communiquent
├── .env                       → tous les mots de passe/clés (NE JAMAIS PARTAGER CE FICHIER)
├── api/
│   └── main.py                → le cœur : toutes les routes web + le planificateur automatique
├── dashboard/
│   └── app.py                 → l'interface web de pilotage (Streamlit)
├── scraper_batiment.py         → va chercher les entreprises sur les données publiques (SIRENE)
├── email_enricher.py           → vérifie/déduit les emails et téléphones réels
├── lead_worker.py               → génère le pitch IA et envoie le premier e-mail
├── ceo_agent.py                 → lance la campagne du jour + envoie le rapport hebdomadaire
├── mail_processor.py            → surveille la boîte mail et détecte les réponses
├── relance_prospects.py         → gère les relances automatiques
├── export_leads_commerciaux.py  → génère les fichiers de prospection à revendre
├── sql/init.sql                 → structure complète de la base de données
├── agents/workers/               → génération d'articles de blog par IA
├── cron.log / crm.log / erreurs_envoi.log → journaux d'activité sur le disque
└── leads.json                    → fichier local temporaire de scraping (pas la base officielle)
```

Pour explorer cette structure moi-même à tout moment, dans un terminal, à la racine du projet :
```bash
cd ~/ai-company
ls -la                     # liste tous les fichiers et dossiers du premier niveau
tree -L 2                  # vue arborescente sur 2 niveaux (si l'outil "tree" est installé)
find . -maxdepth 2 -not -path '*/venv*' -not -path '*/__pycache__*'   # sinon, cette commande fait équivalent
```

### F. Toutes les commandes de vérification manuelle

**1. Est-ce que tout tourne ?**
```bash
cd ~/ai-company
docker compose ps
```
Je dois voir "Up ... (healthy)" pour les 4 services.

**2. Suivre les logs en direct**
```bash
docker logs -f ai_api                                          # tout ce que fait le cerveau, en direct
docker logs ai_api --since 1h | grep "Cron :"                   # uniquement les tâches automatiques
docker logs ai_api --since 6h | grep -i "mail_processor\|📬\|🎯\|🛑"  # uniquement la veille des réponses
docker logs -f ai_dashboard                                     # logs du tableau de bord
```

**3. Interroger l'API directement**
```bash
curl -s http://localhost:8000/health | python3 -m json.tool     # état de santé général

API_KEY=$(python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env')['API_SECRET_KEY'])")
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/stats | python3 -m json.tool   # statistiques globales
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/leads | python3 -m json.tool | less   # liste des prospects
```

**4. Voir la base de données directement (Supabase)**
```bash
set -a && source .env && set +a
curl -s "$SUPABASE_URL/rest/v1/leads?select=company,status,contacted,contacted_at&order=created_at.desc&limit=15" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" | python3 -m json.tool
```
Remplacer `leads` par `contracts`, `intake_responses` ou `ceo_reports` pour consulter les autres tables.

**5. Interface web du tableau de bord**
Ouvrir http://localhost:8501 dans un navigateur : vue d'ensemble, liste des prospects, contenus générés, et un bouton pour déclencher une action manuellement en toute sécurité.

**6. Interface n8n (automatisation de contenu)**
Ouvrir http://localhost:5678 (identifiants dans les variables `N8N_USER` / `N8N_PASSWORD` du fichier `.env`).

**7. Journaux sur le disque (en dehors de Docker)**
```bash
tail -f ~/ai-company/cron.log            # historique long terme du planificateur
tail -f ~/ai-company/crm.log             # traitement des réponses des prospects
tail -f ~/ai-company/erreurs_envoi.log   # échecs d'envoi d'e-mails
```

### G. Comment déclencher une action manuellement, sans risque

La méthode la plus sûre : utiliser le bouton "Déclencher" dans le tableau de bord web (http://localhost:8501), section "Actions de l'agent". Choisir l'action désirée (scraping, traitement des leads, vérification des mails, campagne + rapport, relances) puis cliquer.

### H. Avertissements de sécurité (à mettre en évidence visuellement dans le PDF, encadrés)

- ⚠️ **Ne jamais lancer manuellement** les commandes `python3 lead_worker.py` ou `python3 ceo_agent.py` "juste pour tester" : ces scripts envoient immédiatement de **vrais e-mails à de vrais prospects**, sans confirmation ni simulation.
- ⚠️ Le fichier `.env` contient tous les mots de passe et clés secrètes du système (base de données, e-mail, paiement, signature électronique). Il ne doit **jamais** être partagé, copié dans un message, ou publié en ligne.
- ⚠️ La clé Supabase utilisée est une clé "service_role" : elle donne un accès total (lecture ET écriture) à toute la base de données. À traiter comme un mot de passe root.
- ⚠️ La clé `API_SECRET_KEY` protège toutes les actions sensibles de l'API : même niveau de confidentialité qu'un mot de passe.
- ⚠️ Éviter de partager en dehors d'un usage strictement personnel le résultat des commandes qui affichent des données de prospects (contiennent potentiellement des données personnelles : noms, emails, téléphones).

### I. Points connus comme non finalisés (à mentionner honnêtement dans le PDF, dans une section "état d'avancement")

- La signature électronique (Yousign) fonctionne actuellement en **mode test (sandbox)** : aucune valeur juridique tant que ce n'est pas basculé en production.
- Aucune structure légale (SIRET, forme juridique) n'est encore enregistrée.
- Certaines variables de configuration (base Postgres classique, Redis) existent dans les fichiers mais ne sont reliées à aucun service actif — vestiges à ignorer ou nettoyer.
- Un enregistrement de test (`__TEST_E2E_TUNNEL__`) est présent dans les données réelles et doit être ignoré dans tout comptage.

---

## Instruction finale

En te basant uniquement sur les faits ci-dessus, rédige maintenant l'intégralité du manuel selon la structure demandée, puis produis-le sous forme de **PDF unique, propre, avec sommaire, mise en page soignée, et facile à comprendre pour un dirigeant non-développeur**.
