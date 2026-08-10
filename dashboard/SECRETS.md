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

# "interne" (défaut) = signature électronique simple maison, sans coût, voir
# signature_interne.py. "yousign" = repasse sur Yousign (YOUSIGN_API_KEY ci-dessous).
SIGNATURE_PROVIDER_PAR_DEFAUT = "interne"

YOUSIGN_API_KEY = "..."
YOUSIGN_API_URL = "https://api.yousign.app/v3"  # ou api-sandbox.yousign.app en test

SEUIL_LEAD_ULTRA_QUALIFIE = "0.85"
DELAI_PREMIERE_RELANCE_JOURS = "4"
DELAI_RELANCE_SUIVANTE_JOURS = "4"
MAX_RELANCES = "2"

# Signal d'activité chantiers (Sitadel3/SDES, voir
# outbound_chantiers/signal_activite_chantiers.py) — rid du fichier "Liste
# des autorisations d'urbanisme créant des logements" sur data.statistiques.
# developpement-durable.gouv.fr (Dido), vérifié en direct le 10/08 (national,
# fonctionne aussi bien pour le Grand Est que pour Lyon). Laisser vide =
# signal désactivé, score neutre 0.5 appliqué à tous les leads.
OUTBOUND_OPENDATA_DATASET_ID = "8b35affb-55fc-4c1f-915b-7750f974446a"

# Ollama local (http://localhost:11434) injoignable depuis Streamlit Cloud —
# voir llm_config.py (racine du dépôt) : si LLM_API_URL est vide, tout appel
# à l'IA échoue proprement et repli automatiquement sur un pitch générique
# (jamais de blocage de la boucle d'envoi), mais AUCUN pitch n'est plus
# personnalisé. Pour une vraie génération IA en prod, pointer LLM_API_URL
# vers un LLM accessible publiquement (ex : un Ollama hébergé sur Render,
# ou toute API exposant POST {url}/api/generate) — LLM_API_KEY optionnel
# si cet endpoint est protégé par un jeton. Baisser OLLAMA_TIMEOUT évite
# d'attendre inutilement 90s par lead si aucune IA n'est joignable.
LLM_API_URL = ""
LLM_API_KEY = ""
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

## Autre alternative : découper une valeur trop longue (`SUPABASE_KEY_1`, `SUPABASE_KEY_2`...)

Si même le format `ENV` sur une seule ligne est rejeté ou tronqué par
l'éditeur (certaines valeurs, comme la clé `SUPABASE_KEY`, sont un très long
JWT — l'éditeur de secrets Streamlit Cloud a déjà montré des limites sur les
champs très longs), il est possible de découper n'importe quelle variable en
plusieurs, numérotées à partir de `1`, en gardant le format TOML standard :

```toml
SUPABASE_URL = "https://xxxx.supabase.co"

# SUPABASE_KEY sera reconstituée automatiquement par simple concaténation,
# dans l'ordre des numéros — coupez la valeur n'importe où, du moment que les
# morceaux mis bout à bout redonnent la clé complète.
SUPABASE_KEY_1 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIs"
SUPABASE_KEY_2 = "InJlZiI6Inh4eHgiLCJyb2xlIjoic2VydmljZV9yb2xlIiwiaWF0IjoxN..."
SUPABASE_KEY_3 = "...reste de la signature..."
```

Règles : la séquence doit commencer à `1` et ne pas avoir de trou
(`CLE_1`, `CLE_2`, `CLE_3`, jamais `CLE_1`/`CLE_3` seuls) ; si `SUPABASE_KEY`
existe aussi telle quelle en plus des `SUPABASE_KEY_N`, c'est elle qui est
utilisée (aucun conflit, juste une priorité). Ce mécanisme fonctionne pour
n'importe quelle variable, pas seulement `SUPABASE_KEY`. Voir
`dashboard/secrets_loader.py::_reassembler_cles_decoupees` pour
l'implémentation.

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
