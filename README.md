# leads-creator — Expertise Digitale

Deux moteurs commerciaux dans le secteur du Bâtiment (BTP) :

1. **Marketplace de leads B2C** : des particuliers déposent une demande de devis ; le système la rapproche automatiquement d'un artisan client (abonné ou payant à l'unité) dont le métier et la zone d'intervention correspondent.
2. **Plateforme outbound B2B multi-clients** (`outbound_chantiers/`) : sourcing, enrichissement et prospection d'acteurs professionnels du bâtiment (architectes, promoteurs, maîtres d'œuvre) pour le compte de clients.

Les deux moteurs partagent la même base Supabase, le même dashboard Streamlit et la même boîte d'envoi Zoho.

## Documentation

| Besoin | Document |
|---|---|
| Comprendre le modèle économique, les offres, l'état d'avancement | `DOSSIER_PRESENTATION.md` |
| Schéma technique complet (base de données, cron, dashboard, intégrations) — **généré automatiquement, toujours à jour** | `docs/architecture_globale.md` |
| Observer le système sans rien modifier (logs, commandes, Supabase) | `guide_commandes_complet.md` |
| Structure juridique et organisationnelle | `docs/organigramme.md` |

## Architecture en un coup d'œil

- **Base de données** : Supabase (Postgres), accès serveur via clé `service_role`.
- **Dashboard** : Streamlit Community Cloud (admin + portail client) — pas de serveur à héberger soi-même.
- **Automatisations planifiées** : GitHub Actions (11 workflows, voir `.github/workflows/`) — seul ordonnanceur du projet.
- **E-mails** : Zoho Mail (SMTP envoi, IMAP relève).
- **Paiement** : Stripe (Payment Links + Refunds), webhook via une Supabase Edge Function (`supabase/functions/stripe-webhook/`).
- **IA** : Ollama en local, génère les pitchs de prospection.

## Installation (développement local)

1. Python 3.11 (voir `.python-version`).
2. Environnement virtuel et dépendances :
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copier le modèle de configuration :
   ```bash
   cp .env.example .env
   ```
   puis renseigner les vraies valeurs (Supabase, Zoho, Stripe, Discord...).
4. Lancer le dashboard en local :
   ```bash
   streamlit run dashboard/app.py
   ```

Pour un déploiement réel, le dashboard tourne sur Streamlit Community Cloud (secrets à configurer séparément, voir `dashboard/SECRETS.md`) et les automatisations sur GitHub Actions (rien à héberger).

## ⚠️ Avant de lancer quoi que ce soit manuellement

Les scripts `lead_worker.py`, `ceo_agent.py`, `livraison_devis.py` et les scripts de `outbound_chantiers/` envoient de **vrais e-mails à de vrais prospects/clients**, sans confirmation ni mode simulation. Voir `guide_commandes_complet.md` pour la façon sûre d'observer ou de déclencher une action (dashboard, `gh run`, requêtes Supabase en lecture seule).
