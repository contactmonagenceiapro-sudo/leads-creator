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

Trois formats acceptés dans l'éditeur de secrets Streamlit Cloud (voir
dashboard/SECRETS.md pour des exemples complets) :
1. Format TOML standard, une variable par ligne : CLE = "valeur".
2. Repli à plat, une seule clé « ENV » contenant TOUTES les variables au
   format dotenv (CLE=valeur, une par ligne) — utile quand l'éditeur
   Streamlit déforme un TOML multi-lignes (guillemets substitués,
   retours à la ligne perdus au collage...) : un seul champ, une seule
   paire de guillemets, beaucoup moins de surface pour ce genre de bug.
3. Découpage d'une valeur trop longue pour un champ de l'éditeur (typique
   des clés Supabase, des JWT très longs) en plusieurs variables
   CLE_1, CLE_2, CLE_3... reconstituées ici par concaténation dans l'ordre —
   voir _reassembler_cles_decoupees().

À appeler UNE SEULE FOIS, en tout premier dans app.py — avant tout import de
auth/data_access/supabase_client (qui échoueraient de façon bien moins
lisible si SUPABASE_URL/SUPABASE_KEY manquent).
"""

import base64
import json
import os
import re
from collections.abc import Mapping

import streamlit as st

# Seules variables sans lesquelles l'app ne peut littéralement rien faire
# (aucune donnée n'est accessible sans Supabase, pas de mode dégradé
# possible ici contrairement à Ollama) — tout le reste (Zoho, Stripe,
# Yousign, Discord...) dégrade une fonctionnalité précise mais ne bloque
# jamais le chargement de l'app elle-même.
CLES_CRITIQUES = ("SUPABASE_URL", "SUPABASE_KEY")

# Nom de la clé de repli "tout-en-un" (voir docstring du module et
# dashboard/SECRETS.md) — acceptée en majuscule ou minuscule.
CLES_BLOC_ENV = ("ENV", "env")

# Reconnaît CLE_1, CLE_2... (découpage d'une valeur trop longue pour un
# champ de l'éditeur Streamlit, voir _reassembler_cles_decoupees) — le
# numéro doit être en toute fin de nom, précédé d'un underscore.
_MOTIF_CLE_DECOUPEE = re.compile(r"^(.+)_(\d+)$")


def _reassembler_cles_decoupees(secrets_disponibles: dict) -> tuple[dict[str, str], list[str]]:
    """Reconstitue les valeurs découpées en plusieurs variables CLE_1, CLE_2,
    CLE_3... (voir docstring du module, format 3) — utile quand l'éditeur de
    secrets Streamlit Cloud refuse ou tronque un champ trop long (cas vécu :
    un JWT Supabase). Une valeur découpée n'est reconstituée que si TOUTES
    les parties de 1 à N sont présentes et sont des chaînes (sinon ignorée en
    silence : ce n'est probablement pas un découpage volontaire, juste une
    variable qui se termine par un chiffre, ex. un nom se terminant par une
    année). Renvoie aussi la liste des avertissements rencontrés."""
    avertissements: list[str] = []

    # Toutes les parties matchées sont groupées ici, QUEL QUE SOIT leur type
    # — une partie non-chaîne (ex: section imbriquée par erreur) doit faire
    # échouer la reconstitution du groupe entier, pas être silencieusement
    # sautée (ce qui produirait sinon une valeur tronquée sans avertissement).
    groupes: dict[str, dict[int, object]] = {}
    for cle, valeur in secrets_disponibles.items():
        correspondance = _MOTIF_CLE_DECOUPEE.match(cle)
        if not correspondance:
            continue
        base, numero = correspondance.group(1), int(correspondance.group(2))
        groupes.setdefault(base, {})[numero] = valeur

    reconstituees: dict[str, str] = {}
    for base, parties in groupes.items():
        if base in secrets_disponibles:
            # La clé complète existe aussi telle quelle : elle est
            # prioritaire via setdefault plus loin, donc CE groupe découpé
            # est ignoré — cas piégeux (vécu : une ancienne clé anon laissée
            # en place masque silencieusement une nouvelle clé service_role
            # découpée en CLE_1/CLE_2/...) donc signalé explicitement plutôt
            # que passé sous silence.
            avertissements.append(
                f"Découpage « {base}_1, {base}_2... » ignoré : la variable "
                f"« {base} » existe déjà telle quelle et est prioritaire — "
                f"supprime-la si tu veux que le découpage soit utilisé à la place."
            )
            continue
        numeros = sorted(parties)
        if numeros != list(range(1, len(numeros) + 1)):
            # Séquence non contiguë à partir de 1 (ex: CLE_2 sans CLE_1, ou
            # un trou) — probablement pas un découpage volontaire, on
            # n'invente pas de valeur à moitié reconstruite.
            continue
        if not all(isinstance(parties[n], str) for n in numeros):
            # Une des parties n'est pas une chaîne simple (section imbriquée
            # par erreur) — on ne reconstruit pas une valeur tronquée en
            # silence.
            continue
        reconstituees[base] = "".join(parties[n] for n in numeros)

    return reconstituees, avertissements


def _parser_bloc_env(texte: str) -> dict[str, str]:
    """Parse un bloc `CLE=valeur` façon dotenv (une paire par ligne) — PAS
    du TOML : pas de guillemets requis, juste CLE=valeur. Tolère les lignes
    vides, les commentaires (#...), et des guillemets optionnels autour de
    la valeur (retirés s'ils encadrent exactement toute la valeur). Une
    ligne illisible est simplement ignorée, jamais une erreur bloquante."""
    valeurs: dict[str, str] = {}
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle = cle.strip()
        valeur = valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
            valeur = valeur[1:-1]
        if cle:
            valeurs[cle] = valeur
    return valeurs


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
            if cle in CLES_BLOC_ENV:
                # Traité séparément ci-dessous (après la boucle) : ce n'est
                # pas une variable individuelle mais un bloc dotenv complet.
                continue
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

    # Bloc de repli "tout-en-un" (voir docstring du module) : traité APRÈS
    # les clés individuelles ci-dessus, qui gardent la priorité via
    # setdefault en cas de doublon volontaire des deux formats.
    for cle_bloc in CLES_BLOC_ENV:
        bloc = secrets_disponibles.get(cle_bloc)
        if bloc is None:
            continue
        if not isinstance(bloc, str):
            avertissements.append(
                f"Secret « {cle_bloc} » ignoré : attendu comme un bloc de texte "
                "(CLE=valeur, une par ligne), pas une section imbriquée."
            )
            continue
        try:
            for cle, valeur in _parser_bloc_env(bloc).items():
                os.environ.setdefault(cle, valeur)
        except Exception as e:
            avertissements.append(f"Bloc « {cle_bloc} » illisible : {e}")

    # Découpage CLE_1/CLE_2/... (voir docstring du module, format 3) —
    # traité en dernier, avec la même priorité "setdefault" que les autres
    # formats : une variable déjà chargée (clé individuelle ou bloc ENV)
    # n'est jamais écrasée par une reconstitution.
    try:
        reconstituees, avertissements_decoupage = _reassembler_cles_decoupees(secrets_disponibles)
        avertissements.extend(avertissements_decoupage)
        for cle, valeur in reconstituees.items():
            os.environ.setdefault(cle, valeur)
    except Exception as e:
        avertissements.append(f"Reconstitution des clés découpées (CLE_1, CLE_2...) impossible : {e}")

    return avertissements


def _cles_critiques_manquantes() -> list[str]:
    return [cle for cle in CLES_CRITIQUES if not (os.getenv(cle) or "").strip()]


def _role_jwt_supabase(jwt: str) -> str | None:
    """Décode (SANS vérifier la signature — inutile ici, ce n'est qu'un
    diagnostic de configuration, jamais utilisé pour une décision de
    sécurité) le claim "role" d'une clé Supabase, qui est un JWT standard.
    Renvoie "anon", "service_role", ou None si la valeur ne ressemble pas à
    un JWT lisible (clé tronquée au copier-coller, guillemets non retirés,
    autre type de clé...)."""
    try:
        segments = jwt.split(".")
        if len(segments) != 3:
            return None
        payload_b64 = segments[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # complète le padding base64url manquant
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("role")
    except Exception:
        return None


def _url_supabase_plausible(url: str) -> bool:
    return url.startswith("https://") and ".supabase.co" in url


def _diagnostiquer_cles_supabase() -> list[str]:
    """Vérifications de CONTENU (pas seulement de présence) sur
    SUPABASE_URL/SUPABASE_KEY — spécifiquement pensées pour deux erreurs de
    copier-coller très fréquentes : coller la clé "anon" (publique) au lieu
    de "service_role" (l'app a besoin d'un accès complet en lecture/écriture,
    aucune politique RLS n'est configurée sur ces tables), ou une URL
    tronquée/mal collée. Renvoie une liste de messages BLOQUANTS (pas de
    simples avertissements) : si non vide, l'app ne doit pas continuer."""
    erreurs: list[str] = []

    url = (os.getenv("SUPABASE_URL") or "").strip()
    if url and not _url_supabase_plausible(url):
        erreurs.append(
            f"`SUPABASE_URL` ne ressemble pas à une URL Supabase valide (obtenu : `{url}`) — "
            "attendu un format `https://xxxxxxxx.supabase.co`, sans guillemets ni espace autour. "
            "Vérifie qu'elle a été copiée en entier."
        )

    cle = (os.getenv("SUPABASE_KEY") or "").strip()
    if cle:
        role = _role_jwt_supabase(cle)
        if role == "anon":
            erreurs.append(
                "`SUPABASE_KEY` contient la clé **anon** (publique), pas la clé **service_role** "
                "requise par cette app (accès complet en lecture/écriture, aucune politique RLS "
                "configurée sur les tables). Sur Supabase : Project Settings → API → copie la clé "
                "**`service_role`** (surtout pas `anon`/`public`)."
            )
        elif role is None:
            erreurs.append(
                "`SUPABASE_KEY` ne ressemble pas à une clé Supabase valide (pas un JWT lisible) — "
                "vérifie qu'elle a été copiée en entier, sans guillemets ni espace autour."
            )

    return erreurs


def initialiser_secrets() -> None:
    """À appeler en tout premier dans app.py. Charge st.secrets dans
    os.environ, vérifie que les clés critiques sont présentes ET
    plausibles (bonne forme d'URL, bon TYPE de clé Supabase), et arrête
    proprement le script (st.stop()) avec un message clair au moindre souci
    — au lieu du crash cryptique (httpx.ConnectError, 401...) qui suivrait
    sinon dans supabase_client.py, bien plus dur à diagnostiquer."""
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

    erreurs_supabase = _diagnostiquer_cles_supabase()
    if erreurs_supabase:
        st.error("⚠️ Configuration Supabase incorrecte — l'application ne peut pas démarrer.")
        for e in erreurs_supabase:
            st.markdown(f"- {e}")
        if avertissements:
            with st.expander("Détails techniques"):
                for a in avertissements:
                    st.caption(f"• {a}")
        st.stop()

    if avertissements:
        with st.expander("⚠️ Avertissements de configuration (n'empêchent pas l'app de fonctionner)"):
            for a in avertissements:
                st.caption(f"• {a}")
