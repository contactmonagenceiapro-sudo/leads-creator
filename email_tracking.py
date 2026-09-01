"""
Journalisation des envois d'e-mail — insert Supabase direct de l'événement
'envoye' (table email_events) — ET calcul du budget quotidien partagé de
montée en charge (warmup), voir verifier_budget_quotidien() plus bas.

Le pixel invisible + les liens redirigés (ouverture/clic) ont été retirés :
ils dépendaient des routes /track/open et /track/click du backend FastAPI,
supprimé au profit d'une app 100% Streamlit. Les e-mails repartent donc en
texte brut uniquement (voir ceo_agent.py::send_email_prospect,
outbound_chantiers/outbound_pro_btp.py::envoyer_email).

Module VOLONTAIREMENT à la racine (pas dans outbound_chantiers/), sans
dépendance à outbound_chantiers.config : importé aussi bien par
ceo_agent.py/lead_worker.py/relance_prospects.py (pipeline artisans/B2C)
que par outbound_chantiers/outbound_pro_btp.py (B2B) — les deux pipelines
envoient depuis LA MÊME boîte Zoho (ZOHO_USER), donc partagent la même
réputation à protéger et le même budget quotidien (voir
verifier_budget_quotidien, PALIERS_RAMP_ENVOI_QUOTIDIEN)."""

import logging
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRACKING] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# === Montée en charge progressive du volume d'envoi (warmup) ===
# La boîte d'envoi (ZOHO_USER) est PARTAGÉE par TOUTES les campagnes, B2B
# ET artisans/B2C — ce plafond protège la réputation de cette boîte/ce
# domaine dans son ensemble, jamais une seule campagne ou un seul pipeline
# (voir verifier_budget_quotidien, qui agrège lead_artisan ET
# lead_professionnel — limitation corrigée : seul le B2B était plafonné
# avant, alors que le pipeline artisans envoie depuis la même boîte sans
# aucune limite quotidienne).
#
# Réduits DRASTIQUEMENT (5/j initial -> 2/j, plateau atteint en 3 semaines
# -> 2 mois) suite à un blocage de sécurité Zoho réel ("Unusual sending
# activity detected", SMTP 550 5.4.6) — un rejet explicite au niveau du
# compte, pas un simple ralentissement à prévoir. Complète un warmup tiers
# (Instantly/Lemwarm/...) — ne le remplace pas : un warmup actif construit
# la réputation, ce plafond évite d'envoyer un volume réel disproportionné
# pendant que cette réputation se construit encore. À desserrer prudemment
# (paliers suivants seulement, jamais en sautant en avant) une fois
# plusieurs semaines sans nouveau blocage.
PALIERS_RAMP_ENVOI_QUOTIDIEN = [
    (0, 2),    # jours 1-6 : 2 e-mails/jour max, TOUS pipelines confondus
    (7, 4),    # semaine 2
    (14, 8),   # semaine 3
    (21, 15),  # semaine 4
    (30, 25),  # semaines 5-6
    (45, 40),  # semaines 7-8
    (60, 50),  # au-delà (plateau, atteint en ~2 mois au lieu de 3 semaines)
]


def plafond_envoi_du_jour(jours_ecoules: int) -> int:
    """Plafond d'e-mails de prospection (artisans + B2B confondus)
    autorisé aujourd'hui, selon le palier de montée en charge atteint.
    jours_ecoules=0 le jour du tout premier envoi jamais effectué (voir
    PALIERS_RAMP_ENVOI_QUOTIDIEN)."""
    plafond = PALIERS_RAMP_ENVOI_QUOTIDIEN[0][1]
    for seuil_jours, valeur in PALIERS_RAMP_ENVOI_QUOTIDIEN:
        if jours_ecoules >= seuil_jours:
            plafond = valeur
    return plafond


def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _email_events_get(params: str) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/email_events?{params}", headers=_supabase_headers(), timeout=10
        )
        if reponse.status_code != 200:
            # Avant ce correctif, une réponse non-200 (ex: filtre malformé)
            # retombait silencieusement sur [] — voir incident réel du
            # 29/08/2026 : un "+" non encodé dans un timestamp ISO
            # (created_at=gte.2026-08-29T00:00:00+00:00) était décodé en
            # espace côté PostgREST ("... 00:00", invalid input syntax), et
            # l'échec passait totalement inaperçu : comptage à 0, budget
            # quotidien perçu comme intact, ET le cron GitHub Actions se
            # terminait quand même en "succeeded" (aucune exception ne
            # remontait). Voir le correctif ci-dessous (quote() sur
            # debut_jour_iso) pour la cause racine ; ceci reste une
            # deuxième ligne de défense pour toute AUTRE requête
            # malformée future — on lève au lieu de rendre [] pour que
            # l'appelant (verifier_budget_quotidien -> lancer_campagne_initiale/
            # lancer_relances -> pipeline_outbound_chantiers.py::executer_etape)
            # échoue explicitement (log ÉCHEC + exit code 1) plutôt que de
            # continuer avec un budget calculé sur des données fausses.
            log.error(f"Erreur lecture email_events : HTTP {reponse.status_code} — {reponse.text}")
            raise RuntimeError(f"Requête email_events invalide : HTTP {reponse.status_code} — {reponse.text}")
        return reponse.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur lecture email_events : {e}")
        return []


def _parser_horodatage(valeur: str) -> datetime:
    valeur = valeur.replace("Z", "+00:00")
    if "." in valeur:
        base, reste = valeur.split(".", 1)
        # Postgres tronque parfois les zéros de fin des microsecondes — le
        # parseur strict de Python n'accepte que 0, 3 ou 6 chiffres.
        chiffres = "".join(c for c in reste if c.isdigit())
        fuseau = reste[len(chiffres):] or "+00:00"
        valeur = f"{base}.{chiffres.ljust(6, '0')[:6]}{fuseau}"
    return datetime.fromisoformat(valeur)


def verifier_budget_quotidien() -> dict:
    """Où en est la montée en charge progressive du domaine d'envoi
    (warmup) — voir PALIERS_RAMP_ENVOI_QUOTIDIEN ci-dessus. Agrège TOUS les
    envois de prospection, artisans ET B2B confondus (lead_type IN
    ('lead_artisan', 'lead_professionnel')) : la boîte d'envoi est
    partagée, sa réputation ne se découpe pas par pipeline ni par
    campagne.

    À appeler par CHAQUE script qui envoie de la prospection à froid,
    AVANT de commencer sa boucle d'envoi (voir ceo_agent.py, lead_worker.py,
    relance_prospects.py, outbound_chantiers/outbound_pro_btp.py) — jamais
    une requête par lead, un seul calcul en début de run, décrémenté
    localement au fil des envois réussis (même principe que le reste du
    projet, ex: email_blacklist.emails_blacklistes)."""
    premiers = _email_events_get(
        "select=created_at&type_evenement=eq.envoye&order=created_at.asc&limit=1"
    )
    maintenant = datetime.now(timezone.utc)

    if not premiers:
        jour_ramp = 1  # tout premier envoi jamais effectué : c'est aujourd'hui le jour 1
    else:
        premier_envoi = _parser_horodatage(premiers[0]["created_at"])
        jour_ramp = (maintenant.date() - premier_envoi.date()).days + 1

    plafond_jour = plafond_envoi_du_jour(jour_ramp - 1)

    debut_jour_iso = maintenant.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    # quote() est indispensable ici : _email_events_get() reçoit une URL déjà
    # construite en chaîne (pas de params= géré par requests, qui aurait
    # encodé automatiquement) — un "+" littéral (ex: "...T00:00:00+00:00")
    # part donc tel quel dans le querystring, où PostgREST le décode comme un
    # espace (convention application/x-www-form-urlencoded), invalidant le
    # timestamp côté Postgres. Voir _email_events_get pour l'incident réel.
    envoyes_aujourdhui = len(_email_events_get(
        f"select=id&type_evenement=eq.envoye&created_at=gte.{quote(debut_jour_iso, safe='')}"
    ))

    return {
        "jour_ramp": jour_ramp,
        "plafond_jour": plafond_jour,
        "envoyes_aujourdhui": envoyes_aujourdhui,
        "budget_restant": max(0, plafond_jour - envoyes_aujourdhui),
        "paliers": PALIERS_RAMP_ENVOI_QUOTIDIEN,
    }


def demarrer_tracking(lead_type: str, lead_id: str, client_final: str | None = None) -> str:
    """Crée un identifiant unique pour cet envoi et journalise l'événement
    'envoye' (utilisé par statut_ramp_warmup pour le comptage quotidien).
    Ne bloque JAMAIS l'envoi : si Supabase est injoignable, un identifiant
    local est quand même renvoyé, l'événement reste simplement orphelin."""
    tracking_id = str(uuid.uuid4())
    if not SUPABASE_URL or not SUPABASE_KEY:
        return tracking_id
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/email_events",
            headers=_supabase_headers(),
            json={
                "tracking_id": tracking_id,
                "type_evenement": "envoye",
                "lead_type": lead_type,
                "lead_id": lead_id,
                "client_final": client_final,
            },
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        log.error(f"Échec journalisation envoi (tracking_id={tracking_id}) : {e}")
    return tracking_id
