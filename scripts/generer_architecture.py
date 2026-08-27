#!/usr/bin/env python3
"""
Génère docs/architecture_globale.md en interrogeant les sources de vérité
réelles du projet plutôt que de recopier un document à la main :

  - schéma de base de données : endpoint OpenAPI de PostgREST
    (SUPABASE_URL/rest/v1/, mêmes credentials service_role que
    dashboard/supabase_client.py) — colonnes, types, PK/FK réels tels
    qu'exposés en production, pas les fichiers sql/*.sql (qui peuvent avoir
    divergé, voir sql/init_migrations_appliquees.sql) ;
  - pipelines : .github/workflows/*.yml (cron GitHub Actions) + une liste
    vérifiée de déclenchements synchrones (ex. email de confirmation envoyé
    au dépôt du formulaire, pas par cron) ;
  - pages dashboard : docstrings de dashboard/app_pages/*.py ;
  - intégrations externes : imports/appels réels détectés dans le code.

Usage :
    python3 scripts/generer_architecture.py

Régénéré automatiquement par .github/workflows/generer_architecture.yml à
chaque push sur main touchant sql/**, .github/workflows/** ou
dashboard/app_pages/** — NE PAS ÉDITER docs/architecture_globale.md à la
main, il serait écrasé au prochain run.
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
DOC_SORTIE = RACINE / "docs" / "architecture_globale.md"

FK_RE = re.compile(r"<fk table='([^']+)' column='([^']+)'/>")

# Déclenchements synchrones connus (pas de cron) : vérifiés par grep sur le
# fichier/motif indiqué à chaque génération -> disparaissent automatiquement
# du document si le code change et que le motif ne matche plus (pas une
# liste figée recopiée une fois pour toutes).
DECLENCHEMENTS_SYNCHRONES = [
    {
        "nom": "Confirmation par e-mail d'une demande de devis",
        "fichier": "dashboard/pages_publiques.py",
        "motif": r"def _envoyer_email_confirmation_demande",
        "resume": (
            "Envoyée directement au dépôt du formulaire public "
            "(afficher_demande_devis / afficher_intake), PAS par cron — "
            "voir sql/init_demandes_devis_particuliers_confirmation.sql. "
            "Le rapprochement round-robin qui suit, lui, reste piloté par "
            "livraison_devis.yml (cron horaire)."
        ),
    },
]

# Intégrations externes : détectées par motif réel dans le code (pas une
# liste recopiée). `motifs` vide = présence déduite structurellement
# ailleurs (GitHub Actions déduit de la liste des workflows trouvés).
INTEGRATIONS = [
    {
        "nom": "Supabase (Postgres + Auth)",
        "motifs": [r"from supabase import", r"create_client\("],
        "role": (
            "Base de données applicative (toutes les tables métier) et "
            "authentification native pour l'espace Artisans (landing/). "
            "Accès serveur exclusivement via la clé service_role (bypass RLS)."
        ),
    },
    {
        "nom": "Stripe",
        "motifs": [r"^import stripe", r"stripe\.PaymentLink", r"stripe\.Refund"],
        "role": (
            "Lien de paiement (Payment Links) généré à la signature du "
            "contrat B2C, et remboursements (Refunds API). Confirmation de "
            "paiement 100% manuelle côté admin (pas de webhook)."
        ),
    },
    {
        "nom": "Zoho Mail (SMTP/IMAP)",
        "motifs": [r"smtplib", r"imaplib"],
        "role": "Envoi des campagnes/relances/e-mails transactionnels (SMTP) et relève des bounces/réponses (IMAP).",
    },
    {
        "nom": "Signature électronique interne",
        "motifs": [r"signature_interne"],
        "role": "Provider de signature par défaut (art. 1367 code civil, lien token + preuve IP/user-agent).",
    },
    {
        "nom": "Yousign",
        "motifs": [r"yousign", r"YOUSIGN"],
        "role": "Provider de signature électronique alternatif (sandbox), activable via SIGNATURE_PROVIDER_PAR_DEFAUT.",
    },
    {
        "nom": "API SIRENE (recherche-entreprises.api.gouv.fr)",
        "motifs": [r"recherche-entreprises\.api\.gouv\.fr", r"\bsirene\b"],
        "role": "Recherche/enrichissement d'entreprises (SIREN, adresse) — données publiques, sans clé.",
    },
    {
        "nom": "Google Places API",
        "motifs": [r"GOOGLE_PLACES_API_KEY", r"maps\.googleapis\.com"],
        "role": "Dernier recours pour trouver un téléphone d'entreprise (payant) — voir phone_enricher.py.",
    },
    {
        "nom": "DNS-over-HTTPS (dns.google)",
        "motifs": [r"dns\.google/resolve"],
        "role": "Vérification live des enregistrements SPF/DKIM/DMARC (app_pages/deliverabilite.py), gratuit.",
    },
    {
        "nom": "Discord (webhook)",
        "motifs": [r"DISCORD_WEBHOOK_URL"],
        "role": "Alertes temps réel (lead ultra-qualifié, erreurs) — voir alertes.py.",
    },
    {
        "nom": "Ollama",
        "motifs": [r"OLLAMA_HOST", r"OLLAMA_MODEL"],
        "role": "Génération des pitchs de prospection (LLM local, avec repli générique si injoignable).",
    },
]

# Modules de pilotage construits (voir section 6 de generer_markdown) — une
# ligne ajoutée à la main à la fin de la construction de chaque module,
# volontairement pas déduite du code (rien de spécifique à introspecter
# pour distinguer "module construit" d'un simple ensemble de fichiers).
MODULES_PILOTAGE: list[str] = [
    (
        "**Module 10 — Journal d'audit admin** (27/08/2026) : chaque action admin sensible "
        "(traiter une réclamation, changer un statut de vérification pro, marquer un contrat "
        "signé/payé, exécuter un remboursement) est tracée dans `journal_audit_admin` — voir "
        "`data_access.journaliser_action_admin`, page `dashboard/app_pages/journal_audit.py`."
    ),
    (
        "**Module 3 — Coûts d'infrastructure** (27/08/2026) : coûts remplis manuellement "
        "(`couts_infrastructure`, montant fixe ou % du CA) comparés au CA réel du mois "
        "(`data_access.calculer_ca_du_mois`, réutilisable par le futur module 1 finances) — "
        "page `dashboard/app_pages/couts_infrastructure.py`."
    ),
    (
        "**Module 8 — Qualité des leads** (27/08/2026) : score global calculé à la volée "
        "(`data_access.score_qualite_leads`, pas de table dédiée) — doublons potentiels "
        "(téléphone/SIREN/nom normalisé, scopés par campagne côté B2B), champs manquants sur "
        "les leads actifs (e-mail en priorité, seul canal de prospection), enrichissement B2B "
        "stagnant. Page `dashboard/app_pages/qualite_leads.py` + script CLI/cron optionnel "
        "`scripts/controle_qualite_leads.py`."
    ),
    (
        "**Module 5 — Échéances légales/administratives** (27/08/2026) : table `echeances` "
        "(récurrence optionnelle, ex. vérification mensuelle de l'usage Supabase — fusion du "
        "module 11, son API de gestion étant inaccessible avec `SUPABASE_KEY`), alerte "
        "hebdomadaire (`scripts/controle_echeances.py`, `.github/workflows/controle_echeances.yml`, "
        "lundi 8h UTC) sur toute échéance à moins de 30 jours — page "
        "`dashboard/app_pages/echeances.py`."
    ),
    (
        "**Module 7 — Qualité et délivrabilité e-mail** (27/08/2026) : taux de hard bounce sur "
        "30 jours glissants (`data_access.get_taux_bounce`, aucune nouvelle table — réutilise "
        "`email_events` et `emails_blacklistes`), ajouté à la page existante "
        "`dashboard/app_pages/deliverabilite.py` (déjà warmup + taux de réponse + DNS), alerte "
        "hebdomadaire si le taux dépasse 5 % (`scripts/controle_delivrabilite.py`, "
        "`.github/workflows/controle_delivrabilite.yml`, lundi 9h UTC). Pas de suivi "
        "ouverture/clic : le pixel de tracking a été retiré."
    ),
    (
        "**Module 1 — Finances** (27/08/2026) : CA/MRR/répartitions B2C déjà entièrement "
        "construits (`dashboard/app_pages/finances.py`, `finances_calc.py`, "
        "`data_access.get_contracts_finances`) mais retirés de la navigation le 17/08 sur "
        "suspicion d'ImportError en production — réactivés après retest en réel contre la "
        "prod n'ayant rien reproduit (décalage de cache Streamlit Cloud, pas un bug de code). "
        "B2B hors périmètre : aucune facturation B2B persistée en base à ce jour."
    ),
    (
        "**Module 2 — Pipeline de conversion** (27/08/2026) : répartition B2C/B2B par statut "
        "actuel + taux de contact/intérêt/signature (`data_access.get_pipeline_conversion`, "
        "aucune nouvelle table). Photo de l'état courant, pas une cohorte temporelle — "
        "leads.status/leads_professionnels.statut sont écrasés à chaque changement d'étape, "
        "aucun historique en base (approche confirmée par l'utilisateur). Pas d'alerte "
        "automatique : pas de seuil \"anormalement bas\" fiable sur le volume actuel. Page "
        "`dashboard/app_pages/pipeline_conversion.py`."
    ),
    (
        "**Module 9 — Performance des artisans** (27/08/2026) : formule à l'unité uniquement. "
        "Table `propositions_expirees` (voir sql/init_propositions_expirees.sql) journalise "
        "chaque proposition expirée sans action — nécessite une petite modification de "
        "`livraison_devis.py::expirer_propositions_perimees()` (une info calculée puis jetée "
        "à chaque run devient persistée). Combinée aux livraisons payées "
        "(`data_access.get_performance_artisans`) : taux de réactivité et délai moyen de "
        "paiement par artisan — page `dashboard/app_pages/performance_artisans.py`."
    ),
]


def recuperer_schema_supabase() -> dict:
    load_dotenv(RACINE / ".env")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print(
            "SUPABASE_URL/SUPABASE_KEY absents de l'environnement — impossible "
            "de générer le schéma base de données.",
            file=sys.stderr,
        )
        sys.exit(1)
    reponse = requests.get(
        url.rstrip("/") + "/rest/v1/",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/openapi+json",
        },
        timeout=30,
    )
    reponse.raise_for_status()
    return reponse.json()


def analyser_schema(spec: dict) -> tuple[dict, list[str]]:
    """Sépare tables (exposent POST en plus de GET) des vues (GET seul), et
    extrait PK/FK depuis la description PostgREST de chaque colonne."""
    definitions = spec.get("definitions", {})
    paths = spec.get("paths", {})
    tables: dict[str, dict] = {}
    vues: list[str] = []
    for nom, definition in definitions.items():
        chemin = paths.get(f"/{nom}", {})
        if "post" not in chemin:
            vues.append(nom)
            continue
        colonnes = []
        fks = []
        for col, props in definition.get("properties", {}).items():
            type_col = props.get("format") or props.get("type") or "text"
            description = props.get("description", "")
            est_pk = "<pk/>" in description
            m = FK_RE.search(description)
            if m:
                fks.append((col, m.group(1), m.group(2)))
            colonnes.append((col, type_col, est_pk))
        tables[nom] = {"colonnes": colonnes, "fks": fks}
    return tables, sorted(vues)


def _identifiant_mermaid(texte: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", texte)


def generer_mermaid_er(tables: dict) -> str:
    lignes = ["erDiagram"]
    for nom, info in sorted(tables.items()):
        lignes.append(f"    {nom} {{")
        for col, type_col, est_pk in info["colonnes"]:
            type_mermaid = _identifiant_mermaid(type_col) or "text"
            suffixe = " PK" if est_pk else ""
            lignes.append(f"        {type_mermaid} {col}{suffixe}")
        lignes.append("    }")
    for nom, info in sorted(tables.items()):
        for col, table_cible, _col_cible in info["fks"]:
            if table_cible in tables:
                lignes.append(f'    {table_cible} ||--o{{ {nom} : "{col}"')
    return "\n".join(lignes)


def scanner_workflows() -> list[dict]:
    resultats = []
    dossier = RACINE / ".github" / "workflows"
    if not dossier.exists():
        return resultats
    for fichier in sorted(dossier.glob("*.yml")):
        if fichier.name == "generer_architecture.yml":
            continue  # le workflow de génération lui-même, pas une automatisation métier
        texte = fichier.read_text(encoding="utf-8")
        nom_m = re.search(r"^name:\s*(.+)$", texte, re.MULTILINE)
        nom = nom_m.group(1).strip() if nom_m else fichier.name
        crons = re.findall(r'cron:\s*"([^"]+)"', texte)
        script_m = re.search(r"run:\s*python3\s+(\S+)", texte)
        script = script_m.group(1) if script_m else "?"

        # Reconstitue le premier paragraphe du bloc de commentaires d'en-tête
        # (les fichiers de ce projet enveloppent leurs commentaires sur
        # plusieurs lignes à ~80 colonnes ; les paragraphes sont séparés par
        # une ligne "#" vide) plutôt que de ne garder que la première ligne
        # physique, tronquée en plein milieu d'une phrase.
        commentaires_bruts = []
        en_bloc = False
        for ligne in texte.splitlines()[1:]:
            s = ligne.strip()
            if s.startswith("#"):
                en_bloc = True
                commentaires_bruts.append(s.lstrip("#").strip())
            elif en_bloc:
                break
        paragraphe = []
        for c in commentaires_bruts:
            if c == "" and paragraphe:
                break
            if c:
                paragraphe.append(c)
        resume = " ".join(paragraphe)

        resultats.append(
            {
                "fichier": fichier.name,
                "nom": nom,
                "crons": crons,
                "script": script,
                "resume": resume,
            }
        )
    return resultats


def verifier_declenchements_synchrones() -> list[dict]:
    confirmes = []
    for entree in DECLENCHEMENTS_SYNCHRONES:
        chemin = RACINE / entree["fichier"]
        if chemin.exists() and re.search(entree["motif"], chemin.read_text(encoding="utf-8")):
            confirmes.append(entree)
    return confirmes


def scanner_pages_dashboard() -> list[dict]:
    resultats = []
    dossier = RACINE / "dashboard" / "app_pages"
    if not dossier.exists():
        return resultats
    for fichier in sorted(dossier.glob("*.py")):
        if fichier.name == "__init__.py":
            continue
        texte = fichier.read_text(encoding="utf-8")
        docstring_m = re.match(r'^"""(.*?)"""', texte, re.DOTALL)
        resume = "(pas de docstring)"
        if docstring_m:
            paragraphes = [p.strip() for p in docstring_m.group(1).strip().split("\n\n") if p.strip()]
            if paragraphes:
                # Premier paragraphe entier (pas la première phrase) : couper
                # sur ". " confond les abréviations ("art. 17 RGPD") avec une
                # vraie fin de phrase.
                resume = " ".join(paragraphes[0].split())
        portee = "Client" if fichier.stem == "portail_client" else "Admin"
        resultats.append({"fichier": fichier.name, "resume": resume, "portee": portee})
    return resultats


def scanner_integrations() -> list[dict]:
    fichiers_py = [
        f
        for f in RACINE.rglob("*.py")
        if ".git" not in f.parts
        and "__pycache__" not in f.parts
        and "venv" not in f.parts
        and f.resolve() != Path(__file__).resolve()  # sinon il se détecte lui-même (les motifs ci-dessus sont dans son propre code)
    ]
    contenus = {}
    for f in fichiers_py:
        try:
            contenus[f] = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

    resultats = []
    for integ in INTEGRATIONS:
        fichiers_trouves = set()
        for f, texte in contenus.items():
            if any(re.search(m, texte) for m in integ["motifs"]):
                fichiers_trouves.add(str(f.relative_to(RACINE)))
        if fichiers_trouves:
            resultats.append({**integ, "fichiers": sorted(fichiers_trouves)})
    return resultats


def generer_mermaid_pipelines(workflows: list[dict], synchrones: list[dict]) -> str:
    lignes = ["flowchart LR"]
    if workflows:
        lignes.append('    subgraph CRON["GitHub Actions (cron)"]')
        for wf in workflows:
            id_wf = _identifiant_mermaid(wf["fichier"])
            cron_txt = ", ".join(wf["crons"]) if wf["crons"] else "workflow_dispatch seul"
            lignes.append(f'        {id_wf}["{wf["nom"]}<br/>({cron_txt})"] --> {id_wf}_script(["{wf["script"]}"])')
        lignes.append("    end")
    if synchrones:
        lignes.append('    subgraph SYNC["Déclenchement synchrone (pas de cron)"]')
        for i, s in enumerate(synchrones):
            id_s = f"sync_{i}"
            lignes.append(f'        {id_s}["{s["nom"]}"]')
        lignes.append("    end")
    return "\n".join(lignes)


def generer_mermaid_pages(pages: list[dict]) -> str:
    lignes = ["flowchart TD"]
    lignes.append('    subgraph ADMIN["Espace Admin"]')
    for p in pages:
        if p["portee"] == "Admin":
            id_p = _identifiant_mermaid(p["fichier"])
            lignes.append(f'        {id_p}["{p["fichier"]}"]')
    lignes.append("    end")
    lignes.append('    subgraph CLIENT["Portail Client"]')
    for p in pages:
        if p["portee"] == "Client":
            id_p = _identifiant_mermaid(p["fichier"])
            lignes.append(f'        {id_p}["{p["fichier"]}"]')
    lignes.append("    end")
    return "\n".join(lignes)


def generer_markdown() -> str:
    spec = recuperer_schema_supabase()
    tables, vues = analyser_schema(spec)
    workflows = scanner_workflows()
    synchrones = verifier_declenchements_synchrones()
    pages = scanner_pages_dashboard()
    integrations = scanner_integrations()

    parties = []
    parties.append("# Architecture technique globale — leads-creator\n")
    parties.append(
        "> Document généré automatiquement par `scripts/generer_architecture.py` "
        "à partir des sources de vérité réelles du projet (schéma Supabase live, "
        "`.github/workflows/*.yml`, docstrings de `dashboard/app_pages/*.py`, "
        "imports du code) — **ne pas éditer à la main**, il serait écrasé au "
        "prochain run (voir `.github/workflows/generer_architecture.yml`).\n"
    )
    date_generation = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parties.append(f"Généré le : {date_generation}\n")

    # --- 1. Base de données ---
    parties.append("## 1. Schéma de la base de données\n")
    parties.append(f"{len(tables)} tables, {len(vues)} vue(s) — extraites en direct via l'endpoint OpenAPI de PostgREST.\n")
    parties.append("```mermaid\n" + generer_mermaid_er(tables) + "\n```\n")
    if vues:
        parties.append("**Vues** (non représentées dans le diagramme ci-dessus, lecture seule) :\n")
        for v in vues:
            parties.append(f"- `{v}`")
        parties.append("")

    # --- 2. Pipelines ---
    parties.append("## 2. Pipelines et automatisations\n")
    parties.append("```mermaid\n" + generer_mermaid_pipelines(workflows, synchrones) + "\n```\n")
    if workflows:
        parties.append("### Déclenchées par cron (GitHub Actions)\n")
        parties.append("| Workflow | Fréquence | Script | Rôle |")
        parties.append("|---|---|---|---|")
        for wf in workflows:
            cron_txt = ", ".join(f"`{c}`" for c in wf["crons"]) if wf["crons"] else "manuel uniquement (workflow_dispatch)"
            parties.append(f"| `{wf['fichier']}` | {cron_txt} | `{wf['script']}` | {wf['resume']} |")
        parties.append("")
    if synchrones:
        parties.append("### Déclenchées de façon synchrone (pas de cron)\n")
        for s in synchrones:
            parties.append(f"- **{s['nom']}** (`{s['fichier']}`) — {s['resume']}")
        parties.append("")

    # --- 3. Pages dashboard ---
    parties.append("## 3. Pages et rôles du dashboard\n")
    parties.append("```mermaid\n" + generer_mermaid_pages(pages) + "\n```\n")
    parties.append("| Page | Espace | Rôle |")
    parties.append("|---|---|---|")
    for p in pages:
        parties.append(f"| `{p['fichier']}` | {p['portee']} | {p['resume']} |")
    parties.append("")

    # --- 4. Intégrations ---
    parties.append("## 4. Intégrations externes\n")
    parties.append("Détectées par recherche des imports/appels réels dans le code (pas une liste maintenue à la main).\n")
    parties.append("| Service | Rôle dans le projet | Utilisé dans |")
    parties.append("|---|---|---|")
    for integ in integrations:
        fichiers = ", ".join(f"`{f}`" for f in integ["fichiers"][:5])
        if len(integ["fichiers"]) > 5:
            fichiers += f", … (+{len(integ['fichiers']) - 5})"
        parties.append(f"| {integ['nom']} | {integ['role']} | {fichiers} |")
    if workflows:
        fichiers_wf = ", ".join(f"`{wf['fichier']}`" for wf in workflows)
        parties.append(
            f"| GitHub Actions | Seul déclencheur cron du projet (Streamlit Community Cloud n'a pas de cron) — voir section 2 | "
            f"{fichiers_wf} |"
        )
    parties.append("")

    # --- 5. Surveillance continue ---
    table_sante = "sante_base_donnees" in tables
    page_sante = (RACINE / "dashboard" / "app_pages" / "sante_bdd.py").exists()
    workflow_sante = next((wf for wf in workflows if wf["fichier"] == "controle_sante_bdd.yml"), None)
    if table_sante or page_sante:
        parties.append("## 5. Surveillance continue de la base\n")
        parties.append(
            "Un contrôle de santé automatisé (`scripts/controle_sante_bdd.py`) tourne "
            + (f"quotidiennement ({', '.join(workflow_sante['crons'])}, UTC)" if workflow_sante and workflow_sante["crons"] else "périodiquement")
            + " et vérifie la couverture RLS, les fonctions SECURITY DEFINER exposées, "
            "les demandes de devis bloquées, les réclamations en retard, les erreurs non "
            "résolues, la croissance anormale des tables, les données de test résiduelles "
            "et les FK non indexées — objectif : détecter une régression avant qu'un "
            "utilisateur ou un test manuel ne la découvre par hasard.\n"
        )
        if table_sante:
            parties.append("- Historique complet des contrôles : table `sante_base_donnees`.")
        if page_sante:
            parties.append("- Vue dashboard (statut, tendance 30 jours, actions à prendre) : `dashboard/app_pages/sante_bdd.py` (espace admin).")
        if workflow_sante:
            parties.append(f"- Déclenchement automatique : `.github/workflows/{workflow_sante['fichier']}`.")
        parties.append("")

    # --- 6. Modules de pilotage ---
    # Contrairement aux sections précédentes, cette liste n'est PAS déduite
    # du code (rien à introspecter pour un module "prévu mais pas encore
    # construit") : mise à jour à la main au fil de la construction de
    # chaque module (voir échange du 27/08/2026), une ligne par module créé
    # + le statut du module 12 (en attente, dépendance externe).
    parties.append("## 6. Modules de pilotage (économique, opérationnel, qualité)\n")
    parties.append(
        "Chantier en cours (27/08/2026) : 12 modules de pilotage complétant la "
        "surveillance technique (section 5) — construits un par un, chacun avec sa "
        "table Supabase (RLS actif) et sa page dashboard admin dédiée.\n"
    )
    for ligne in MODULES_PILOTAGE:
        parties.append(f"- {ligne}")
    parties.append(
        "- **Module 12 — Trafic du site vitrine** : en attente (dépendance externe, "
        "nom de domaine pas encore acheté) — volontairement non construit."
    )
    parties.append("")

    return "\n".join(parties)


def main() -> None:
    contenu = generer_markdown()
    DOC_SORTIE.parent.mkdir(parents=True, exist_ok=True)
    DOC_SORTIE.write_text(contenu, encoding="utf-8")
    print(f"Écrit : {DOC_SORTIE.relative_to(RACINE)} ({len(contenu)} caractères)")


if __name__ == "__main__":
    main()
