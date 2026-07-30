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

import glob
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import stripe

from supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DASHBOARD] %(message)s")
log = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

SEUIL_LEAD_ULTRA_QUALIFIE = float(os.getenv("SEUIL_LEAD_ULTRA_QUALIFIE", "0.85"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

RACINE_REPO = Path(__file__).resolve().parent.parent

CACHE_TTL_SECONDES = 30


class DataAccessError(Exception):
    """Erreur levée par les fonctions de ce module — remplace ApiError.
    Toujours interceptée par common.py::safe_call/executer_avec_spinner."""
    pass


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
        leads_count = supabase.table("leads").select(count="exact").execute().count or 0
        articles_count = supabase.table("articles").select(count="exact").execute().count or 0
        reports = (
            supabase.table("ceo_reports")
            .select("*").order("created_at", desc=True).limit(1).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture statistiques : {e}") from e
    return {
        "leads_total": leads_count,
        "articles_generes": articles_count,
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
        return supabase.table("leads").select(count="exact").gte("last_relance_at", depuis_iso).execute().count or 0
    except Exception as e:
        log.error(f"Erreur lecture du nombre de relances envoyées : {e}")
        return 0


@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_health() -> dict:
    """État réel des dépendances externes (Supabase, Ollama, Zoho, Discord).
    Ollama est probablement injoignable depuis Streamlit Cloud (mode dégradé
    assumé) — ce n'est pas une erreur bloquante, juste une information."""
    import requests

    resultat = {}
    try:
        supabase.table("campagnes").select("id").limit(1).execute()
        resultat["supabase"] = "ok"
    except Exception as e:
        resultat["supabase"] = f"down ({e})"

    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
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
            .order("created_at", desc=True).limit(limit).execute().data
        )
    except Exception as e:
        raise DataAccessError(f"Erreur lecture leads : {e}") from e
    return {"leads": data}


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


def enrichir_lead_pro(lead_pro_id: str) -> dict:
    """Ré-enrichissement à la demande (admin) — même logique que le pipeline
    automatique (module 2). Ne remplace jamais une donnée déjà connue par un
    résultat vide : seuls les champs manquants sont mis à jour."""
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
        if cle != "contact_exploitable" and valeur and not lead_pro.get(cle)
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

@st.cache_data(ttl=CACHE_TTL_SECONDES)
def get_campagnes() -> dict:
    try:
        data = supabase.table("campagnes").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        raise DataAccessError(f"Erreur lecture campagnes : {e}") from e
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
