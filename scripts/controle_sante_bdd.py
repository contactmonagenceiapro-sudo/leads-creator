#!/usr/bin/env python3
"""
Contrôle de santé quotidien de la base Supabase — sécurité (couverture RLS,
fonctions exposées publiquement), opérationnel (demandes de devis bloquées,
réclamations en retard, erreurs non résolues) et dérive (croissance
anormale d'une table, données de test résiduelles, FK non indexées).

Objectif : détecter par exemple une régression RLS ou des demandes de devis
bloquées en attente de confirmation AVANT qu'un utilisateur ou un test
manuel ne les découvre par hasard (voir
sql/init_demandes_devis_particuliers_confirmation.sql pour l'incident qui a
motivé ce système).

Écrit un résultat par contrôle dans `sante_base_donnees` (voir
sql/init_sante_base_donnees.sql) à chaque exécution, affiche un résumé sur
la sortie standard, puis alerte (Discord, repli e-mail — voir alertes.py)
si au moins un contrôle est 'critique'.

Usage : python3 scripts/controle_sante_bdd.py
Déclenché quotidiennement par .github/workflows/controle_sante_bdd.yml.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

RACINE = Path(__file__).resolve().parent.parent
# alertes.py vit à la racine du dépôt, pas dans scripts/ : sans cet ajout,
# `python3 scripts/controle_sante_bdd.py` ne met que scripts/ sur sys.path
# (comportement standard de Python pour un script lancé directement), pas
# son parent — même nécessité que dashboard/app.py pour ses propres imports
# racine.
sys.path.insert(0, str(RACINE))

import alertes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

load_dotenv(RACINE / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("SUPABASE_URL/SUPABASE_KEY absents — impossible de lancer le contrôle.")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

FK_RE = re.compile(r"<fk table='([^']+)' column='([^']+)'/>")

# Tables déjà auditées manuellement en conditions réelles (voir
# sql/fix_rls_leads_kpis.sql, fix_rls_5_tables_restantes.sql,
# fix_rls_error_log.sql, fix_rls_articles.sql) au 26/08/2026. Une table hors
# de cette liste est nouvelle depuis le dernier audit humain complet : lire
# 0 ligne via la clé anon ne PROUVE pas qu'elle est protégée (RLS actif, OU
# RLS absent + table simplement vide — ambiguïté documentée dans
# fix_rls_5_tables_restantes.sql) — elle est alors seulement signalée
# 'attention' pour vérification manuelle, jamais validée 'ok' à tort.
TABLES_AUDITEES_MANUELLEMENT = {
    "agence_config", "agent_memories", "articles", "artisans", "campagnes",
    "ceo_reports", "contracts", "couts_infrastructure", "demandes_devis_particuliers",
    "echeances", "email_events", "emails_blacklistes", "error_log", "intake_responses",
    "journal_audit_admin", "kpis", "leads", "leads_professionnels", "mail_check_lock",
    "mail_check_runs", "migrations_appliquees", "propositions_expirees", "reclamations",
    "registre_suppressions_rgpd", "remboursements", "sante_base_donnees", "satisfaction_enquetes",
    "tasks", "utilisateur_campagnes", "utilisateur_leads", "utilisateurs_dashboard",
}

# Fonctions RPC déjà revues le 26/08/2026 (voir échange d'audit) — toute
# fonction supplémentaire qui apparaît dans /rpc/ est une régression
# potentielle à vérifier (nouvelle fonction SECURITY DEFINER accessible en
# théorie via l'API REST publique).
RPC_CONNUES_ET_REVUES = {
    "match_memories": "Fonction SQL légitime (recherche vectorielle agent_memories), pas un problème de sécurité.",
    "rls_auto_enable": (
        "Event trigger (RETURNS event_trigger) — apparaît dans la liste RPC de "
        "PostgREST mais Postgres refuse structurellement tout appel direct "
        "('event trigger functions can only be called as event triggers'). "
        "Revue le 26/08/2026, confirmé faux positif advisor."
    ),
}

# Fixture de test E2E connue et volontaire (voir mémoire projet : lead
# __TEST_E2E_TUNNEL__, email = l'agence elle-même) — jamais un faux positif
# à signaler, contrairement à un "VilleTestE2E999" oublié après un test.
IDENTIFIANTS_TEST_CONNUS_ET_ATTENDUS = {"__TEST_E2E_TUNNEL__"}

TABLES_SURVEILLEES_CROISSANCE = ["leads", "leads_professionnels", "demandes_devis_particuliers", "reclamations"]


def _cle_anon() -> str:
    """Même clé anon publique que celle exposée aux visiteurs
    (landing/supabase-client.js) : c'est justement CE rôle-là que RLS doit
    bloquer, donc c'est avec lui qu'il faut tester — pas de secret
    supplémentaire à gérer, cette clé est publique par design."""
    js = (RACINE / "landing" / "supabase-client.js").read_text(encoding="utf-8")
    m = re.search(r'SUPABASE_ANON_KEY = "([^"]+)"', js)
    if not m:
        raise RuntimeError("Clé anon introuvable dans landing/supabase-client.js")
    return m.group(1)


def _schema_openapi() -> dict:
    reponse = requests.get(
        SUPABASE_URL.rstrip("/") + "/rest/v1/",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/openapi+json",
        },
        timeout=30,
    )
    reponse.raise_for_status()
    return reponse.json()


def _tables_et_fks(spec: dict) -> tuple[dict[str, str | None], list[tuple[str, str]], list[str]]:
    """Retourne (table -> colonne PK ou None, liste des (table, colonne FK),
    liste des noms de fonctions exposées sous /rpc/) à partir du schéma
    PostgREST live — mêmes tags <pk/>/<fk .../> que scripts/generer_architecture.py."""
    definitions = spec.get("definitions", {})
    paths = spec.get("paths", {})
    tables_pk: dict[str, str | None] = {}
    fks: list[tuple[str, str]] = []
    for nom, definition in definitions.items():
        chemin = paths.get(f"/{nom}", {})
        if "post" not in chemin:
            continue  # vue, pas une table
        pk = None
        for col, props in definition.get("properties", {}).items():
            description = props.get("description", "")
            if "<pk/>" in description and pk is None:
                pk = col
            if FK_RE.search(description):
                fks.append((nom, col))
        tables_pk[nom] = pk
    rpc = sorted(p[len("/rpc/"):] for p in paths if p.startswith("/rpc/"))
    return tables_pk, fks, rpc


def enregistrer(resultat: dict) -> dict:
    supabase.table("sante_base_donnees").insert(
        {"type_controle": resultat["type_controle"], "statut": resultat["statut"], "detail": resultat["detail"]}
    ).execute()
    return resultat


# --- a) Couverture RLS --------------------------------------------------

# Une expression USING/WITH CHECK vide (NULL) ou littéralement 'true' (avec
# ou sans parenthèses) équivaut à "toujours vrai" — aucun filtrage réel.
_MOTIF_QUAL_TOUJOURS_VRAI = re.compile(r"^\(*\s*true\s*\)*$", re.IGNORECASE)


def _expression_toujours_vraie(expr: str | None) -> bool:
    return expr is None or bool(_MOTIF_QUAL_TOUJOURS_VRAI.match(expr.strip()))


def _politique_est_permissive(p: dict) -> bool:
    """qual (clause USING) ne s'applique jamais à un INSERT pur, et
    with_check (clause WITH CHECK) ne s'applique jamais à un SELECT/DELETE
    pur — Postgres renvoie systématiquement NULL pour la clause non
    pertinente, ce qui n'a RIEN d'une policy permissive (ex :
    artisans_select_own, cmd=SELECT, with_check=NULL par construction,
    qual correctement scopé à `auth.uid() = id`). Ne juger que la clause
    réellement pertinente pour le cmd de la policy, jamais les deux
    aveuglément."""
    cmd = (p.get("cmd") or "").upper()
    qual, with_check = p.get("qual"), p.get("with_check")
    if cmd in ("SELECT", "DELETE"):
        return _expression_toujours_vraie(qual)
    if cmd == "INSERT":
        return _expression_toujours_vraie(with_check)
    # UPDATE (qual ET with_check pertinents) ou ALL (couvre les 4 cmd) :
    # permissif si l'une ou l'autre clause pertinente laisse tout passer.
    return _expression_toujours_vraie(qual) or _expression_toujours_vraie(with_check)


def _policies_trop_permissives() -> tuple[list[dict], bool]:
    """Lit public.v_policies_rls (voir sql/init_vue_policies_rls.sql) et
    remonte toute policy accessible à public/anon dont la condition
    (USING ou WITH CHECK) n'exclut en pratique personne — exactement le
    problème rencontré sur ceo_reports (RLS activé, mais policy "Allow all"
    roles={public} qual=true qui la rendait inutile ; un simple test "RLS
    activé ?" ne peut PAS détecter ça). Renvoie (policies_suspectes,
    vue_disponible) : si la vue n'existe pas encore (migration pas encore
    appliquée), renvoie ([], False) plutôt que de faire échouer tout le
    contrôle — mais ce cas doit rester visible dans le detail plutôt que
    silencieusement confondu avec "aucune policy permissive trouvée"."""
    try:
        lignes = supabase.table("v_policies_rls").select("*").execute().data
    except Exception as e:
        log.warning(
            f"v_policies_rls indisponible ({e}) — vérification des policies RLS "
            "individuelles désactivée pour ce run. Voir sql/init_vue_policies_rls.sql."
        )
        return [], False

    suspectes = []
    for p in lignes:
        roles_bruts = p.get("roles") or []
        if isinstance(roles_bruts, str):
            roles_bruts = roles_bruts.strip("{}").split(",")
        roles = {str(r).strip().strip('"').lower() for r in roles_bruts}
        if not ({"public", "anon"} & roles):
            continue
        if _politique_est_permissive(p):
            suspectes.append(
                {
                    "table": p.get("tablename"),
                    "policy": p.get("policyname"),
                    "roles": sorted(roles),
                    "cmd": p.get("cmd"),
                    "qual": p.get("qual"),
                    "with_check": p.get("with_check"),
                }
            )
    return suspectes, True


def controler_couverture_rls(tables_pk: dict[str, str | None]) -> dict:
    anon_key = _cle_anon()
    exposees = []
    non_auditees = []
    for table, pk in sorted(tables_pk.items()):
        colonne = pk or "*"
        try:
            reponse = requests.get(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}",
                params={"select": colonne, "limit": 1},
                headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            log.error(f"Impossible de tester {table} via la clé anon : {e}")
            continue
        lignes_visibles = reponse.status_code == 200 and reponse.json()
        if lignes_visibles:
            exposees.append(table)
        elif table not in TABLES_AUDITEES_MANUELLEMENT:
            non_auditees.append(table)

    policies_permissives, vue_policies_disponible = _policies_trop_permissives()

    statut = "critique" if (exposees or policies_permissives) else ("attention" if non_auditees else "ok")
    return {
        "type_controle": "couverture_rls",
        "statut": statut,
        "detail": {
            "tables_controlees": len(tables_pk),
            "tables_exposees_via_anon": exposees,
            "tables_non_auditees_non_confirmables": non_auditees,
            "policies_trop_permissives": policies_permissives,
            "verification_policies_disponible": vue_policies_disponible,
            "note": (
                "articles (exposition découverte le 24/08/2026, corrigée par "
                "sql/fix_rls_articles.sql) fait partie des tables auditées ci-dessus. "
                "ceo_reports (27/08/2026) avait RLS activé MAIS une policy 'Allow all' "
                "(roles={public}, qual=true) qui l'annulait — d'où la vérification "
                "explicite des policies elles-mêmes, pas seulement du flag RLS."
            ),
        },
    }


# --- b) Fonctions SECURITY DEFINER exposées -----------------------------


def controler_fonctions_rpc_exposees(rpc_actuelles: list[str]) -> dict:
    nouvelles = sorted(f for f in rpc_actuelles if f not in RPC_CONNUES_ET_REVUES)
    statut = "attention" if nouvelles else "ok"
    return {
        "type_controle": "fonctions_rpc_exposees",
        "statut": statut,
        "detail": {
            "fonctions_exposees_actuellement": rpc_actuelles,
            "fonctions_deja_revues": sorted(RPC_CONNUES_ET_REVUES),
            "fonctions_nouvelles_a_revoir": nouvelles,
            "note": (
                "handle_new_artisan() n'apparaît jamais ici (RETURNS trigger, exclue "
                "structurellement par PostgREST) — normal, pas une régression."
            ),
        },
    }


# --- c) Demandes de devis bloquées --------------------------------------


def controler_demandes_devis_bloquees() -> dict:
    seuil = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    lignes = (
        supabase.table("demandes_devis_particuliers")
        .select("id,commune,corps_metier,created_at")
        .eq("statut_confirmation", "en_attente_confirmation")
        .lt("created_at", seuil)
        .execute()
        .data
    )
    statut = "critique" if lignes else "ok"
    return {
        "type_controle": "demandes_devis_bloquees",
        "statut": statut,
        "detail": {
            "seuil_heures": 48,
            "nombre": len(lignes),
            "demandes": lignes[:20],
            "note": (
                "Signal direct du bug d'email de confirmation déjà rencontré (voir "
                "sql/init_demandes_devis_particuliers_confirmation.sql) : une demande "
                "bloquée ici plus de 48h n'a jamais reçu de clic de confirmation, "
                "probablement parce que l'e-mail n'est jamais parti."
            ),
        },
    }


# --- d) Réclamations en retard -------------------------------------------


def controler_reclamations_en_retard() -> dict:
    maintenant = datetime.now(timezone.utc)
    seuil_attention = (maintenant - timedelta(days=3)).isoformat()
    seuil_critique = (maintenant - timedelta(days=7)).isoformat()
    en_attente = (
        supabase.table("reclamations")
        .select("id,type_lead,motif,date_reclamation")
        .eq("statut", "en_attente")
        .execute()
        .data
    )
    en_retard_3j = [r for r in en_attente if r["date_reclamation"] < seuil_attention]
    en_retard_7j = [r for r in en_attente if r["date_reclamation"] < seuil_critique]
    statut = "critique" if en_retard_7j else ("attention" if en_retard_3j else "ok")
    return {
        "type_controle": "reclamations_en_retard",
        "statut": statut,
        "detail": {
            "total_en_attente": len(en_attente),
            "en_retard_plus_3_jours": len(en_retard_3j),
            "en_retard_plus_7_jours_delai_contractuel_depasse": len(en_retard_7j),
            "reclamations_critiques": en_retard_7j[:20],
        },
    }


# --- e) Erreurs non résolues ----------------------------------------------


def _error_log_branchee_a_un_usage_reel() -> bool:
    """Vrai si au moins un fichier .py du projet écrit réellement dans
    error_log (pas seulement une mention en commentaire, comme le docstring
    de dashboard/pages_publiques.py qui EXPLIQUE qu'elle ne l'est pas)."""
    motif = re.compile(r"""\.table\(\s*['"]error_log['"]\s*\)\s*\.insert\(""")
    for fichier in RACINE.rglob("*.py"):
        if ".git" in fichier.parts or "__pycache__" in fichier.parts or fichier.resolve() == Path(__file__).resolve():
            continue
        try:
            if motif.search(fichier.read_text(encoding="utf-8")):
                return True
        except (UnicodeDecodeError, OSError):
            continue
    return False


def controler_erreurs_non_resolues() -> dict:
    reponse = supabase.table("error_log").select("id", count="exact").eq("resolved", False).execute()
    nombre = reponse.count or 0
    branchee = _error_log_branchee_a_un_usage_reel()
    statut = "attention" if nombre > 10 else "ok"
    return {
        "type_controle": "erreurs_non_resolues",
        "statut": statut,
        "detail": {
            "nombre_non_resolues": nombre,
            "table_branchee_a_un_usage_reel": branchee,
            "note": (
                ""
                if branchee
                else (
                    "error_log fait toujours partie du scaffolding initial du projet, "
                    "jamais branchée à un usage réel (voir sql/fix_rls_error_log.sql) — "
                    "ce contrôle restera silencieux (0 ligne) tant qu'aucun code n'écrit "
                    "dedans, ce qui n'est PAS la preuve d'une absence d'erreurs."
                )
            ),
        },
    }


# --- f) Croissance anormale d'une table -----------------------------------


def _compter(table: str) -> int:
    reponse = supabase.table(table).select("id", count="exact").limit(1).execute()
    return reponse.count or 0


def controler_croissance_tables() -> dict:
    comptes_actuels = {t: _compter(t) for t in TABLES_SURVEILLEES_CROISSANCE}

    precedent = (
        supabase.table("sante_base_donnees")
        .select("detail")
        .eq("type_controle", "croissance_table")
        .order("date_controle", desc=True)
        .limit(1)
        .execute()
        .data
    )
    comptes_precedents = precedent[0]["detail"].get("comptes", {}) if precedent else {}

    depassements = []
    for table, actuel in comptes_actuels.items():
        avant = comptes_precedents.get(table)
        if avant and avant > 0 and actuel > avant * 2:
            depassements.append({"table": table, "avant": avant, "actuel": actuel})

    statut = "attention" if depassements else "ok"
    return {
        "type_controle": "croissance_table",
        "statut": statut,
        "detail": {
            "comptes": comptes_actuels,
            "comptes_precedents": comptes_precedents,
            "tables_ayant_plus_que_double": depassements,
        },
    }


# --- g) Données de test résiduelles ---------------------------------------


def _lignes_suspectes(table: str, colonnes: list[str]) -> list[dict]:
    filtre_or = ",".join(f"{c}.ilike.*test*" for c in colonnes)
    reponse = supabase.table(table).select("id," + ",".join(colonnes)).or_(filtre_or).limit(50).execute()
    suspectes = []
    for ligne in reponse.data:
        valeurs = " ".join(str(ligne.get(c) or "") for c in colonnes)
        if any(connu in valeurs for connu in IDENTIFIANTS_TEST_CONNUS_ET_ATTENDUS):
            continue
        suspectes.append(ligne)
    return suspectes


def controler_donnees_test_residuelles() -> dict:
    cibles = {
        "leads": ["name", "company", "commune", "email"],
        "leads_professionnels": ["nom_entreprise", "commune", "email"],
        "demandes_devis_particuliers": ["nom", "commune", "email"],
    }
    trouvees = {}
    for table, colonnes in cibles.items():
        suspectes = _lignes_suspectes(table, colonnes)
        if suspectes:
            trouvees[table] = suspectes
    statut = "attention" if trouvees else "ok"
    return {
        "type_controle": "donnees_test_residuelles",
        "statut": statut,
        "detail": {
            "tables_avec_donnees_suspectes": {t: len(v) for t, v in trouvees.items()},
            "detail": trouvees,
            "exclusions_connues": sorted(IDENTIFIANTS_TEST_CONNUS_ET_ATTENDUS),
        },
    }


# --- h) FK non indexées ----------------------------------------------------


def _blocs_create_table(texte: str) -> list[tuple[str, str]]:
    """Extrait (table, corps) pour chaque CREATE TABLE du fichier, en
    comptant les parenthèses (pas une simple regex non-greedy) : le corps
    contient souvent des parenthèses imbriquées (CHECK (statut IN (...)),
    gen_random_uuid()...) qui feraient s'arrêter un `\\((.*?)\\)` bien avant
    la vraie fin de la table."""
    blocs = []
    for m in re.finditer(r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-z_]+)\s*\(", texte, re.IGNORECASE):
        debut = m.end()
        profondeur = 1
        i = debut
        while i < len(texte) and profondeur > 0:
            if texte[i] == "(":
                profondeur += 1
            elif texte[i] == ")":
                profondeur -= 1
            i += 1
        blocs.append((m.group(1), texte[debut : i - 1]))
    return blocs


_MOTIF_INDEX = re.compile(r"CREATE (?:UNIQUE )?INDEX[^;]*?ON\s+([a-z_]+)\s*\(([^)]+)\)", re.IGNORECASE)
_MOTIF_PK_COMPOSITE = re.compile(r"PRIMARY KEY\s*\(([^)]+)\)", re.IGNORECASE)
_MOTIF_PK_INLINE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s+\S.*\bPRIMARY KEY\b", re.IGNORECASE | re.MULTILINE)


def _colonnes_indexees_par_table() -> dict[str, set[str]]:
    """Colonnes couvertes par un index, qu'il soit explicite (CREATE INDEX)
    ou implicite (clé primaire — mono-colonne, ou EN TÊTE d'une clé primaire
    composite : un index B-tree sur (a, b) couvre déjà les lookups sur `a`
    seul, règle du préfixe gauche — voir utilisateur_campagnes/
    utilisateur_leads dans sql/init_portail_client.sql /
    init_utilisateur_leads.sql, sinon faussement signalées non indexées)."""
    resultat: dict[str, set[str]] = {}

    def ajouter(table: str, colonnes: set[str]) -> None:
        resultat.setdefault(table, set()).update(colonnes)

    for fichier in (RACINE / "sql").glob("*.sql"):
        texte = fichier.read_text(encoding="utf-8")

        for table, colonnes_brutes in _MOTIF_INDEX.findall(texte):
            ajouter(table, {c.strip().split()[0] for c in colonnes_brutes.split(",")})

        for table, corps in _blocs_create_table(texte):
            for m in _MOTIF_PK_COMPOSITE.finditer(corps):
                premiere_colonne = m.group(1).split(",")[0].strip()
                ajouter(table, {premiere_colonne})
            for m in _MOTIF_PK_INLINE.finditer(corps):
                ajouter(table, {m.group(1)})

    return resultat


def controler_fk_non_indexees(fks: list[tuple[str, str]]) -> dict:
    indexees = _colonnes_indexees_par_table()
    manquantes = [
        {"table": table, "colonne": colonne} for table, colonne in fks if colonne not in indexees.get(table, set())
    ]
    statut = "attention" if manquantes else "ok"
    return {
        "type_controle": "fk_non_indexees",
        "statut": statut,
        "detail": {
            "total_fk_controlees": len(fks),
            "fk_sans_index": manquantes,
            "methode": (
                "Analyse statique de sql/*.sql (pas d'accès direct au catalogue "
                "Postgres depuis ce script) — un index créé à la main dans l'éditeur "
                "Supabase sans migration versionnée ne serait pas détecté ici."
            ),
        },
    }


# --- Actions concrètes par type de contrôle (page dashboard) --------------

ACTIONS_CONCRETES = {
    "couverture_rls": "Pour tables_exposees_via_anon : activer RLS (voir modèle sql/fix_rls_articles.sql). Pour policies_trop_permissives : supprimer ou restreindre la policy listée (DROP POLICY, voir l'incident ceo_reports/sql/fix_rls_ceo_reports.sql) — RLS activé ne suffit pas si une policy roles={public|anon} avec qual/with_check vide ou 'true' la neutralise. Pour tables_non_auditees_non_confirmables : vérifier manuellement (advisors Supabase ou ligne de test factice, voir sql/fix_rls_5_tables_restantes.sql).",
    "fonctions_rpc_exposees": "Vérifier chaque fonction listée dans fonctions_nouvelles_a_revoir : si elle n'a pas besoin d'être publique, révoquer EXECUTE pour anon/authenticated (garder service_role/postgres).",
    "demandes_devis_bloquees": "Vérifier l'envoi d'e-mail de confirmation (dashboard/pages_publiques.py::_envoyer_email_confirmation_demande) et le cron livraison_devis.yml — relancer/confirmer manuellement les demandes listées si nécessaire.",
    "reclamations_en_retard": "Traiter les réclamations listées depuis la page dashboard/app_pages/reclamations.py — le délai contractuel (7 jours) est engageant vis-à-vis du client.",
    "erreurs_non_resolues": "Consulter et résoudre (ou marquer resolved=true) les lignes de error_log ; si la table n'est toujours pas branchée à un usage réel, envisager de la brancher ou de la documenter comme obsolète.",
    "croissance_table": "Vérifier les lignes récentes de la table concernée (doublon, abus, attaque) avant de considérer que c'est une simple bonne nouvelle commerciale.",
    "donnees_test_residuelles": "Nettoyer manuellement les lignes suspectes listées (hors exclusions_connues), ou les documenter comme fixture volontaire si légitime.",
    "fk_non_indexees": "Ajouter une migration sql/fix_index_<table>_<colonne>.sql (voir sql/fix_index_remboursements_lead_professionnel.sql comme modèle) pour chaque entrée listée dans fk_sans_index.",
}


def main() -> None:
    spec = _schema_openapi()
    tables_pk, fks, rpc = _tables_et_fks(spec)

    resultats = [
        enregistrer(controler_couverture_rls(tables_pk)),
        enregistrer(controler_fonctions_rpc_exposees(rpc)),
        enregistrer(controler_demandes_devis_bloquees()),
        enregistrer(controler_reclamations_en_retard()),
        enregistrer(controler_erreurs_non_resolues()),
        enregistrer(controler_croissance_tables()),
        enregistrer(controler_donnees_test_residuelles()),
        enregistrer(controler_fk_non_indexees(fks)),
    ]

    print("\n=== Contrôle de santé de la base — résumé ===")
    for r in resultats:
        icone = {"ok": "✅", "attention": "⚠️", "critique": "🚨"}[r["statut"]]
        print(f"{icone} {r['type_controle']:<28} {r['statut']:<10} {r['detail']}")

    critiques = [r for r in resultats if r["statut"] == "critique"]
    if critiques:
        lignes = "\n".join(f"- **{c['type_controle']}** : {c['detail']}" for c in critiques)
        message = f"🚨 **Contrôle santé BDD — {len(critiques)} alerte(s) critique(s)**\n{lignes}"
        if alertes.DISCORD_WEBHOOK_URL:
            alertes.alerter_discord(message)
            log.error(f"{len(critiques)} contrôle(s) critique(s) — alerte Discord envoyée.")
        else:
            log.warning(
                "DISCORD_WEBHOOK_URL non configuré — impossible d'alerter sur Discord. "
                "Repli sur l'e-mail d'alerte (voir alertes.envoyer_alerte_email). Pour "
                "activer Discord : renseigner DISCORD_WEBHOOK_URL (.env en local, secret "
                "GitHub Actions en production — voir .env.example)."
            )
            alertes.envoyer_alerte_email(
                f"🚨 Contrôle santé BDD — {len(critiques)} alerte(s) critique(s)", message
            )
            log.error(f"{len(critiques)} contrôle(s) critique(s) — alerte e-mail envoyée (repli).")
    else:
        log.info("Aucun contrôle critique.")


if __name__ == "__main__":
    main()
