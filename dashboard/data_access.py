"""
Accès aux données pour le dashboard — remplace dashboard/api_client.py (appels
HTTP vers le backend FastAPI, supprimé). Chaque fonction ci-dessous reprend
TELLE QUELLE la logique métier de l'endpoint FastAPI correspondant
(api/main.py, avant suppression), mais interroge Supabase directement.

Pas de notion de rôle/JWT ici : la distinction admin/client (quelles
campagnes un compte peut voir/modifier) est gérée par les PAGES elles-mêmes
via dashboard/auth.py (est_admin(), campagnes_autorisees()) — il n'y a plus
de frontière réseau séparée à faire respecter, tout tourne dans le même
process Streamlit.

Cache : les fonctions de LECTURE sont décorées @st.cache_data(ttl=CACHE_TTL_SECONDES)
— sans ça, Streamlit relançait ces requêtes Supabase à chaque interaction
sur la page (frappe, clic, changement d'onglet), y compris plusieurs fois
dans le même rerun. Chaque fonction d'ÉCRITURE invalide explicitement le(s)
cache(s) concerné(s) après un succès (ex: `get_campagnes.clear()`), pour que
le `st.rerun()` qui suit une modification affiche immédiatement la donnée à
jour plutôt que la version mise en cache jusqu'à CACHE_TTL_SECONDES plus tard.
"""

import calendar
import glob
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
import stripe

from supabase_client import supabase

# sys.path (voir dashboard/app.py) inclut la racine du dépôt : import direct
# du module du scraper pour réutiliser sa définition des deux zones (Lyon /
# Grand Est) sans dupliquer la liste des communes ici — voir
# scraper_batiment.py::VILLES_LYON / VILLES_GRAND_EST.
from scraper_batiment import VILLES_GRAND_EST, VILLES_LYON

# Même module que mail_processor.py (désinscription STOP) et
# lead_worker.py/outbound_pro_btp.py (hard bounces) — voir
# supprimer_donnees_rgpd() ci-dessous : le droit à l'effacement doit lui
# aussi empêcher tout recontact futur, même après un nouveau scraping qui
# recréerait une ligne pour la même entreprise/personne.
from email_blacklist import blacklister_email

# Même module que dashboard/pages_publiques.py (contrôle automatique du
# SIRET à l'intake) — voir sql/init_verification_pro_artisans.sql.
from verification_pro import STATUT_REFUSE, STATUT_VERIFIE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DASHBOARD] %(message)s")
log = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

SEUIL_LEAD_ULTRA_QUALIFIE = float(os.getenv("SEUIL_LEAD_ULTRA_QUALIFIE", "0.85"))

RACINE_REPO = Path(__file__).resolve().parent.parent

CACHE_TTL_SECONDES = 30

# Leads de test/démo créés manuellement dans `leads` (jamais de vrais
# prospects) : __TEST_E2E_TUNNEL__ (id fixe, réutilisé pour dérouler le
# tunnel de vente à blanc — voir mémoire e2e_tunnel_test_fixture) boucle ses
# e-mails vers la boîte de l'agence elle-même, ce qui gonflait artificiellement
# le taux de réponse artisans. Exclus des KPIs/vues admin pour ne jamais
# fausser une lecture des vraies statistiques (ex: démo, reporting) — jamais
# supprimés pour autant, ce lead sert toujours à valider le tunnel.
LEADS_TEST_A_EXCLURE = (
    "a51d80c8-8363-42a6-87c8-7481911ecc2b",  # __TEST_E2E_TUNNEL__
    # "entreprise de test"/"test" (ce66b08a.../b45fc2f7...) supprimées le
    # 27/08/2026 (nettoyage données de test résiduelles, voir contrôle
    # santé donnees_test_residuelles) — retirées d'ici plutôt que laissées
    # en référence à des lignes qui n'existent plus.
)


class DataAccessError(Exception):
    """Erreur levée par les fonctions de ce module — remplace ApiError.
    Toujours interceptée par common.py::safe_call/executer_avec_spinner."""
    pass


def journaliser_action_admin(action: str, cible_type: str, cible_id: str | None, detail: dict | None = None) -> None:
    """Journal d'audit (module 10 pilotage, voir sql/init_journal_audit_admin.sql)
    — à appeler à la toute fin d'une fonction qui vient de RÉUSSIR une action
    admin sensible (jamais avant, pour ne jamais logger une action qui a en
    fait échoué). Lit l'identité de l'appelant directement dans
    st.session_state (auth_utilisateur_id/auth_email, voir auth.py) plutôt
    que de forcer chaque appelant à les repasser en paramètre.

    Ne lève JAMAIS d'exception : un échec d'écriture du journal (réseau,
    table pas encore migrée...) ne doit jamais faire échouer l'action métier
    réelle déjà effectuée — même philosophie que alertes.alerter_discord()
    (best effort, jamais bloquant)."""
    try:
        supabase.table("journal_audit_admin").insert({
            "utilisateur_id": st.session_state.get("auth_utilisateur_id"),
            "utilisateur_email": st.session_state.get("auth_email"),
            "action": action,
            "cible_type": cible_type,
            "cible_id": cible_id,
            "detail": detail or {},
        }).execute()
    except Exception as e:
        log.error(f"Échec écriture journal_audit_admin (action={action}, cible={cible_type}/{cible_id}) : {e}")


def _slugifier(texte: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (texte or "").lower()).strip("-")
    return slug or "campagne"


def _erreur_upsert_campagne(e: Exception, slug: str, contexte: str = "enregistrement") -> DataAccessError:
    """`campagnes.slug` a sa propre contrainte unique (indépendante de
    l'upsert `on_conflict=nom_client`) — deux noms de client qui se
    slugifient identiquement (variantes de casse/ponctuation) déclenchent
    une violation Postgres brute (code 23505) plutôt qu'un vrai conflit sur
    nom_client. Message clair plutôt que l'erreur SQL telle quelle."""
    message = str(e)
    if "slug" in message.lower() and ("duplicate" in message.lower() or "unique" in message.lower() or "23505" in message):
        return DataAccessError(
            f"Une autre campagne génère déjà le même identifiant de page (slug « {slug} », "
            "dérivé du nom du client) — renomme légèrement l'un des deux noms pour lever "
            "l'ambiguïté."
        )
    return DataAccessError(f"Échec {contexte} de la campagne : {e}")


# ---------------------------------------------------------------------
# Statistiques / santé
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_stats() -> dict:
    try:
        # Aucune colonne passée à select() (pas même "*") : la version
        # installée de postgrest (voir requirements.txt, sans pin de version)
        # en fait alors une requête HEAD automatiquement — c'est ce qui
        # remplace l'ancien argument select(..., head=True), supprimé dans
        # cette version (une colonne passée déclencherait un GET classique).
        leads_count = (
            supabase.table("leads").select(count="exact")
            .not_.in_("id", LEADS_TEST_A_EXCLURE).execute().count or 0
        )
        reports = (
            supabase.table("ceo_reports")
            .select("*").order("created_at", desc=True).limit(1).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture statistiques : {e}") from e
    return {
        "leads_total": leads_count,
        "last_ceo_report": reports[0].get("date") if reports else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def compter_relances_envoyees_depuis(depuis_iso: str) -> int:
    """Nombre de leads (artisans) dont last_relance_at est postérieur à
    `depuis_iso` — utilisé pour afficher le nombre réel d'e-mails de relance
    envoyés après un run de relance_prospects.py (lancé en subprocess par
    process_runner.lancer_relance(), qui ne remonte pas son résultat
    directement, voir common.py::afficher_suivi). PAS de décorateur
    @st.cache_data ici : appelé juste après la fin du subprocess, un résultat
    caché de CACHE_TTL_SECONDES donnerait un compte obsolète ou vide."""
    try:
        # Aucune colonne passée à select() : requête HEAD (compte seul, voir
        # get_stats() ci-dessus pour le pourquoi de cette syntaxe).
        return (
            supabase.table("leads").select(count="exact")
            .not_.in_("id", LEADS_TEST_A_EXCLURE)
            .gte("last_relance_at", depuis_iso).execute().count or 0
        )
    except Exception as e:
        log.error(f"Erreur lecture du nombre de relances envoyées : {e}")
        return 0


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_health() -> dict:
    """État réel des dépendances externes (Supabase, Ollama, Zoho, Discord).
    Ollama/LLM cloud : décision assumée le 11/08, faute de budget pour un
    service LLM hébergé — LLM_API_URL/OLLAMA_HOST restent volontairement non
    configurés, donc generer_pitch()/generer_pitch_ia() retombent sur le
    pitch générique (voir lead_worker.py/outbound_pro_btp.py). Ce n'est PLUS
    affiché comme une panne (voir plus bas) tant qu'aucun des deux n'est
    configuré : ce serait alors une vraie alerte, pas l'état normal actuel."""
    import requests

    resultat = {}

    # dnspython (email_validator.py::possede_enregistrement_mx) — vérifié
    # EN PREMIER, isolé de tout le reste : si un souci imprévu ailleurs dans
    # cette fonction (Supabase, Ollama...) devait un jour se produire hors
    # des except déjà posés plus bas, ce contrôle ne doit jamais en pâtir en
    # se retrouvant absent du résultat (symptôme vu en usage réel : la clé
    # "dnspython" manquante affiche "?" côté sidebar, indiscernable d'un
    # échec du contrôle lui-même — voir app.py). Sans accès direct aux logs
    # de build Streamlit Cloud, c'est le seul moyen fiable de confirmer,
    # depuis l'app RÉELLEMENT déployée (même environnement Python que
    # lead_worker.py/ceo_agent.py/..., lancés en subprocess), que la
    # dépendance a bien été installée. Si absente, la vérification MX est
    # silencieusement ignorée (jamais bloquante, voir email_validator.py) —
    # donc rien ne casse, mais ce filtre de qualité est alors incomplet.
    # except Exception (pas seulement ImportError) : un souci d'installation
    # partielle peut lever autre chose qu'ImportError (ex: erreur au niveau
    # C d'une dépendance native) — dans tous les cas, un message clair vaut
    # mieux qu'une exception qui ferait disparaître toute la fonction.
    try:
        import dns.resolver  # noqa: F401

        resultat["dnspython"] = "ok"
    except Exception as e:
        resultat["dnspython"] = f"absent ou cassé ({e}) — vérification MX des emails désactivée"

    try:
        supabase.table("campagnes").select("id").limit(1).execute()
        resultat["supabase"] = "ok"
    except Exception as e:
        resultat["supabase"] = f"down ({e})"

    # Aucun LLM_API_URL/OLLAMA_HOST configuré : llm_config.py retombe alors
    # sur le défaut codé en dur http://127.0.0.1:11434 (jamais joignable
    # depuis Streamlit Cloud) — décision assumée le 11/08 (pas de budget
    # actuellement), donc affiché en gris neutre "non configuré", jamais en
    # rouge "down" : ce n'est pas une panne à corriger. Si l'un des deux est
    # explicitement configuré plus tard et devient injoignable, LÀ c'est une
    # vraie panne (voir branche ci-dessous, comportement inchangé).
    if not (os.getenv("LLM_API_URL") or os.getenv("OLLAMA_HOST")):
        resultat["ollama"] = "non configuré — pitch générique utilisé (choix actuel, pas une panne)"
    else:
        try:
            # Teste la MÊME URL que celle effectivement utilisée par les
            # scripts de prospection (lead_worker.py/outbound_pro_btp.py) —
            # voir llm_config.py, racine du dépôt, pour la résolution
            # local/cloud (LLM_API_URL en prod, OLLAMA_HOST en dev/docker-
            # compose). Un OLLAMA_HOST redéfini séparément ici pouvait faire
            # afficher "ok" sur un Ollama local jamais utilisé par les
            # scripts réels si LLM_API_URL pointait ailleurs (ou l'inverse).
            from llm_config import LLM_API_KEY, LLM_API_URL

            headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
            r = requests.get(f"{LLM_API_URL}/api/tags", headers=headers, timeout=5)
            modeles = [m["name"] for m in r.json().get("models", [])] if r.status_code == 200 else []
            resultat["ollama"] = "ok" if r.status_code == 200 and modeles else "degraded (aucun modèle installé)"
        except requests.exceptions.RequestException as e:
            resultat["ollama"] = f"down ({e})"

    resultat["zoho_configure"] = bool(os.getenv("ZOHO_USER") and os.getenv("ZOHO_PASSWORD"))
    resultat["discord_configure"] = bool(os.getenv("DISCORD_WEBHOOK_URL"))

    return resultat


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_contents() -> dict:
    """Articles générés (fichiers article*.md à la racine du dépôt)."""
    try:
        fichiers = sorted(glob.glob(str(RACINE_REPO / "article*.md")))
        contenus = []
        for f in fichiers:
            with open(f, "r", encoding="utf-8") as fichier:
                contenus.append({"filename": os.path.basename(f), "content": fichier.read()})
    except OSError as e:
        raise DataAccessError(f"Erreur lecture des contenus générés : {e}") from e
    return {"contents": contenus}


# ---------------------------------------------------------------------
# Leads (artisans / B2C)
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_leads(limit: int = 100) -> dict:
    try:
        data = (
            supabase.table("leads").select("*")
            .not_.in_("id", LEADS_TEST_A_EXCLURE)
            .order("created_at", desc=True).limit(limit).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads : {e}") from e
    return {"leads": data}


# ---------------------------------------------------------------------
# Vérification pro (SIRET / assurance décennale) des artisans clients —
# voir sql/init_verification_pro_artisans.sql (colonnes sur `leads`) et
# verification_pro.py (constantes de statut, contrôle SIRENE). Vue admin
# dans app_pages/administration_contrats.py, section "Vérification pro".
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_leads_verification_pro() -> dict:
    """Artisans ayant déclaré un SIRET à l'intake (voir dashboard/pages_publiques.py
    ::afficher_intake) — candidats à la vérification manuelle de l'assurance
    décennale. Le contrôle automatique du SIRET (siret_verifie_sirene) a
    déjà eu lieu à la soumission de l'intake ; cette fonction ne fait que
    LIRE son résultat pour l'afficher à l'admin, jamais un nouvel appel
    réseau à SIRENE."""
    try:
        data = (
            supabase.table("leads")
            .select(
                "id,company,email,siret_declare,siret_verifie_sirene,"
                "siret_raison_sociale_sirene,assurance_decennale_declaree,"
                "statut_verification_pro,date_verification,created_at"
            )
            .not_.is_("siret_declare", "null")
            .not_.in_("id", LEADS_TEST_A_EXCLURE)
            .order("created_at", desc=True)
            .limit(500)
            .execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture vérification pro : {e}") from e
    return {"leads": data}


def maj_statut_verification_pro(lead_id: str, statut: str) -> dict:
    """Changement manuel du statut de vérification d'un artisan (les 4
    valeurs possibles sont définies dans verification_pro.STATUTS_VERIFICATION_PRO)
    — date_verification est posée à la date de CETTE décision admin
    ('verifie' ou 'refuse'), distincte de siret_verifie_sirene (contrôle
    automatique, déjà horodaté implicitement par la soumission de l'intake)."""
    corps = {"statut_verification_pro": statut}
    if statut in (STATUT_VERIFIE, STATUT_REFUSE):
        corps["date_verification"] = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("leads").update(corps).eq("id", lead_id).execute()
    except Exception as e:
        raise DataAccessError(f"Erreur mise à jour du statut de vérification : {e}") from e
    get_leads_verification_pro.clear()
    journaliser_action_admin("maj_statut_verification_pro", "lead", lead_id, {"nouveau_statut": statut})
    return corps


def _zone_pour_commune(commune: str | None) -> str:
    """Classe une commune de `leads` dans l'une des deux zones scrapées
    (voir scraper_batiment.py::VILLES_LYON / VILLES_GRAND_EST), sans
    dupliquer les noms de communes. 'Autre' couvre les leads antérieurs à
    ce découpage ou une commune retirée depuis de VILLES_CIBLES."""
    if commune in VILLES_LYON:
        return "Lyon"
    if commune in VILLES_GRAND_EST:
        return "Grand Est"
    return "Autre"


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_stats_par_zone_artisans() -> dict:
    """Répartition des leads artisans (table `leads`) par zone géographique
    — le pipeline scraper_batiment.py/lead_worker.py n'a pas de notion de
    campagne (contrairement au B2B, voir get_campagne_stats) : cette vue
    reconstitue une visibilité séparée Lyon / Grand Est à partir de la seule
    colonne `commune`, sans toucher à la structure de données existante."""
    try:
        leads = (
            supabase.table("leads").select("commune,status,contacted,score")
            .not_.in_("id", LEADS_TEST_A_EXCLURE).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture répartition par zone : {e}") from e

    zones: dict[str, dict] = {
        nom: {"total": 0, "a_contacter": 0, "contactes": 0, "scores": []}
        for nom in ("Lyon", "Grand Est", "Autre")
    }
    for lead in leads:
        zone = zones[_zone_pour_commune(lead.get("commune"))]
        zone["total"] += 1
        if lead.get("contacted"):
            zone["contactes"] += 1
        elif lead.get("status") == "a_contacter":
            zone["a_contacter"] += 1
        zone["scores"].append(lead.get("score") or 0)

    for zone in zones.values():
        scores = zone.pop("scores")
        zone["score_moyen"] = round(sum(scores) / len(scores), 1) if scores else 0

    return zones


# ---------------------------------------------------------------------
# Leads professionnels (B2B, outbound_chantiers)
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_leads_pro(client_final: str) -> dict:
    try:
        data = (
            supabase.table("leads_professionnels").select("*")
            .eq("client_final", client_final)
            .order("score_final", desc=True).limit(200).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads B2B : {e}") from e
    return {"leads_pro": data}


def enrichir_lead_pro(lead_pro_id: str, forcer_reecriture: bool = False) -> dict:
    """Ré-enrichissement à la demande (admin) — même logique que le pipeline
    automatique (module 2).

    Par défaut (forcer_reecriture=False, comportement historique inchangé) :
    ne remplace jamais une donnée déjà connue par un résultat vide, seuls les
    champs manquants sont mis à jour — protège une donnée déjà vérifiée
    manuellement d'un écrasement accidentel par un résultat de moins bonne
    qualité.

    Ce même garde-fou a un revers resté invisible jusqu'ici : si le champ
    déjà rempli est FAUX (pas seulement vide), un nouveau résultat CORRECT
    trouvé par un ré-enrichissement ultérieur était silencieusement ignoré,
    jamais écrit en base — aucune erreur, aucun log, juste une correction
    perdue (cas réel : linkedin_url pointant vers la page LinkedIn d'un
    annuaire source plutôt que vers le professionnel, jamais corrigé malgré
    plusieurs ré-enrichissements). forcer_reecriture=True lève ce garde-fou
    explicitement, à la demande de l'utilisateur (case à cocher dédiée côté
    dashboard, jamais activée par défaut) : écrase alors tout champ pour
    lequel un nouveau résultat non vide a été trouvé, même déjà rempli."""
    from outbound_chantiers.enrichir_acteurs_pro import enrichir_un_acteur

    try:
        leads_pro = supabase.table("leads_professionnels").select("*").eq("id", lead_pro_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du lead professionnel : {e}") from e
    if not leads_pro:
        raise DataAccessError("Lead professionnel introuvable")
    lead_pro = leads_pro[0]

    resultat = enrichir_un_acteur(lead_pro["nom_entreprise"], lead_pro.get("commune") or "")

    changements = {
        cle: valeur for cle, valeur in resultat.items()
        if cle != "contact_exploitable" and valeur and (forcer_reecriture or not lead_pro.get(cle))
    }
    changements["enrichissement_statut"] = resultat["enrichissement_statut"]
    changements["enrichi_at"] = datetime.now(timezone.utc).isoformat()

    try:
        supabase.table("leads_professionnels").update(changements).eq("id", lead_pro_id).execute()
    except Exception as e:
        raise DataAccessError(f"Échec mise à jour enrichissement : {e}") from e

    get_leads_pro.clear()
    log.info(f"Ré-enrichissement de « {lead_pro['nom_entreprise']} » : statut={resultat['enrichissement_statut']}")
    return {"status": "success", "resultat": resultat}


def signaler_lead_pro_invalide(
    lead_pro_id: str, motif: str, est_admin: bool, montant_credit_centimes: int = 0
) -> dict:
    """Signale un lead B2B invalide/non qualifié. `est_admin` remplace la
    distinction de rôle JWT d'origine : un admin fixe librement le montant de
    l'avoir et le valide immédiatement ; un compte client ne fixe jamais son
    propre crédit (toujours 0, demande en 'en_attente' de revue humaine)."""
    motif = (motif or "signalé invalide").strip()
    try:
        leads_pro = supabase.table("leads_professionnels").select("*").eq("id", lead_pro_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du lead professionnel : {e}") from e
    if not leads_pro:
        raise DataAccessError("Lead professionnel introuvable")
    lead_pro = leads_pro[0]

    montant = montant_credit_centimes if est_admin else 0

    try:
        supabase.table("leads_professionnels").update(
            {"signale_invalide": True, "motif_invalidite": motif}
        ).eq("id", lead_pro_id).execute()

        resultat = supabase.table("remboursements").insert({
            "lead_professionnel_id": lead_pro_id,
            "client_final": lead_pro.get("client_final"),
            "montant_centimes": montant,
            "motif": f"Avoir {'(saisi admin)' if est_admin else '(auto-signalé par le client, à valider)'} — "
                     f"lead « {lead_pro.get('nom_entreprise')} » signalé invalide : {motif}",
            "type_remboursement": "avoir_commercial",
            "statut": "valide" if est_admin else "en_attente",
            "demande_par": "admin" if est_admin else "client",
        }).execute()
    except Exception as e:
        raise DataAccessError(f"Échec création avoir : {e}") from e

    get_leads_pro.clear()
    get_remboursements.clear()
    log.info(f"Lead pro « {lead_pro.get('nom_entreprise')} » signalé invalide (par {'admin' if est_admin else 'client'})")
    return {"remboursement": resultat.data[0] if resultat.data else None}


# ---------------------------------------------------------------------
# Campagnes
# ---------------------------------------------------------------------

# Priorité de statut pour get_campagnes() ci-dessous — INDÉPENDANTE de la date
# de création : une campagne 'active' doit toujours passer devant, même créée
# avant un brouillon ou une campagne en_pause plus récente. Un statut absent/
# inconnu (donnée legacy) reste prioritaire sur un brouillon explicite plutôt
# que d'être poussé tout en bas par défaut.
_PRIORITE_STATUT_CAMPAGNE = {"active": 0, "en_pause": 1, "archivee": 2, "brouillon": 3}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_campagnes() -> dict:
    try:
        data = supabase.table("campagnes").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture campagnes : {e}") from e
    # Tri par PRIORITÉ DE STATUT d'abord (voir _PRIORITE_STATUT_CAMPAGNE),
    # date de création ensuite au sein d'un même statut (tri stable : l'ordre
    # obtenu par .order("created_at", desc=True) ci-dessus est préservé à
    # l'intérieur de chaque groupe). Nécessaire : un tri qui se contentait de
    # repousser 'brouillon' en dernier laissait un statut 'en_pause' ou
    # 'archivee' plus récent passer devant une campagne 'active' plus
    # ancienne — sinon les st.selectbox de gestion_clients.py/
    # suivi_resultats.py/administration_contrats.py/portail_client.py
    # (index 0 implicite) peuvent présélectionner une campagne inactive au
    # lieu de la vraie campagne en cours.
    data = sorted(
        data,
        key=lambda c: _PRIORITE_STATUT_CAMPAGNE.get(c.get("statut"), 1),
    )
    return {"campagnes": data}


def creer_ou_modifier_campagne(campagne: dict) -> dict:
    nom_client = (campagne.get("nom_client") or "").strip()
    if not nom_client:
        raise DataAccessError("nom_client est requis")

    corps = {
        "nom_client": nom_client,
        "slug": campagne.get("slug") or _slugifier(nom_client),
        "secteur": campagne.get("secteur") or "",
        "description_services": campagne.get("description_services") or "",
        "communes_cibles": campagne.get("communes_cibles") or [],
        "types_acteur_cibles": campagne.get("types_acteur_cibles") or [],
        "statut": campagne.get("statut") or "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resultat = supabase.table("campagnes").upsert(corps, on_conflict="nom_client").execute()
    except Exception as e:
        raise _erreur_upsert_campagne(e, corps["slug"], "enregistrement") from e
    get_campagnes.clear()
    return {"campagne": resultat.data[0] if resultat.data else corps}


def dupliquer_campagne(nom_client: str, nouveau_nom_client: str, statut: str = "active") -> dict:
    try:
        source = supabase.table("campagnes").select("*").eq("nom_client", nom_client).limit(1).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture de la campagne source : {e}") from e
    if not source:
        raise DataAccessError(f"Campagne « {nom_client} » introuvable")
    source = source[0]

    nouveau_nom = (nouveau_nom_client or "").strip()
    if not nouveau_nom:
        raise DataAccessError("Le nouveau nom est requis")
    if nouveau_nom == nom_client:
        raise DataAccessError("Le nouveau nom doit être différent de la campagne source")

    corps = {
        "nom_client": nouveau_nom,
        "slug": _slugifier(nouveau_nom),
        "secteur": source.get("secteur", ""),
        "description_services": source.get("description_services", ""),
        "communes_cibles": source.get("communes_cibles", []),
        "types_acteur_cibles": source.get("types_acteur_cibles", []),
        "statut": statut or "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resultat = supabase.table("campagnes").upsert(corps, on_conflict="nom_client").execute()
    except Exception as e:
        raise _erreur_upsert_campagne(e, corps["slug"], "duplication") from e
    get_campagnes.clear()
    return {"campagne": resultat.data[0] if resultat.data else corps}


def renommer_campagne(nom_client: str, nouveau_nom_client: str) -> dict:
    """Renomme une campagne EN BROUILLON uniquement — jamais une campagne
    active/en_pause/archivée, dont le nom peut déjà être référencé ailleurs.
    Crée la nouvelle ligne PUIS supprime l'ancienne (jamais l'inverse)."""
    try:
        source = supabase.table("campagnes").select("*").eq("nom_client", nom_client).limit(1).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture de la campagne source : {e}") from e
    if not source:
        raise DataAccessError(f"Campagne « {nom_client} » introuvable")
    source = source[0]

    if source.get("statut") != "brouillon":
        raise DataAccessError(
            "Seule une campagne en brouillon peut être renommée directement. "
            "Dupliquez plutôt cette campagne sous un nouveau nom."
        )

    nouveau_nom = (nouveau_nom_client or "").strip()
    if not nouveau_nom:
        raise DataAccessError("Le nouveau nom est requis")
    if nouveau_nom == nom_client:
        raise DataAccessError("Le nouveau nom doit être différent de l'original")

    corps = {
        "nom_client": nouveau_nom,
        "slug": _slugifier(nouveau_nom),
        "secteur": source.get("secteur", ""),
        "description_services": source.get("description_services", ""),
        "communes_cibles": source.get("communes_cibles", []),
        "types_acteur_cibles": source.get("types_acteur_cibles", []),
        "statut": "brouillon",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resultat = supabase.table("campagnes").upsert(corps, on_conflict="nom_client").execute()
    except Exception as e:
        raise _erreur_upsert_campagne(e, corps["slug"], "renommage") from e

    try:
        supabase.table("campagnes").delete().eq("id", source["id"]).execute()
    except Exception as e:
        # La nouvelle ligne existe déjà à ce stade : ne JAMAIS prétendre que
        # le renommage a échoué (il a en partie réussi) ni le cacher —
        # l'admin doit savoir qu'un doublon existe et le supprimer lui-même.
        log.error(
            f"Renommage « {nom_client} » -> « {nouveau_nom} » : nouvelle ligne créée mais "
            f"l'ancienne (id={source['id']}) n'a pas pu être supprimée : {e}"
        )
        get_campagnes.clear()
        raise DataAccessError(
            f"La campagne a bien été renommée en « {nouveau_nom} », mais l'ancienne fiche "
            f"« {nom_client} » n'a pas pu être supprimée automatiquement (doublon) — "
            "supprime-la manuellement."
        ) from e

    get_campagnes.clear()
    return {"campagne": resultat.data[0] if resultat.data else corps}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_campagne_stats(nom_client: str) -> dict:
    try:
        leads = (
            supabase.table("leads_professionnels")
            .select("contacted,statut,score_final")
            .eq("client_final", nom_client).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture stats campagne : {e}") from e

    total = len(leads)
    contactes = sum(1 for l in leads if l.get("contacted"))
    interesses = sum(1 for l in leads if l.get("statut") == "interested")
    ultra_qualifies = sum(1 for l in leads if (l.get("score_final") or 0) >= SEUIL_LEAD_ULTRA_QUALIFIE)
    return {
        "nom_client": nom_client,
        "leads_total": total,
        "contactes": contactes,
        "taux_contact": round(contactes / total, 3) if total else 0,
        "opportunites": interesses,
        "leads_ultra_qualifies": ultra_qualifies,
    }


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_warmup_status() -> dict:
    """Montée en charge progressive (warmup) du domaine d'envoi B2B."""
    from outbound_chantiers.outbound_pro_btp import statut_ramp_warmup
    return statut_ramp_warmup()


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_taux_reponse() -> dict:
    """Taux de réponse par segment (artisans) et par campagne B2B (client_final),
    calculé à partir de email_events (voir sql/init_email_reponses.sql) : un
    'envoye' sans 'repondu' correspondant = pas encore de réponse. Compte les
    lead_id DISTINCTS (pas les lignes) : une relance ou une seconde réponse
    du même lead ne doit pas gonfler artificiellement le taux.

    Pas de notion d'ouverture ici (pixel de tracking retiré, voir
    email_tracking.py) — uniquement le taux de réponse."""
    try:
        lignes = (
            supabase.table("email_events")
            .select("lead_type,lead_id,client_final,type_evenement")
            .in_("type_evenement", ["envoye", "repondu"])
            .not_.in_("lead_id", LEADS_TEST_A_EXCLURE)
            .execute()
            .data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture email_events : {e}") from e

    def _taux(envoyes: set, repondus: set) -> dict:
        return {
            "envoyes": len(envoyes),
            "repondus": len(repondus),
            "taux": round(len(repondus) / len(envoyes), 3) if envoyes else 0,
        }

    envoyes_artisan, repondus_artisan = set(), set()
    envoyes_par_client: dict[str, set] = {}
    repondus_par_client: dict[str, set] = {}

    for ligne in lignes:
        lead_id = ligne["lead_id"]
        cible_envoyes, cible_repondus = (
            (envoyes_artisan, repondus_artisan) if ligne["lead_type"] == "lead_artisan"
            else (
                envoyes_par_client.setdefault(ligne.get("client_final") or "?", set()),
                repondus_par_client.setdefault(ligne.get("client_final") or "?", set()),
            )
        )
        (cible_repondus if ligne["type_evenement"] == "repondu" else cible_envoyes).add(lead_id)

    clients = sorted(set(envoyes_par_client) | set(repondus_par_client))
    return {
        "artisans": _taux(envoyes_artisan, repondus_artisan),
        "b2b_par_client": [
            {"client_final": client, **_taux(envoyes_par_client.get(client, set()), repondus_par_client.get(client, set()))}
            for client in clients
        ],
    }


# ---------------------------------------------------------------------
# Contrats / paiements (Stripe) — confirmation manuelle (plus de webhooks)
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_contracts() -> dict:
    try:
        data = (
            supabase.table("contracts")
            .select("*,leads(company,email)")
            .order("created_at", desc=True).limit(100).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture contrats : {e}") from e
    return {"contracts": data}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_contracts_finances() -> dict:
    """Contrats B2C pour app_pages/finances.py — fonction dédiée plutôt que
    de modifier get_contracts() ci-dessus (utilisée telle quelle par
    Gestion & Réponse pour les remboursements) : exclut les leads de test/
    démo (LEADS_TEST_A_EXCLURE) pour ne jamais fausser un KPI financier, et
    ne limite pas à 100 lignes (le CA total et la courbe cumulée ont besoin
    de tout l'historique, pas seulement des contrats les plus récents).

    PÉRIMÈTRE : la table `contracts` ne couvre QUE le B2C (vente de leads
    aux artisans, tunnel intake -> Yousign -> Stripe). Le B2B (campagnes
    clients, ex. S.B.G Travaux) n'a aucune facturation persistée en base à
    ce jour — le bon de commande généré depuis "Administration & Contrats"
    ne fait qu'un PDF, rien n'est écrit côté Supabase (voir sa docstring)."""
    try:
        data = (
            supabase.table("contracts")
            .select("*,leads(company,email)")
            .not_.in_("lead_id", LEADS_TEST_A_EXCLURE)
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture contrats (finances) : {e}") from e
    return {"contracts": data}


def marquer_contrat_signe(contract_id: str) -> dict:
    """Remplace le webhook Yousign : l'admin vérifie manuellement dans
    Yousign que le contrat est signé, puis clique ce bouton."""
    try:
        contrats = supabase.table("contracts").select("*").eq("id", contract_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du contrat : {e}") from e
    if not contrats:
        raise DataAccessError("Contrat introuvable")
    contrat = contrats[0]

    try:
        supabase.table("contracts").update(
            {"yousign_status": "signe", "signed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", contract_id).execute()
        supabase.table("leads").update({"status": "contrat_signe"}).eq("id", contrat["lead_id"]).execute()
    except Exception as e:
        raise DataAccessError(f"Échec mise à jour contrat : {e}") from e
    get_contracts.clear()
    journaliser_action_admin("marquer_contrat_signe", "contract", contract_id, {"lead_id": contrat["lead_id"]})
    return {"status": "ok"}


def marquer_contrat_paye(contract_id: str, stripe_payment_intent_id: str) -> dict:
    """Remplace le webhook Stripe : l'admin vérifie manuellement dans Stripe
    que le paiement est passé, saisit le payment_intent_id affiché dans le
    dashboard Stripe (indispensable pour un remboursement futur), et clique
    ce bouton."""
    try:
        contrats = supabase.table("contracts").select("*").eq("id", contract_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du contrat : {e}") from e
    if not contrats:
        raise DataAccessError("Contrat introuvable")
    contrat = contrats[0]

    if not (stripe_payment_intent_id or "").strip():
        raise DataAccessError("Le payment_intent_id Stripe est requis (visible dans le dashboard Stripe).")

    try:
        supabase.table("contracts").update({
            "payment_status": "paye",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "stripe_payment_intent_id": stripe_payment_intent_id.strip(),
        }).eq("id", contract_id).execute()
        supabase.table("leads").update({"status": "paye"}).eq("id", contrat["lead_id"]).execute()
    except Exception as e:
        raise DataAccessError(f"Échec mise à jour contrat : {e}") from e
    get_contracts.clear()
    journaliser_action_admin(
        "marquer_contrat_paye", "contract", contract_id,
        {"lead_id": contrat["lead_id"], "stripe_payment_intent_id": stripe_payment_intent_id.strip()},
    )
    return {"status": "ok"}


# ---------------------------------------------------------------------
# Remboursements
# ---------------------------------------------------------------------

def _valider_eligibilite_remboursement_stripe(contrat: dict, montant_centimes: int) -> tuple[bool, str]:
    """Règles métier de validation automatique — ne déclenche jamais le
    remboursement lui-même (action explicite séparée, voir executer_remboursement)."""
    if contrat.get("payment_status") != "paye":
        return False, "Le contrat n'est pas marqué comme payé."
    if not contrat.get("stripe_payment_intent_id"):
        return False, (
            "Paiement antérieur à la capture du payment_intent — à rembourser "
            "manuellement depuis le dashboard Stripe."
        )
    if montant_centimes > (contrat.get("montant_centimes") or 0):
        return False, "Le montant demandé dépasse le montant réellement payé sur ce contrat."
    return True, ""


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_remboursements(statut: str | None = None, client_final: str | None = None) -> dict:
    try:
        requete = supabase.table("remboursements").select("*").order("created_at", desc=True).limit(200)
        if statut:
            requete = requete.eq("statut", statut)
        if client_final:
            requete = requete.eq("client_final", client_final)
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture remboursements : {e}") from e
    return {"remboursements": data}


def creer_remboursement(demande: dict, est_admin: bool = True) -> dict:
    """`est_admin` (par défaut True — seule la page admin "Gestion & Réponse"
    appelle cette fonction aujourd'hui) suit le même principe que
    signaler_lead_pro_invalide : si cette fonction est un jour câblée à une
    page cliente, un compte client ne doit jamais pouvoir créer un avoir
    "avoir_commercial" auto-validé avec un montant de son choix — dans ce
    cas le montant est ignoré et la demande reste 'en_attente'."""
    contract_id = demande.get("contract_id")
    lead_professionnel_id = demande.get("lead_professionnel_id")
    client_final = demande.get("client_final")
    motif = (demande.get("motif") or "").strip()

    if not motif:
        raise DataAccessError("motif est requis")
    if not contract_id and not lead_professionnel_id and not client_final:
        raise DataAccessError("contract_id, lead_professionnel_id ou client_final requis")

    if contract_id:
        try:
            contrats = supabase.table("contracts").select("*").eq("id", contract_id).execute().data
        except Exception as e:
            raise DataAccessError(f"Erreur lecture du contrat : {e}") from e
        if not contrats:
            raise DataAccessError("Contrat introuvable")
        contrat = contrats[0]
        montant_centimes = demande.get("montant_centimes") or contrat.get("montant_centimes") or 0
        eligible, raison = _valider_eligibilite_remboursement_stripe(contrat, montant_centimes)
        corps = {
            "contract_id": contract_id,
            "client_final": client_final,
            "montant_centimes": montant_centimes,
            "motif": motif if eligible else f"{motif} — REJETÉ automatiquement : {raison}",
            "type_remboursement": "stripe",
            "statut": "valide" if eligible else "rejete",
            "demande_par": demande.get("demande_par") or "operateur",
        }
    else:
        montant = demande.get("montant_centimes") or 0 if est_admin else 0
        corps = {
            "lead_professionnel_id": lead_professionnel_id,
            "client_final": client_final,
            "montant_centimes": montant,
            "motif": motif,
            "type_remboursement": "avoir_commercial",
            "statut": "valide" if est_admin else "en_attente",
            "demande_par": demande.get("demande_par") or ("operateur" if est_admin else "client"),
        }

    try:
        resultat = supabase.table("remboursements").insert(corps).execute()
    except Exception as e:
        raise DataAccessError(f"Échec enregistrement remboursement : {e}") from e
    get_remboursements.clear()
    return {"remboursement": resultat.data[0] if resultat.data else corps}


def executer_remboursement(remboursement_id: str) -> dict:
    """Exécute RÉELLEMENT le remboursement Stripe — action explicite
    uniquement, jamais automatique."""
    try:
        rembs = supabase.table("remboursements").select("*").eq("id", remboursement_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du remboursement : {e}") from e
    if not rembs:
        raise DataAccessError("Remboursement introuvable")
    remb = rembs[0]

    if remb["type_remboursement"] != "stripe":
        raise DataAccessError(
            "Seuls les remboursements de type 'stripe' s'exécutent ici — "
            "un avoir commercial n'a aucun mouvement d'argent à déclencher."
        )
    if remb["statut"] != "valide":
        raise DataAccessError(f"Statut actuel '{remb['statut']}' : seul un remboursement 'valide' peut être exécuté.")

    try:
        contrats = supabase.table("contracts").select("*").eq("id", remb["contract_id"]).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture du contrat associé : {e}") from e
    contrat = contrats[0] if contrats else None
    if not contrat or not contrat.get("stripe_payment_intent_id"):
        supabase.table("remboursements").update({"statut": "echoue"}).eq("id", remboursement_id).execute()
        get_remboursements.clear()
        raise DataAccessError("Contrat ou payment_intent introuvable — remboursement impossible.")

    try:
        refund = stripe.Refund.create(
            payment_intent=contrat["stripe_payment_intent_id"],
            amount=remb["montant_centimes"],
        )
    except stripe.error.StripeError as e:
        supabase.table("remboursements").update({"statut": "echoue"}).eq("id", remboursement_id).execute()
        get_remboursements.clear()
        log.error(f"Échec remboursement Stripe {remboursement_id} : {e}")
        raise DataAccessError(f"Échec Stripe : {e}") from e

    supabase.table("remboursements").update({
        "statut": "rembourse",
        "stripe_refund_id": refund.id,
        "traite_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", remboursement_id).execute()
    get_remboursements.clear()
    log.info(f"💸 Remboursement Stripe exécuté : {remboursement_id} (refund={refund.id})")
    journaliser_action_admin(
        "executer_remboursement", "remboursement", remboursement_id,
        {"montant_centimes": remb["montant_centimes"], "stripe_refund_id": refund.id},
    )
    return {"status": "success", "stripe_refund_id": refund.id}


# ---------------------------------------------------------------------
# Événements e-mail (envoi uniquement — ouverture/clic retirés)
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_email_events(type_evenement: str | None = None, client_final: str | None = None, limit: int = 50) -> dict:
    try:
        requete = supabase.table("email_events").select("*").order("created_at", desc=True).limit(min(limit, 200))
        if type_evenement:
            requete = requete.eq("type_evenement", type_evenement)
        if client_final:
            requete = requete.eq("client_final", client_final)
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture événements e-mail : {e}") from e
    return {"email_events": data}


# ---------------------------------------------------------------------
# Suppression RGPD (droit à l'effacement, art. 17 RGPD)
# ---------------------------------------------------------------------

def rechercher_email_rgpd(email: str) -> dict:
    """Cherche un email dans `leads` (B2C) ET `leads_professionnels` (B2B)
    — jamais une seule des deux, un même contact pouvant légitimement
    exister des deux côtés (ex: un artisan aussi ciblé par une campagne
    B2B). Ne décide de rien : sert uniquement à afficher ce qui SERAIT
    supprimé, pour vérification visuelle avant toute action (voir
    supprimer_donnees_rgpd ci-dessous, qui reçoit les ids exacts affichés
    ici plutôt que de re-résoudre l'email au moment de la suppression)."""
    email = (email or "").strip().lower()
    if not email:
        raise DataAccessError("Merci de renseigner un e-mail.")
    try:
        leads = (
            supabase.table("leads")
            .select("id,company,name,status,created_at")
            .eq("email", email).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur de recherche dans leads : {e}") from e
    try:
        leads_pro = (
            supabase.table("leads_professionnels")
            .select("id,nom_entreprise,type_acteur,client_final,statut,created_at")
            .eq("email", email).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur de recherche dans leads_professionnels : {e}") from e
    return {"email": email, "leads": leads or [], "leads_professionnels": leads_pro or []}


def supprimer_donnees_rgpd(
    email: str,
    leads_ids: list[str],
    leads_professionnels_ids: list[str],
    date_demande_iso: str,
    traite_par: str,
) -> dict:
    """Exécute le droit à l'effacement pour un email donné :
    1) supprime définitivement les lignes dont les ids ont été VALIDÉS par
       l'admin après vérification visuelle (rechercher_email_rgpd) — jamais
       un nouveau filtre par email au moment de la suppression, pour
       garantir que ce qui est supprimé est exactement ce qui a été montré ;
    2) blackliste l'email (email_blacklist.py) pour empêcher tout recontact
       futur, même après un nouveau scraping qui recréerait une ligne ;
    3) trace l'opération dans registre_suppressions_rgpd (voir
       sql/init_registre_suppressions_rgpd.sql) — y compris quand
       leads_ids/leads_professionnels_ids sont tous deux vides (email déjà
       supprimé ou jamais présent : la demande doit être tracée comme
       traitée quand même, pas silencieusement ignorée)."""
    email = (email or "").strip().lower()
    if not email:
        raise DataAccessError("Merci de renseigner un e-mail.")
    if not (traite_par or "").strip():
        raise DataAccessError("Merci d'indiquer qui traite cette suppression.")

    supprimes_leads = []
    if leads_ids:
        try:
            supprimes_leads = supabase.table("leads").delete().in_("id", leads_ids).execute().data or []
        except Exception as e:
            raise DataAccessError(f"Échec de la suppression dans leads : {e}") from e

    supprimes_leads_pro = []
    if leads_professionnels_ids:
        try:
            supprimes_leads_pro = (
                supabase.table("leads_professionnels")
                .delete().in_("id", leads_professionnels_ids).execute().data or []
            )
        except Exception as e:
            raise DataAccessError(f"Échec de la suppression dans leads_professionnels : {e}") from e

    tables_touchees = []
    if supprimes_leads:
        tables_touchees.append("leads")
    if supprimes_leads_pro:
        tables_touchees.append("leads_professionnels")
    table_source = "+".join(tables_touchees) or "aucune"

    lead_id_reference = (supprimes_leads[0]["id"] if supprimes_leads else None) or (
        supprimes_leads_pro[0]["id"] if supprimes_leads_pro else None
    )
    lead_type_reference = "lead_artisan" if supprimes_leads else ("lead_professionnel" if supprimes_leads_pro else None)
    blacklister_email(
        email, raison="rgpd_droit_effacement",
        lead_type=lead_type_reference, lead_id=lead_id_reference,
    )

    email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()
    try:
        supabase.table("registre_suppressions_rgpd").insert({
            "email_hash": email_hash,
            "table_source": table_source,
            "date_demande": date_demande_iso,
            "date_traitement": datetime.now(timezone.utc).isoformat(),
            "traite_par": traite_par.strip(),
        }).execute()
    except Exception as e:
        # La suppression + blacklist ont déjà eu lieu à ce stade : ne
        # jamais prétendre que rien ne s'est passé, juste signaler que la
        # TRACE de l'opération n'a pas pu être enregistrée (table pas
        # encore créée ? voir sql/init_registre_suppressions_rgpd.sql).
        raise DataAccessError(
            f"Suppression effectuée (tables concernées : {table_source}) et email "
            f"blacklisté, MAIS l'enregistrement dans le registre a échoué : {e} — "
            "vérifie que sql/init_registre_suppressions_rgpd.sql a bien été exécuté "
            "dans Supabase."
        ) from e

    get_registre_suppressions_rgpd.clear()
    return {
        "email": email,
        "supprimes_leads": len(supprimes_leads),
        "supprimes_leads_professionnels": len(supprimes_leads_pro),
        "table_source": table_source,
    }


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_registre_suppressions_rgpd(limit: int = 20) -> dict:
    try:
        lignes = (
            supabase.table("registre_suppressions_rgpd")
            .select("*").order("date_traitement", desc=True).limit(limit).execute().data
        )
    except Exception as e:
        raise DataAccessError(
            f"Erreur lecture du registre RGPD : {e} — la table existe-t-elle "
            "(sql/init_registre_suppressions_rgpd.sql) ?"
        ) from e
    return {"suppressions": lignes or []}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_leads_par_ids(lead_ids: tuple[str, ...]) -> dict:
    """Résolution ciblée id -> lead (nom d'entreprise notamment), pour
    l'affichage de get_demandes_devis() ci-dessous sans dépendre de get_leads()
    (limitée aux 100 plus récents, un artisan destinataire plus ancien
    pourrait en être absent). `lead_ids` en tuple (pas liste) : @st.cache_data
    a besoin d'arguments hashables pour mettre le résultat en cache."""
    lead_ids = [lid for lid in lead_ids if lid]
    if not lead_ids:
        return {"leads": []}
    try:
        data = supabase.table("leads").select("id,company,email").in_("id", lead_ids).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture artisans : {e}") from e
    return {"leads": data}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_demandes_devis(statut: str | None = None, limit: int = 200) -> dict:
    """Voir livraison_devis.py pour le mécanisme de rapprochement qui pose
    ces statuts. lead_id_livraison n'est jamais résolu en nom d'entreprise
    ici : la page appelante (app_pages/demandes_devis.py) le fait via
    get_leads()/un lookup ciblé, pour ne pas ajouter une jointure à chaque
    lecture de cette liste, potentiellement filtrée sans avoir besoin du nom."""
    try:
        requete = (
            supabase.table("demandes_devis_particuliers").select("*")
            .order("created_at", desc=True).limit(limit)
        )
        if statut:
            requete = requete.eq("statut", statut)
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture demandes de devis : {e}") from e
    return {"demandes": data or []}


def marquer_demande_devis_payee_et_livree(demande_id: str, stripe_payment_intent_id: str | None = None) -> dict:
    """Confirmation MANUELLE du paiement d'une proposition 'à l'unité' —
    même principe que marquer_contrat_paye ci-dessus (pas de webhook Stripe
    dans ce projet) : l'admin vérifie lui-même dans Stripe que le paiement
    de la demande est passé, puis clique ce bouton. C'est CE moment précis
    qui révèle enfin les coordonnées complètes du particulier à l'artisan
    (jamais avant, voir livraison_devis.py::_proposer — la proposition
    initiale ne contenait qu'une description du besoin, pas nom/email/
    téléphone).

    stripe_payment_intent_id est optionnel (contrairement à
    marquer_contrat_paye) : une demande de devis n'est jamais remboursée
    individuellement dans ce projet (seul un contrat l'est, voir
    sql/init_remboursements.sql), donc rien ne l'exige pour un usage futur
    — gardé quand même pour la traçabilité si l'admin l'a sous la main."""
    try:
        demandes = supabase.table("demandes_devis_particuliers").select("*").eq("id", demande_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture de la demande : {e}") from e
    if not demandes:
        raise DataAccessError("Demande introuvable")
    demande = demandes[0]

    if demande.get("statut") != "proposee":
        raise DataAccessError(f"Cette demande n'est pas en attente de paiement (statut actuel : {demande.get('statut')}).")
    if not demande.get("lead_id_livraison"):
        raise DataAccessError("Aucun artisan destinataire associé à cette proposition.")

    try:
        leads = supabase.table("leads").select("*").eq("id", demande["lead_id_livraison"]).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture de l'artisan destinataire : {e}") from e
    if not leads:
        raise DataAccessError("Artisan destinataire introuvable (peut-être supprimé depuis, voir RGPD).")
    artisan = leads[0]

    champs_maj = {"statut": "livree", "livree_le": datetime.now(timezone.utc).isoformat()}
    if (stripe_payment_intent_id or "").strip():
        champs_maj["stripe_payment_intent_id"] = stripe_payment_intent_id.strip()
    try:
        supabase.table("demandes_devis_particuliers").update(champs_maj).eq("id", demande_id).execute()
    except Exception as e:
        raise DataAccessError(f"Échec mise à jour de la demande : {e}") from e

    from ceo_agent import send_email_prospect  # import différé : même raison que contrats_signature.py (cycle au chargement)

    corps = (
        f"Bonjour,\n\nMerci pour votre paiement ! Voici les coordonnées complètes de votre client :\n\n"
        f"Nom : {demande.get('nom')}\n"
        f"E-mail : {demande.get('email') or '—'}\n"
        f"Téléphone : {demande.get('telephone') or '—'}\n"
        f"Commune : {demande.get('commune') or '—'}\n"
        f"Besoin décrit : {demande.get('message') or '—'}\n\nCordialement"
    )
    send_email_prospect(artisan["email"], "Vos coordonnées client — paiement confirmé", corps, lead_id=artisan["id"])

    get_demandes_devis.clear()
    return {"status": "ok"}


# ---------------------------------------------------------------------
# Réclamations (B2C + B2B) — Article 4 des CGV (construire_articles_cgv_b2c
# et son équivalent B2B), voir sql/init_reclamations.sql pour le schéma
# complet et la justification des choix de modélisation (lead_id polymorphe,
# client_lead_id vs client_final selon type_lead).
#
# Coexiste avec le mécanisme B2B déjà en place (signaler_lead_pro_invalide
# ci-dessus, leads_professionnels.signale_invalide + remboursements) —
# volontairement pas remplacé : cette table est le point d'entrée formel
# (délai vérifié, motif contrôlé, décision tracée), pas encore reliée à un
# remboursement/avoir effectif (étape ultérieure distincte, jamais couplée
# automatiquement ici).
# ---------------------------------------------------------------------

MOTIFS_RECLAMATION = ("email_invalide", "telephone_errone", "zone_non_conforme", "type_non_conforme", "doublon")

LIBELLES_MOTIFS_RECLAMATION = {
    "email_invalide": "E-mail invalide",
    "telephone_errone": "Téléphone erroné",
    "zone_non_conforme": "Zone non conforme",
    "type_non_conforme": "Type non conforme (corps de métier / typologie de projet)",
    "doublon": "Doublon",
}

# Délai contractuel de réclamation (Article 4 des CGV) — l'absence de
# réponse du prospect est explicitement exclue comme motif valide par les
# CGV, ce qui est déjà garanti structurellement : elle n'existe pas dans
# MOTIFS_RECLAMATION, un client ne peut donc pas la sélectionner.
DELAI_RECLAMATION_JOURS = 7

# Fenêtre glissante et seuil pour calculer_taux_reclamation() ci-dessous —
# au-delà, revue manuelle du client par l'admin (jamais d'action bloquante
# automatique, voir la demande d'origine).
FENETRE_TAUX_RECLAMATION_JOURS = 90
SEUIL_ALERTE_TAUX_RECLAMATION = 0.20


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_leads_payants() -> dict:
    """Artisans clients actifs (status='paye') — pour le sélecteur d'aperçu
    admin du Portail Client B2C (voir app_pages/portail_client.py), même
    filtre que livraison_devis.py::_artisans_clients_actifs (sans les
    intake/contrats associés, juste de quoi peupler un sélecteur)."""
    try:
        data = (
            supabase.table("leads").select("id,company,email").eq("status", "paye")
            .not_.in_("id", LEADS_TEST_A_EXCLURE).order("company").execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture artisans payants : {e}") from e
    return {"leads": data}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_demandes_devis_livrees_pour_lead(lead_id: str) -> dict:
    """Demandes de devis (B2C) livrées à CET artisan (lead_id_livraison=lead_id,
    statut='livree') — alimente à la fois la liste "Vos demandes de devis"
    du Portail Client et le sélecteur du formulaire de réclamation associé."""
    try:
        data = (
            supabase.table("demandes_devis_particuliers").select("*")
            .eq("lead_id_livraison", lead_id).eq("statut", "livree")
            .order("livree_le", desc=True).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture des demandes de devis livrées : {e}") from e
    return {"demandes": data or []}


def get_demandes_devis_par_ids(demande_ids: tuple[str, ...]) -> dict:
    """Résolution ciblée id -> demande de devis (nom du particulier, corps de
    métier...), pour l'affichage de get_reclamations() sans dépendre de
    get_demandes_devis() (limitée aux 200 plus récentes) — même principe que
    get_leads_par_ids ci-dessus."""
    demande_ids = [d for d in demande_ids if d]
    if not demande_ids:
        return {"demandes": []}
    try:
        data = (
            supabase.table("demandes_devis_particuliers")
            .select("id,nom,corps_metier,commune,email,telephone")
            .in_("id", demande_ids).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture demandes de devis : {e}") from e
    return {"demandes": data}


def get_leads_pro_par_ids(lead_pro_ids: tuple[str, ...]) -> dict:
    """Équivalent get_demandes_devis_par_ids() ci-dessus, côté B2B
    (leads_professionnels) — même principe que get_leads_par_ids."""
    lead_pro_ids = [l for l in lead_pro_ids if l]
    if not lead_pro_ids:
        return {"leads_pro": []}
    try:
        data = (
            supabase.table("leads_professionnels").select("id,nom_entreprise,type_acteur,commune")
            .in_("id", lead_pro_ids).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads professionnels : {e}") from e
    return {"leads_pro": data}


def _dans_les_delais_reclamation(date_livraison_iso: str | None) -> bool:
    if not date_livraison_iso:
        return False
    try:
        date_livraison = datetime.fromisoformat(date_livraison_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - date_livraison) <= timedelta(days=DELAI_RECLAMATION_JOURS)


def creer_reclamation(
    type_lead: str, lead_id: str, motif: str, description_libre: str | None,
    est_admin: bool, client_lead_id: str | None = None, client_final: str | None = None,
) -> dict:
    """Crée une réclamation (B2C ou B2B), avec vérification CÔTÉ SERVEUR
    (pas seulement côté formulaire, voir demande d'origine) :
    - motif parmi les 5 valeurs objectives de l'Article 4 des CGV
      (MOTIFS_RECLAMATION) ;
    - le lead existe bien, a bien été livré, et l'a bien été à CE client
      (client_lead_id pour le B2C, client_final pour le B2B) — sauf pour un
      admin (est_admin=True), qui peut créer une réclamation pour n'importe
      quel client, même bypass de principe que
      signaler_lead_pro_invalide(est_admin=True) ci-dessus.

    Le délai de 7 jours N'EST PAS bloquant : au-delà, dans_les_delais=False
    est simplement posé sur la ligne créée — la réclamation est quand même
    enregistrée (seule l'absence de réponse du prospect est exclue comme
    motif valide par les CGV, jamais un dépassement de délai côté client).

    Renvoie {"reclamation": <ligne créée>, "dans_les_delais": bool}."""
    if type_lead not in ("b2c", "b2b"):
        raise DataAccessError("type_lead invalide.")
    if motif not in MOTIFS_RECLAMATION:
        raise DataAccessError("Motif invalide.")

    if type_lead == "b2c":
        try:
            demandes = supabase.table("demandes_devis_particuliers").select("*").eq("id", lead_id).execute().data
        except Exception as e:
            raise DataAccessError(f"Erreur lecture de la demande de devis : {e}") from e
        if not demandes:
            raise DataAccessError("Demande de devis introuvable.")
        demande = demandes[0]
        if demande.get("statut") != "livree" or not demande.get("lead_id_livraison"):
            raise DataAccessError("Cette demande de devis n'a pas encore été livrée.")
        if not est_admin and demande["lead_id_livraison"] != client_lead_id:
            raise DataAccessError("Cette demande de devis ne vous a pas été livrée.")
        client_lead_id_final = demande["lead_id_livraison"]
        client_final_final = None
        date_livraison = demande.get("livree_le")
    else:
        try:
            leads_pro = supabase.table("leads_professionnels").select("*").eq("id", lead_id).execute().data
        except Exception as e:
            raise DataAccessError(f"Erreur lecture du lead professionnel : {e}") from e
        if not leads_pro:
            raise DataAccessError("Lead professionnel introuvable.")
        lead_pro = leads_pro[0]
        if not est_admin and lead_pro.get("client_final") != client_final:
            raise DataAccessError("Ce lead ne vous a pas été livré.")
        client_lead_id_final = None
        client_final_final = lead_pro.get("client_final")
        date_livraison = lead_pro.get("created_at")

    if not date_livraison:
        raise DataAccessError("Date de livraison introuvable pour ce lead — réclamation impossible.")

    dans_delais = _dans_les_delais_reclamation(date_livraison)

    payload = {
        "type_lead": type_lead,
        "lead_id": lead_id,
        "client_lead_id": client_lead_id_final,
        "client_final": client_final_final,
        "motif": motif,
        "description_libre": (description_libre or "").strip() or None,
        "date_livraison_lead": date_livraison,
        "dans_les_delais": dans_delais,
        "statut": "en_attente",
    }
    try:
        resultat = supabase.table("reclamations").insert(payload).execute()
    except Exception as e:
        raise DataAccessError(f"Échec d'enregistrement de la réclamation : {e}") from e

    get_reclamations.clear()
    log.info(f"Réclamation créée ({type_lead}, motif={motif}, dans_les_delais={dans_delais}) pour le lead {lead_id}")
    return {"reclamation": resultat.data[0] if resultat.data else None, "dans_les_delais": dans_delais}


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_reclamations(
    statut: str | None = None, client_lead_id: str | None = None, client_final: str | None = None,
) -> dict:
    try:
        requete = supabase.table("reclamations").select("*").order("date_reclamation", desc=True)
        if statut:
            requete = requete.eq("statut", statut)
        if client_lead_id:
            requete = requete.eq("client_lead_id", client_lead_id)
        if client_final:
            requete = requete.eq("client_final", client_final)
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture réclamations : {e}") from e
    return {"reclamations": data or []}


def traiter_reclamation(reclamation_id: str, decision: str, traite_par: str, commentaire: str | None = None) -> dict:
    """Décision admin : 'acceptee' ou 'refusee'. Ne construit AUCUNE logique
    de remboursement/compensation (voir sql/init_reclamations.sql et la
    demande d'origine) — se contente de changer le statut et de tracer la
    décision (date_traitement, traite_par, commentaire_traitement). Un motif
    de refus est obligatoire (contrainte reclamation_refus_trace en base,
    revérifiée ici en amont pour un message d'erreur clair côté UI plutôt
    que de laisser Supabase renvoyer une violation de contrainte brute)."""
    if decision not in ("acceptee", "refusee"):
        raise DataAccessError("Décision invalide.")
    if not (traite_par or "").strip():
        raise DataAccessError("Merci d'indiquer qui traite cette réclamation.")
    if decision == "refusee" and not (commentaire or "").strip():
        raise DataAccessError("Un motif de refus est requis.")

    try:
        reclamations = supabase.table("reclamations").select("*").eq("id", reclamation_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture de la réclamation : {e}") from e
    if not reclamations:
        raise DataAccessError("Réclamation introuvable.")
    if reclamations[0].get("statut") != "en_attente":
        raise DataAccessError(f"Cette réclamation a déjà été traitée (statut actuel : {reclamations[0]['statut']}).")

    champs_maj = {
        "statut": decision,
        "date_traitement": datetime.now(timezone.utc).isoformat(),
        "traite_par": traite_par.strip(),
        "commentaire_traitement": (commentaire or "").strip() or None,
    }
    try:
        supabase.table("reclamations").update(champs_maj).eq("id", reclamation_id).execute()
    except Exception as e:
        raise DataAccessError(f"Échec mise à jour de la réclamation : {e}") from e

    get_reclamations.clear()
    log.info(f"Réclamation {reclamation_id} {decision} par {traite_par}")
    journaliser_action_admin(
        "traiter_reclamation", "reclamation", reclamation_id,
        {"decision": decision, "traite_par": traite_par.strip(), "commentaire": champs_maj["commentaire_traitement"]},
    )
    return champs_maj


def calculer_taux_reclamation(
    client_lead_id: str | None = None, client_final: str | None = None, jours: int = FENETRE_TAUX_RECLAMATION_JOURS,
) -> dict:
    """Taux de réclamation d'un client sur les `jours` derniers jours =
    (réclamations reçues / leads livrés) sur la même fenêtre glissante.
    Renvoie taux=0.0 avec livres=0 si aucun lead livré sur la période
    (pas assez de données pour qu'un taux ait un sens — jamais présenté
    comme une alerte dans ce cas, voir "alerte" ci-dessous)."""
    if not client_lead_id and not client_final:
        raise DataAccessError("client_lead_id ou client_final requis.")
    seuil = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()

    try:
        if client_lead_id:
            livres = (
                supabase.table("demandes_devis_particuliers").select("id", count="exact")
                .eq("lead_id_livraison", client_lead_id).eq("statut", "livree")
                .gte("livree_le", seuil).execute()
            )
        else:
            livres = (
                supabase.table("leads_professionnels").select("id", count="exact")
                .eq("client_final", client_final).gte("created_at", seuil).execute()
            )

        requete_reclamations = supabase.table("reclamations").select("id", count="exact")
        if client_lead_id:
            requete_reclamations = requete_reclamations.eq("client_lead_id", client_lead_id)
        else:
            requete_reclamations = requete_reclamations.eq("client_final", client_final)
        reclamations = requete_reclamations.gte("date_reclamation", seuil).execute()
    except Exception as e:
        raise DataAccessError(f"Erreur calcul du taux de réclamation : {e}") from e

    nb_livres = livres.count or 0
    nb_reclamations = reclamations.count or 0
    taux = (nb_reclamations / nb_livres) if nb_livres else 0.0
    return {
        "taux": taux,
        "livres": nb_livres,
        "reclamations": nb_reclamations,
        "alerte": nb_livres > 0 and taux > SEUIL_ALERTE_TAUX_RECLAMATION,
    }


def get_clients_taux_reclamation_eleve(jours: int = FENETRE_TAUX_RECLAMATION_JOURS) -> dict:
    """Calcule le taux de réclamation de TOUS les clients ayant au moins une
    réclamation sur la fenêtre, et renvoie ceux au-dessus du seuil d'alerte
    (SEUIL_ALERTE_TAUX_RECLAMATION) — pour la bannière de la vue d'ensemble
    de la page admin Réclamations. Une seule passe par client concerné
    plutôt qu'un calcul répété pour tous les clients existants (dont la
    grande majorité n'a jamais eu de réclamation)."""
    seuil = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    try:
        reclamations = (
            supabase.table("reclamations").select("client_lead_id,client_final")
            .gte("date_reclamation", seuil).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture réclamations : {e}") from e

    clients_b2c = {r["client_lead_id"] for r in reclamations if r.get("client_lead_id")}
    clients_b2b = {r["client_final"] for r in reclamations if r.get("client_final")}

    alertes = []
    for client_lead_id in clients_b2c:
        taux_info = calculer_taux_reclamation(client_lead_id=client_lead_id, jours=jours)
        if taux_info["alerte"]:
            alertes.append({"type_lead": "b2c", "client_lead_id": client_lead_id, **taux_info})
    for client_final in clients_b2b:
        taux_info = calculer_taux_reclamation(client_final=client_final, jours=jours)
        if taux_info["alerte"]:
            alertes.append({"type_lead": "b2b", "client_final": client_final, **taux_info})
    return {"alertes": alertes}


# Surveillance continue de la base (voir sql/init_sante_base_donnees.sql,
# scripts/controle_sante_bdd.py, .github/workflows/controle_sante_bdd.yml)


def get_sante_bdd_historique(jours: int = 30) -> dict:
    """Tout l'historique des contrôles sur la fenêtre demandée, le plus
    récent en premier — sert à la fois au statut instantané (première ligne
    de chaque type_controle) et à la tendance affichée par
    app_pages/sante_bdd.py."""
    seuil = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    try:
        data = (
            supabase.table("sante_base_donnees")
            .select("*")
            .gte("date_controle", seuil)
            .order("date_controle", desc=True)
            .execute()
            .data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture santé base de données : {e}") from e
    return {"controles": data or []}


def get_sante_bdd_derniers_par_type() -> dict:
    """Le résultat le plus récent de chaque type_controle (pas juste les 30
    derniers jours : un contrôle qui n'a pas tourné depuis longtemps doit
    quand même apparaître, avec sa date, plutôt que de disparaître
    silencieusement de la page)."""
    try:
        data = (
            supabase.table("sante_base_donnees")
            .select("*")
            .order("date_controle", desc=True)
            .limit(500)
            .execute()
            .data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture santé base de données : {e}") from e
    derniers: dict[str, dict] = {}
    for ligne in data or []:
        derniers.setdefault(ligne["type_controle"], ligne)
    return {"derniers": derniers}


# Module 10 (pilotage) — journal d'audit admin (voir journaliser_action_admin
# ci-dessus et sql/init_journal_audit_admin.sql)


def get_journal_audit(
    utilisateur_email: str | None = None, action: str | None = None, limit: int = 200
) -> dict:
    try:
        requete = (
            supabase.table("journal_audit_admin")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if utilisateur_email:
            requete = requete.eq("utilisateur_email", utilisateur_email)
        if action:
            requete = requete.eq("action", action)
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture journal d'audit : {e}") from e
    return {"entrees": data or []}


# Module 3 (pilotage) — coûts d'infrastructure (voir sql/init_couts_infrastructure.sql)


def get_couts_infrastructure(inclure_termines: bool = True) -> dict:
    try:
        requete = supabase.table("couts_infrastructure").select("*").order("date_debut", desc=True)
        if not inclure_termines:
            requete = requete.is_("date_fin", "null")
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture coûts d'infrastructure : {e}") from e
    return {"couts": data or []}


def ajouter_cout_infrastructure(
    service: str, cout_mensuel_centimes: int | None, pourcentage_du_ca: float | None,
    date_debut: str, notes: str | None = None,
) -> dict:
    service = (service or "").strip()
    if not service:
        raise DataAccessError("Le nom du service est requis.")
    if (cout_mensuel_centimes is None) == (pourcentage_du_ca is None):
        raise DataAccessError("Renseigner soit un coût mensuel fixe, soit un pourcentage du CA — jamais les deux, ni aucun des deux.")
    corps = {
        "service": service,
        "cout_mensuel_centimes": cout_mensuel_centimes,
        "pourcentage_du_ca": pourcentage_du_ca,
        "date_debut": date_debut,
        "notes": (notes or "").strip() or None,
    }
    try:
        data = supabase.table("couts_infrastructure").insert(corps).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur ajout coût d'infrastructure : {e}") from e
    return data[0] if data else corps


def terminer_cout_infrastructure(cout_id: str, date_fin: str) -> dict:
    try:
        data = supabase.table("couts_infrastructure").update({"date_fin": date_fin}).eq("id", cout_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur clôture coût d'infrastructure : {e}") from e
    if not data:
        raise DataAccessError("Coût introuvable.")
    return data[0]


def calculer_ca_du_mois(annee: int, mois: int) -> dict:
    """CA réel encaissé sur le mois — deux sources, jamais confondues avec
    du CA "engagé" (contrat signé mais pas payé, proposition envoyée mais
    pas honorée) :
    - contracts.montant_centimes où payment_status='paye', daté par paid_at
      (formules abonnement B2C ET achats à l'unité passés par le tunnel
      intake/contrat) ;
    - demandes_devis_particuliers.montant_centimes où
      stripe_payment_intent_id IS NOT NULL (paiement à l'unité via le
      formulaire public /demande-devis, hors tunnel intake), daté par
      livree_le — pas de colonne de date de paiement dédiée sur cette
      table ; livree_le est fiable ici car SEULES les livraisons "à
      l'unité" ont un stripe_payment_intent_id (les livraisons formule
      abonnement sont gratuites — incluses dans le contrat déjà payé,
      voir livraison_devis.py::_livrer_directement — donc jamais comptées
      deux fois)."""
    debut = f"{annee:04d}-{mois:02d}-01"
    dernier_jour = calendar.monthrange(annee, mois)[1]
    fin = f"{annee:04d}-{mois:02d}-{dernier_jour:02d}T23:59:59"
    try:
        contrats = (
            supabase.table("contracts").select("montant_centimes,type_offre,formule_abonnement")
            .eq("payment_status", "paye").gte("paid_at", debut).lte("paid_at", fin).execute().data
        )
        demandes = (
            supabase.table("demandes_devis_particuliers").select("montant_centimes,corps_metier")
            .not_.is_("stripe_payment_intent_id", "null").gte("livree_le", debut).lte("livree_le", fin).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur calcul du CA du mois : {e}") from e
    ca_contrats = sum(c["montant_centimes"] or 0 for c in contrats)
    ca_demandes = sum(d["montant_centimes"] or 0 for d in demandes)
    return {
        "annee": annee, "mois": mois,
        "ca_total_centimes": ca_contrats + ca_demandes,
        "ca_contrats_centimes": ca_contrats,
        "ca_demandes_unite_centimes": ca_demandes,
        "nb_contrats": len(contrats),
        "nb_demandes_unite": len(demandes),
        "contrats": contrats,
        "demandes": demandes,
    }


# ---------------------------------------------------------------------
# Module 8 (pilotage) — qualité des données leads. Volontairement SANS
# table dédiée (contrairement aux autres modules) : à l'échelle actuelle
# (~600 leads B2C, ~75 leads pro), tout est recalculable à la volée à
# chaque consultation — pas de valeur à figer un historique ici comme pour
# sante_base_donnees (pas de dérive lente à surveiller, juste un état
# courant à corriger). Réutilisé à l'identique par
# scripts/controle_qualite_leads.py (cron optionnel ou lancement à la
# demande) et par dashboard/app_pages/qualite_leads.py.
# ---------------------------------------------------------------------

STATUTS_LEADS_MORTS = ("invalide",)
STATUTS_LEADS_PRO_MORTS = ("invalide",)
SEUIL_JOURS_ENRICHISSEMENT_STAGNANT = 7


def _normaliser_nom(texte: str | None) -> str:
    """Normalisation volontairement simple (casse, espaces, ponctuation
    courante) — pas de correspondance floue/Levenshtein : à cette échelle,
    la variance réelle observée est "Dupont SARL" vs "dupont sarl " vs
    "Dupont, SARL", pas des fautes de frappe qui nécessiteraient un vrai
    algorithme de distance."""
    if not texte:
        return ""
    t = texte.lower().strip()
    for car in (",", ".", "  "):
        t = t.replace(car, " ")
    return " ".join(t.split())


def _grouper_doublons(lignes: list[dict], champ: str, normaliser: bool = False) -> list[dict]:
    groupes: dict[str, list[dict]] = {}
    for ligne in lignes:
        valeur = ligne.get(champ)
        if not valeur:
            continue
        cle = _normaliser_nom(valeur) if normaliser else str(valeur).strip().lower()
        if not cle:
            continue
        groupes.setdefault(cle, []).append(ligne)
    return [
        {"valeur": cle, "champ": champ, "lignes": groupe}
        for cle, groupe in groupes.items() if len(groupe) > 1
    ]


def detecter_doublons_leads() -> dict:
    """Doublons potentiels sur `leads` — email/company exacts sont déjà
    impossibles (contraintes UNIQUE idx_leads_email_unique/
    idx_leads_company_unique, voir sql/init.sql) : ne reste à détecter que
    ce que ces contraintes ne couvrent pas — même téléphone, même SIREN, ou
    company quasi-identique après normalisation légère."""
    try:
        lignes = (
            supabase.table("leads").select("id,company,telephone,siren,status,email")
            .not_.in_("id", LEADS_TEST_A_EXCLURE).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads (doublons) : {e}") from e
    return {
        "telephone": _grouper_doublons(lignes, "telephone"),
        "siren": _grouper_doublons(lignes, "siren"),
        "company_normalisee": _grouper_doublons(lignes, "company", normaliser=True),
    }


def detecter_doublons_leads_professionnels() -> dict:
    """Comme detecter_doublons_leads, mais SCOPÉ PAR client_final : la même
    entreprise (même architecte, même promoteur) sourcée pour deux clients
    B2B différents est un cas normal de cette plateforme multi-clients, pas
    un doublon — voir campagnes/outbound_chantiers. Comparer uniquement au
    sein d'une même campagne évite ce faux positif systématique."""
    try:
        lignes = (
            supabase.table("leads_professionnels")
            .select("id,nom_entreprise,telephone,siren,client_final,statut,email")
            .execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads_professionnels (doublons) : {e}") from e
    par_client: dict[str, list[dict]] = {}
    for ligne in lignes:
        par_client.setdefault(ligne["client_final"], []).append(ligne)
    resultat = {"telephone": [], "siren": [], "nom_entreprise_normalise": []}
    for client_final, sous_lignes in par_client.items():
        for cle, groupes in (
            ("telephone", _grouper_doublons(sous_lignes, "telephone")),
            ("siren", _grouper_doublons(sous_lignes, "siren")),
            ("nom_entreprise_normalise", _grouper_doublons(sous_lignes, "nom_entreprise", normaliser=True)),
        ):
            for g in groupes:
                g["client_final"] = client_final
            resultat[cle].extend(groupes)
    return resultat


def champs_manquants_leads_actifs() -> dict:
    """Sur les leads NON invalides (voir STATUTS_LEADS_MORTS) : l'e-mail est
    le SEUL canal de prospection utilisé par ce projet (aucun appel/SMS,
    voir ceo_agent.py/lead_worker.py) — un lead actif sans email n'est
    contactable par AUCUN mécanisme existant, priorité absolue. Téléphone
    manquant est secondaire (donnée commerciale utile à la revente, pas un
    canal de prospection)."""
    try:
        lignes = (
            supabase.table("leads").select("id,company,status,email,telephone")
            .not_.in_("status", list(STATUTS_LEADS_MORTS))
            .not_.in_("id", LEADS_TEST_A_EXCLURE)
            .execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads (champs manquants) : {e}") from e
    sans_email = [l for l in lignes if not l.get("email")]
    sans_telephone = [l for l in lignes if not l.get("telephone")]
    return {
        "total_actifs": len(lignes),
        "sans_email": sans_email,
        "sans_telephone": sans_telephone,
    }


def champs_manquants_leads_pro_actifs() -> dict:
    try:
        lignes = (
            supabase.table("leads_professionnels").select("id,nom_entreprise,statut,email,telephone,client_final")
            .not_.in_("statut", list(STATUTS_LEADS_PRO_MORTS)).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads_professionnels (champs manquants) : {e}") from e
    sans_email = [l for l in lignes if not l.get("email")]
    sans_telephone = [l for l in lignes if not l.get("telephone")]
    return {
        "total_actifs": len(lignes),
        "sans_email": sans_email,
        "sans_telephone": sans_telephone,
    }


def taux_enrichissement_leads_pro(seuil_jours: int = SEUIL_JOURS_ENRICHISSEMENT_STAGNANT) -> dict:
    seuil = (datetime.now(timezone.utc) - timedelta(days=seuil_jours)).isoformat()
    try:
        total = supabase.table("leads_professionnels").select("id", count="exact").execute().count or 0
        stagnants = (
            supabase.table("leads_professionnels").select("id,nom_entreprise,client_final,created_at")
            .eq("enrichissement_statut", "non_tente").lt("created_at", seuil).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture enrichissement leads_professionnels : {e}") from e
    return {
        "total": total,
        "seuil_jours": seuil_jours,
        "nb_stagnants": len(stagnants),
        "pourcentage_stagnants": round(len(stagnants) / total * 100, 1) if total else 0.0,
        "stagnants": stagnants,
    }


def score_qualite_leads() -> dict:
    """Synthèse 0-100 des 4 signaux ci-dessus, pondérée simplement (pas de
    prétention statistique) — sert de repère visuel unique en haut de la
    page, le détail par signal reste la vraie information actionnable."""
    doublons_leads = detecter_doublons_leads()
    doublons_pro = detecter_doublons_leads_professionnels()
    manquants_leads = champs_manquants_leads_actifs()
    manquants_pro = champs_manquants_leads_pro_actifs()
    enrichissement = taux_enrichissement_leads_pro()

    nb_doublons = sum(len(g) for g in doublons_leads.values()) + sum(len(g) for g in doublons_pro.values())
    total_actifs = manquants_leads["total_actifs"] + manquants_pro["total_actifs"]
    nb_sans_email = len(manquants_leads["sans_email"]) + len(manquants_pro["sans_email"])
    taux_sans_email = (nb_sans_email / total_actifs) if total_actifs else 0.0

    penalite_doublons = min(30, nb_doublons * 3)
    penalite_email = min(50, taux_sans_email * 100 * 0.6)
    penalite_enrichissement = min(20, enrichissement["pourcentage_stagnants"] * 0.4)
    score = round(max(0, 100 - penalite_doublons - penalite_email - penalite_enrichissement))

    return {
        "score": score,
        "nb_groupes_doublons": nb_doublons,
        "taux_sans_email_pct": round(taux_sans_email * 100, 1),
        "pourcentage_enrichissement_stagnant": enrichissement["pourcentage_stagnants"],
        "doublons_leads": doublons_leads,
        "doublons_leads_professionnels": doublons_pro,
        "manquants_leads": manquants_leads,
        "manquants_leads_professionnels": manquants_pro,
        "enrichissement": enrichissement,
    }


# ---------------------------------------------------------------------
# Module 5 (pilotage) — échéances légales et administratives (voir
# sql/init_echeances.sql). Inclut la vérification récurrente de l'usage
# Supabase (fusion du module 11 — API de gestion Supabase inaccessible avec
# SUPABASE_KEY, vérification manuelle retenue).
# ---------------------------------------------------------------------

SEUIL_ALERTE_ECHEANCE_JOURS = 30


def get_echeances(inclure_traitees: bool = True) -> dict:
    try:
        requete = supabase.table("echeances").select("*").order("date_echeance")
        if not inclure_traitees:
            requete = requete.eq("statut", "a_traiter")
        data = requete.execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture échéances : {e}") from e
    return {"echeances": data or []}


def ajouter_echeance(
    type_echeance: str, description: str, date_echeance: str,
    recurrence_jours: int | None = None, notes: str | None = None,
) -> dict:
    type_echeance = (type_echeance or "").strip()
    description = (description or "").strip()
    if not type_echeance or not description:
        raise DataAccessError("Type et description sont requis.")
    corps = {
        "type": type_echeance,
        "description": description,
        "date_echeance": date_echeance,
        "recurrence_jours": recurrence_jours,
        "notes": (notes or "").strip() or None,
    }
    try:
        data = supabase.table("echeances").insert(corps).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur ajout échéance : {e}") from e
    return data[0] if data else corps


def terminer_echeance(echeance_id: str) -> dict:
    """Clôture l'échéance ; si recurrence_jours est renseigné, recrée
    automatiquement la suivante decalée de N jours À PARTIR DE L'ÉCHÉANCE
    D'ORIGINE (pas de la date du jour) — voir sql/init_echeances.sql pour
    le raisonnement anti-dérive."""
    try:
        lignes = supabase.table("echeances").select("*").eq("id", echeance_id).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture échéance : {e}") from e
    if not lignes:
        raise DataAccessError("Échéance introuvable.")
    echeance = lignes[0]

    try:
        supabase.table("echeances").update({
            "statut": "traite",
            "date_traitement": datetime.now(timezone.utc).isoformat(),
        }).eq("id", echeance_id).execute()
    except Exception as e:
        raise DataAccessError(f"Erreur clôture échéance : {e}") from e

    nouvelle = None
    if echeance.get("recurrence_jours"):
        date_origine = datetime.strptime(echeance["date_echeance"], "%Y-%m-%d").date()
        nouvelle_date = date_origine + timedelta(days=echeance["recurrence_jours"])
        nouvelle = ajouter_echeance(
            echeance["type"], echeance["description"], nouvelle_date.isoformat(),
            echeance["recurrence_jours"], echeance.get("notes"),
        )
    return {"statut": "ok", "nouvelle_echeance": nouvelle}


def get_echeances_a_relancer(seuil_jours: int = SEUIL_ALERTE_ECHEANCE_JOURS) -> dict:
    """Échéances encore 'a_traiter' à moins de seuil_jours (y compris déjà
    dépassées, statut != 'traite') — utilisé par scripts/controle_echeances.py
    (cron hebdomadaire) et par la page dashboard."""
    limite = (datetime.now(timezone.utc).date() + timedelta(days=seuil_jours)).isoformat()
    try:
        data = (
            supabase.table("echeances").select("*")
            .eq("statut", "a_traiter").lte("date_echeance", limite)
            .order("date_echeance").execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture échéances à relancer : {e}") from e
    return {"echeances": data or [], "seuil_jours": seuil_jours}
