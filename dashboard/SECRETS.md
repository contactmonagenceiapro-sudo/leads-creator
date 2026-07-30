# Secrets Streamlit Community Cloud

Ce dashboard n'a plus de backend séparé : il accède directement à Supabase
et lance les scripts de fond (scraping, envoi d'e-mails...) en subprocess
dans son propre environnement. Tous les secrets doivent donc être configurés
une seule fois, côté Streamlit Community Cloud (App settings → Secrets),
au format TOML.

## Pourquoi `os.environ`, pas seulement `st.secrets`

Streamlit Cloud n'injecte les secrets que dans `st.secrets`, jamais dans les
variables d'environnement du process. Mais tous les scripts lancés en
subprocess (`ceo_agent.py`, `mail_processor.py`, `scraper_batiment.py`...)
lisent leur configuration via `os.getenv()`. `dashboard/app.py` copie donc
`st.secrets` vers `os.environ` au démarrage — un subprocess hérite alors de
ces variables comme n'importe quel processus enfant.

## Secrets à renseigner

Copiez chaque variable de `.env.example` (racine du dépôt) avec sa vraie
valeur. Au minimum, en format TOML (dans l'éditeur de secrets Streamlit
Cloud) :

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "eyJ..."  # clé service_role

ZOHO_USER = "vous@votredomaine.fr"
ZOHO_PASSWORD = "..."
ZOHO_DOSSIER_BOUNCES_TRAITES = ""

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."

AGENCY_NAME = "Expertise Digitale"
AGENCY_EMAIL = "..."
CEO_EMAIL = "..."

# URL de CETTE app Streamlit une fois déployée (onglet "Share" en haut à
# droite) — utilisée dans les liens envoyés aux prospects par e-mail.
PUBLIC_DASHBOARD_URL = "https://mon-app.streamlit.app"

STRIPE_SECRET_KEY = "sk_live_..."   # ou sk_test_... en mode test
STRIPE_PUBLIC_KEY = "pk_live_..."
CONTRACT_AMOUNT_EUR = "990"

YOUSIGN_API_KEY = "..."
YOUSIGN_API_URL = "https://api.yousign.app/v3"  # ou api-sandbox.yousign.app en test

SEUIL_LEAD_ULTRA_QUALIFIE = "0.85"
DELAI_PREMIERE_RELANCE_JOURS = "4"
DELAI_RELANCE_SUIVANTE_JOURS = "4"
MAX_RELANCES = "2"

# Ollama probablement injoignable depuis Streamlit Cloud (mode dégradé
# accepté : la génération de pitch IA échoue proprement, repli sur un pitch
# générique). Baisser le timeout évite d'attendre inutilement 90s par lead.
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT = "5"
```

## Alternative si l'éditeur de secrets Streamlit "casse" le format ci-dessus

L'éditeur de secrets Streamlit Cloud est un simple champ de texte : sur
mobile en particulier, l'autocorrection peut remplacer les guillemets droits
`"` par des guillemets typographiques `"…"`, ou le collage peut perdre des
retours à la ligne — dans les deux cas, le TOML multi-lignes ci-dessus
devient illisible.

Repli possible : une seule clé `ENV`, contenant TOUTES les variables au
format `CLE=valeur` (une par ligne, **sans guillemets nécessaires** — pas du
TOML, juste du texte façon `.env`) — un seul champ, une seule paire de
guillemets, beaucoup moins de prise pour ce genre de bug. Deux façons
équivalentes de l'écrire dans l'éditeur :

**Multi-lignes** (TOML triple-guillemets) :
```toml
ENV = """
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...
ZOHO_USER=vous@votredomaine.fr
ZOHO_PASSWORD=...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
AGENCY_NAME=Expertise Digitale
PUBLIC_DASHBOARD_URL=https://mon-app.streamlit.app
"""
```

**Une seule ligne** (si même le multi-lignes pose problème — `\n` explicite,
jamais un vrai retour à la ligne tapé) :
```toml
ENV = "SUPABASE_URL=https://xxxx.supabase.co\nSUPABASE_KEY=eyJ...\nZOHO_USER=vous@votredomaine.fr"
```

Les deux formats (variables individuelles `CLE = "valeur"` ET bloc `ENV`)
peuvent coexister sans conflit — en cas de doublon, la variable individuelle
est prioritaire. Voir `dashboard/secrets_loader.py` pour l'implémentation.

## Pas de webhook à configurer

Les anciens webhooks Stripe/Yousign (backend FastAPI, supprimé) sont
remplacés par une confirmation manuelle dans le dashboard (page « Gestion &
Réponse » → section « Contrats ») : aucune URL de callback à déclarer côté
Stripe/Yousign.

## Ce qui n'est PAS dans `st.secrets`

Rien côté Streamlit Cloud ne remplace un fichier `.env` local — pour lancer
`streamlit run dashboard/app.py` sur votre poste, gardez un `.env` classique
à la racine du dépôt (copié depuis `.env.example`), chargé via
`python-dotenv` comme avant.
