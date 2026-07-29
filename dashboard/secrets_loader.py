"""
Chargement des secrets Streamlit (st.secrets) vers os.environ — point
d'entrée unique et TOLÉRANT : rien ici ne doit jamais faire planter l'app
avec un traceback brut, quelle que soit la cause (secret manquant, section
TOML imbriquée par erreur, st.secrets lui-même inaccessible...). Sur un
écran étroit (mobile), un traceback Streamlit classique est difficile à lire
et à diagnostiquer — ce module transforme systématiquement un problème de
config en message clair + arrêt propre (st.error + st.stop()), jamais une
exception qui remonte telle quelle.

Streamlit Cloud n'injecte les secrets configurés dans son interface QUE
dans st.secrets, jamais dans os.environ — mais tout le reste du projet
(scripts lancés en subprocess : ceo_agent.py, mail_processor.py...) lit sa
config via os.getenv(). Ce pont est nécessaire pour qu'un subprocess hérite
bien de ces valeurs (subprocess.Popen(..., env=os.environ.copy()) dans
process_runner.py). Sans effet en dev local (st.secrets vide si aucun
fichier .streamlit/secrets.toml n'existe).

À appeler UNE SEULE FOIS, en tout premier dans app.py — avant tout import de
auth/data_access/supabase_client (qui échoueraient de façon bien moins
lisible si SUPABASE_URL/SUPABASE_KEY manquent).
"""

import os
from collections.abc import Mapping

import streamlit as st

# Seules variables sans lesquelles l'app ne peut littéralement rien faire
# (aucune donnée n'est accessible sans Supabase, pas de mode dégradé
# possible ici contrairement à Ollama) — tout le reste (Zoho, Stripe,
# Yousign, Discord...) dégrade une fonctionnalité précise mais ne bloque
# jamais le chargement de l'app elle-même.
CLES_CRITIQUES = ("SUPABASE_URL", "SUPABASE_KEY")


def _charger_secrets_dans_environ() -> list[str]:
    """Copie st.secrets vers os.environ (setdefault : une variable déjà
    présente dans l'environnement — ex: .env en dev local — n'est jamais
    écrasée). Renvoie la liste des avertissements rencontrés (secret ignoré,
    st.secrets illisible...) — ne lève jamais d'exception."""
    avertissements: list[str] = []

    try:
        secrets_disponibles = dict(st.secrets)
    except Exception as e:
        # st.secrets peut lever si le fichier secrets.toml (local) est
        # malformé, ou dans de rares cas d'environnement d'exécution
        # incomplet — un app cassée par un secret illisible n'a aucun sens :
        # on continue avec ce qui est déjà dans os.environ (ex: .env).
        avertissements.append(f"st.secrets illisible ({e}) — repli sur les variables d'environnement déjà présentes.")
        return avertissements

    for cle, valeur in secrets_disponibles.items():
        try:
            if isinstance(valeur, Mapping):
                # Section TOML imbriquée ([section] plutôt que CLE = "valeur"
                # à plat) — SECRETS.md documente le format plat attendu.
                # Convertir bêtement en str produirait une valeur du type
                # "{'x': 'y'}" injectée silencieusement dans os.environ :
                # mieux vaut l'ignorer explicitement et le signaler.
                avertissements.append(
                    f"Secret « {cle} » ignoré : section imbriquée ([{cle}]) au lieu d'une "
                    "valeur simple — voir dashboard/SECRETS.md (format plat attendu)."
                )
                continue
            os.environ.setdefault(cle, str(valeur))
        except Exception as e:
            avertissements.append(f"Secret « {cle} » n'a pas pu être chargé : {e}")

    return avertissements


def _cles_critiques_manquantes() -> list[str]:
    return [cle for cle in CLES_CRITIQUES if not (os.getenv(cle) or "").strip()]


def initialiser_secrets() -> None:
    """À appeler en tout premier dans app.py. Charge st.secrets dans
    os.environ puis vérifie les clés critiques : si l'une d'elles manque,
    affiche un message clair (au lieu du crash cryptique qui suivrait sinon
    dans supabase_client.py) et arrête proprement le script (st.stop())."""
    avertissements = _charger_secrets_dans_environ()
    manquantes = _cles_critiques_manquantes()

    if manquantes:
        st.error(
            "⚠️ Configuration incomplète — l'application ne peut pas démarrer.\n\n"
            f"Variable(s) manquante(s) ou vide(s) : **{', '.join(manquantes)}**."
        )
        st.markdown(
            "- Sur **Streamlit Community Cloud** : App settings → Secrets "
            "(voir `dashboard/SECRETS.md` pour le format attendu).\n"
            "- En **local** : vérifie ton fichier `.env` à la racine du dépôt."
        )
        if avertissements:
            with st.expander("Détails techniques"):
                for a in avertissements:
                    st.caption(f"• {a}")
        st.stop()

    if avertissements:
        with st.expander("⚠️ Avertissements de configuration (n'empêchent pas l'app de fonctionner)"):
            for a in avertissements:
                st.caption(f"• {a}")
