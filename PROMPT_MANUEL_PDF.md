>
> **Comment utiliser ce fichier** : ce qui suit est un **prompt complet et autonome**, prêt à copier-coller dans n'importe quelle IA (Claude, ChatGPT, ou autre) capable de produire un document long et bien mis en forme. Il contient déjà tous les faits réels sur le système — l'IA à qui tu le donnes n'a besoin d'aucun accès au code, tout est inclus ci-dessous. Copie tout ce qui suit cette ligne, à partir de "# PROMPT" jusqu'à la fin du fichier.
>

# PROMPT — Génère mon manuel d'utilisation complet en PDF

Tu es un rédacteur technique expert, spécialisé dans la création de manuels d'utilisation clairs pour des dirigeants non-développeurs. Je suis le propriétaire d'une entreprise de prospection automatisée appelée **Expertise Digitale**. Ton unique tâche : transformer toutes les informations factuelles ci-dessous en un **document PDF unique, magnifique, extrêmement bien structuré et facile à comprendre**, qui me servira de **mode d'emploi personnel** de mon propre système.

Ne va pas chercher d'informations ailleurs, n'invente rien : utilise exclusivement les faits fournis ci-dessous. Si un point n'est pas couvert, indique clairement "non documenté" plutôt que d'inventer.

## Consignes de forme (très importantes)

- Langue : français, clair, sans jargon inutile — si un terme technique est nécessaire (API, webhook, cron, IMAP...), l'expliquer simplement dès sa première apparition.
- Structure obligatoire : page de garde, sommaire cliquable avec numéros de page, sections numérotées, en-têtes cohérents.
- Mise en page professionnelle et aérée : utilise des tableaux pour les données structurées, des blocs de code clairement délimités et lisibles pour toutes les commandes (police à chasse fixe, fond distinct), des encadrés visuellement distincts (couleur/bordure) pour tout avertissement de sécurité.
- Public cible : moi-même, dirigeant, pas développeur de formation mais à l'aise avec un terminal si on lui explique clairement quoi taper.
- Longueur : ne pas résumer à l'excès — je veux TOUT, en détail, mais organisé pour qu'on trouve une information en quelques secondes grâce au sommaire.
- Termine par un glossaire des termes techniques utilisés et par une FAQ / section dépannage.
- Le format de sortie final doit être un **PDF**, prêt à imprimer ou consulter à l'écran.

## Structure attendue du document

1. Page de garde ("Expertise Digitale — Manuel d'utilisation du système")
2. Sommaire
3. Résumé en une page : à quoi sert ce système, en langage simple
4. Vue d'ensemble de l'architecture (avec schéma/diagramme)
5. Comment fonctionne chacun des deux pipelines commerciaux, étape par étape
6. Structure des fichiers et dossiers du projet, et comment l'explorer soi-même
7. Toutes les commandes de vérification et de suivi
8. Comment déclencher une action manuellement sans risque
9. Avertissements de sécurité
10. Glossaire
11. FAQ / dépannage courant

---

## FAITS RÉELS DU SYSTÈME (base factuelle à utiliser telle quelle)

### A. Résumé du système

Expertise Digitale exploite **deux moteurs commerciaux distincts**, tous deux dans le secteur du Bâtiment (BTP) :

1. **Marketplace de leads B2C** : des particuliers déposent une demande de devis (formulaire public en ligne). Le système la rapproche automatiquement d'un artisan client (abonné mensuel ou payant à l'unité) dont le métier et la zone d'intervention correspondent — un seul artisan à la fois, jamais de diffusion simultanée. L'artisan reçoit le contact du particulier dès livraison (formule abonnement) ou après paiement d'un lien de paiement à usage unique (formule à l'unité, coordonnées révélées seulement après paiement).
2. **Plateforme de prospection B2B multi-clients** : pour le compte de clients (agences, entreprises du BTP), le système source des acteurs professionnels du bâtiment (architectes, promoteurs immobiliers, maîtres d'œuvre) actifs sur des chantiers récents, vérifie leurs coordonnées réelles, les score, puis les contacte par campagne avec relances automatiques. Chaque client a sa propre configuration (secteur, communes ciblées, types de professionnels recherchés).

Comment un artisan devient client (pipeline B2C) :
1. Repéré via des données publiques officielles (SIRENE — recherche-entreprises.api.gouv.fr), sur la Métropole de Lyon (Lyon 1er-9e + communes limitrophes : Villeurbanne, Vénissieux, Saint-Priest, Bron, Vaulx-en-Velin, Caluire-et-Cuire, Oullins-Pierre-Bénite, Rillieux-la-Pape, Écully) et une zone Grand Est (Reims, Strasbourg, Metz, Nancy, Troyes, Mulhouse, Colmar, Charleville-Mézières).
2. Vérification réelle de l'email/téléphone avant tout envoi (jamais d'adresse fabriquée, pour éviter les retours indésirables).
3. E-mail de prospection personnalisé, régénéré par IA à chaque envoi (modèle local Ollama, aucun coût par requête).
4. Envoi automatique (SMTP Zoho), avec plafond de montée en charge progressif (2 e-mails/jour au début, jusqu'à 50/jour au bout de 2 mois, partagé avec le B2B) pour protéger la réputation de la boîte d'envoi.
5. Relance automatique à J+4 puis J+8 en l'absence de réponse, puis abandon définitif (2 relances maximum).
6. Sur intérêt : formulaire de qualification (métier, communes couvertes, formule choisie), devis PDF généré automatiquement.
7. Signature électronique (méthode interne par défaut — lien à usage unique avec preuve d'IP/navigateur, ou Yousign en option) puis paiement (lien Stripe).
8. Une fois client, l'artisan devient éligible à recevoir des demandes de devis de particuliers, selon la formule choisie.

Offres actuelles :
- **B2C — à l'unité** : prix variable selon la qualité du lead (30 € Basique / 45 € Standard / 65 € Premium), coordonnées du particulier révélées après paiement.
- **B2C — abonnement mensuel** : volume inclus, livraison directe sans paiement supplémentaire (420 €/mois pour 10 leads, 760 €/mois pour 20 leads, 990 €/mois pour 30 leads).
- **B2B — sourcing d'acteurs pro pour un client** : grille indicative par type d'acteur (architectes 70 €/lead, promoteurs 60 €/lead, maîtres d'œuvre 50 €/lead), prix réel toujours négocié et saisi à la main sur chaque bon de commande.

**Statut légal** : le SIRET est en cours d'immatriculation (confirmé en base le 04/09/2026) mais pas encore obtenu — à finaliser.

**État commercial réel** : le tunnel de vente est entièrement câblé et validé par un test de bout en bout, mais **aucun client B2C n'a encore payé à ce jour** — c'est la priorité commerciale du moment, pas un problème technique.

### B. Architecture technique — plus de conteneurs Docker pour l'application

Il n'y a **plus de serveur/API à faire tourner sur mon ordinateur**. Le système repose entièrement sur des services managés :

| Composant | Rôle | Où il tourne |
|---|---|---|
| **Dashboard** | Interface web de pilotage (admin + espace client) | Streamlit Community Cloud (cloud managé) — accessible depuis n'importe quel navigateur, l'URL exacte est dans les secrets de l'app (`PUBLIC_DASHBOARD_URL`) |
| **Automatisations planifiées** | Scraping, campagnes, relances, contrôles — tout ce qui doit se déclencher à heure fixe | GitHub Actions (dans le dépôt GitHub du projet), 11 tâches planifiées au total |
| **Base de données** | Tous les prospects, contrats, demandes de devis | Supabase (cloud managé) |
| **Ollama** | Intelligence artificielle locale qui rédige les e-mails de prospection | En local sur ma machine (ou tout serveur que je désigne), pas dans un conteneur par défaut |

Le système communique aussi avec :
- **Zoho Mail** : envoi (SMTP) et réception (IMAP) des e-mails de prospection.
- **Stripe** : paiement (liens à usage unique) et remboursements ; la confirmation de paiement est automatique (webhook reçu par une fonction Supabase dédiée).
- Optionnellement : **Discord** (alertes automatiques : lead intéressé, panne détectée), **Yousign** (signature électronique alternative, actuellement en mode test/sandbox — sans valeur juridique ; la méthode utilisée par défaut est une signature électronique interne, sans coût).

*Note historique : une version antérieure de ce système tournait dans 4 conteneurs Docker locaux (API, IA, dashboard, automatisation n8n). Cette architecture a été abandonnée — le dashboard est passé sur Streamlit Community Cloud, les automatisations sur GitHub Actions, et l'outil n8n a été retiré du projet début septembre 2026, confirmé inutilisé.*

### C. Automatisations planifiées — ce qui tourne en continu, sans intervention

Déclenchées par GitHub Actions (cron), indépendamment de mon ordinateur ou du dashboard :

| Tâche automatique | Fréquence | Ce qu'elle fait |
|---|---|---|
| Campagne de prospection B2C | quotidien, 6h20 UTC | Envoie un e-mail personnalisé à chaque artisan non contacté dont l'e-mail est vérifié |
| Relance des prospects B2C | quotidien, 6h40 UTC | Relance J+4 puis J+8, puis abandon |
| Réattribution des demandes de devis | toutes les heures (:15) | Rapproche les nouvelles demandes de devis avec un artisan éligible ; réattribue celles restées sans paiement après 48h |
| Relève des mails | toutes les heures | Détecte les réponses (positives/négatives) et les retours d'erreur (bounces) |
| Traitement des paiements Stripe | toutes les 10 minutes | Confirme automatiquement un paiement reçu et déclenche la suite |
| Sourcing B2B | chaque lundi, 6h30 UTC | Recherche de nouveaux acteurs professionnels pour chaque client actif |
| Campagne + relances B2B | quotidien, 9h30 UTC | Contacte les nouveaux acteurs et relance ceux dont l'échéance est atteinte |
| Contrôle de santé de la base | quotidien, 4h30 UTC | Vérifie la sécurité et la cohérence des données, alerte en cas d'anomalie |
| Enquêtes de satisfaction | quotidien, 5h UTC | Envoyées automatiquement une semaine après le premier paiement d'un artisan |
| Contrôle des échéances | chaque lundi, 8h UTC | Alerte sur toute échéance légale/administrative à moins de 30 jours |
| Contrôle de délivrabilité | chaque lundi, 9h UTC | Alerte si le taux de rejet des e-mails dépasse 5 % |

### D. Le parcours complet d'un particulier (pipeline B2C)

```
1. Le particulier dépose une demande de devis (formulaire public en ligne)
        ↓
2. E-mail de confirmation envoyé — la demande n'est prise en compte que si
   le particulier clique le lien dans les 24h
        ↓
3. Rapprochement automatique (toutes les heures) avec un artisan client actif
   dont le métier et la zone correspondent, par ordre de rotation équitable
        ↓
4a. Formule abonnement : livraison DIRECTE des coordonnées du particulier,
    comptée dans le volume mensuel inclus
        ↓ (OU)
4b. Formule à l'unité : proposition envoyée avec un lien de paiement —
    coordonnées du particulier révélées seulement après paiement, sous 48h
    sinon la demande est proposée à un autre artisan
```

### E. Le parcours complet d'un artisan (comment il devient client, pipeline B2C)

```
1. Scraping des données publiques (SIRENE), zone Lyon + Grand Est
        ↓
2. Vérification réelle : email/téléphone valides ?
        ↓
3. Génération du message de prospection personnalisé par IA
        ↓
4. Envoi de l'e-mail (avec plafond de montée en charge quotidien)
        ↓
5. Relance à J+4 puis J+8 en l'absence de réponse (2 relances max)
        ↓
6. Si intérêt → formulaire de qualification (métier, zone couverte, formule)
        ↓
7. Devis PDF généré automatiquement → signature électronique
        ↓
8. Paiement (lien Stripe) → une fois payé, l'artisan devient client actif
   et entre dans le circuit de livraison des demandes de devis (§D)
```

### F. Structure des dossiers et fichiers du projet

Emplacement du projet sur mon ordinateur : `~/ai-company/leads-creator`.

```
leads-creator/
├── .env                          → tous les mots de passe/clés (NE JAMAIS PARTAGER CE FICHIER)
├── dashboard/
│   ├── app.py                     → l'interface web de pilotage (Streamlit)
│   ├── app_pages/                 → une page par fonction (finances, réclamations, sourcing...)
│   └── process_runner.py          → lance les scripts de fond depuis le dashboard
├── scraper_batiment.py            → va chercher les artisans sur les données publiques (SIRENE)
├── email_enricher.py              → vérifie/déduit les emails réels
├── lead_worker.py                 → génère le pitch IA et envoie le premier e-mail
├── ceo_agent.py                   → lance la campagne quotidienne
├── mail_processor.py              → surveille la boîte mail et détecte les réponses
├── relance_prospects.py           → gère les relances automatiques
├── livraison_devis.py             → cœur du tunnel : rapproche les demandes de devis avec les artisans
├── generation_contrats.py         → génère les devis/bons de commande PDF, grilles de prix
├── outbound_chantiers/            → pipeline B2B (sourcing, enrichissement, campagnes par client)
├── scripts/                       → tâches planifiées par GitHub Actions
├── sql/                           → structure complète de la base de données
├── .github/workflows/             → les 11 automatisations planifiées
└── docs/architecture_globale.md   → schéma technique complet, généré automatiquement, toujours à jour
```

Pour explorer cette structure moi-même à tout moment, dans un terminal, à la racine du projet :
```bash
cd ~/ai-company/leads-creator
ls -la                     # liste tous les fichiers et dossiers du premier niveau
find . -maxdepth 2 -not -path '*/venv*' -not -path '*/__pycache__*'
```

### G. Toutes les commandes de vérification manuelle

**1. Suivre l'exécution des automatisations (GitHub Actions)**
```bash
gh run list --limit 20                              # derniers runs, tous workflows
gh run view <run-id> --log                           # logs complets d'un run précis
gh run list --workflow=livraison_devis.yml           # runs d'un workflow donné
```
Sans l'outil `gh` installé, l'onglet **Actions** du dépôt sur github.com donne la même chose.

**2. Suivre une action lancée manuellement depuis le dashboard**
Page **"Suivi et Résultats des Actions"** du dashboard — centralise le statut et les logs de chaque action de fond lancée manuellement.

**3. Interroger le dashboard**
Ouvrir l'URL de déploiement du dashboard dans un navigateur : vue d'ensemble, et des boutons pour déclencher une action manuellement en toute sécurité.

**4. Voir la base de données directement (Supabase)**
```bash
set -a && source .env && set +a
curl -s "$SUPABASE_URL/rest/v1/leads?select=company,status,contacted,contacted_at&order=created_at.desc&limit=15" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY" | python3 -m json.tool
```
Remplacer `leads` par `contracts`, `intake_responses`, `demandes_devis_particuliers`, `leads_professionnels`, `ceo_reports` pour consulter les autres tables. L'interface web Supabase (Table Editor) permet aussi de parcourir visuellement.

### H. Comment déclencher une action manuellement, sans risque

La méthode la plus sûre : utiliser les boutons du dashboard web, section actions manuelles (scraping, traitement des leads, vérification des mails, campagne, relances). Le résultat est visible immédiatement dans la page "Suivi et Résultats des Actions".

### I. Avertissements de sécurité (à mettre en évidence visuellement dans le PDF, encadrés)

- ⚠️ **Ne jamais lancer manuellement** les commandes `python3 lead_worker.py`, `python3 ceo_agent.py` ou `python3 livraison_devis.py` "juste pour tester" : ces scripts envoient immédiatement de **vrais e-mails à de vrais prospects/clients**, sans confirmation ni simulation.
- ⚠️ Le fichier `.env` contient tous les mots de passe et clés secrètes du système (base de données, e-mail, paiement, signature électronique). Il ne doit **jamais** être partagé, copié dans un message, ou publié en ligne.
- ⚠️ La clé Supabase utilisée est une clé "service_role" : elle donne un accès total (lecture ET écriture) à toute la base de données, sécurité contournée. À traiter comme un mot de passe root.
- ⚠️ Éviter de partager en dehors d'un usage strictement personnel le résultat des commandes qui affichent des données de prospects/clients (contiennent potentiellement des données personnelles : noms, emails, téléphones).

### J. Points connus comme non finalisés (à mentionner honnêtement dans le PDF, dans une section "état d'avancement")

- Aucune structure légale définitivement immatriculée à ce jour (SIRET en cours).
- Aucun client B2C n'a encore payé à ce jour — le tunnel est prêt et testé, mais pas encore converti commercialement.
- La signature électronique Yousign (alternative optionnelle, non utilisée par défaut) fonctionne en mode test (sandbox) : sans valeur juridique tant que ce n'est pas basculé en production.
- La facturation B2B n'est pas encore enregistrée dans la base de données.
- Assurance Responsabilité Civile Professionnelle : pas encore souscrite.

---

## Instruction finale

En te basant uniquement sur les faits ci-dessus, rédige maintenant l'intégralité du manuel selon la structure demandée, puis produis-le sous forme de **PDF unique, propre, avec sommaire, mise en page soignée, et facile à comprendre pour un dirigeant non-développeur**.
