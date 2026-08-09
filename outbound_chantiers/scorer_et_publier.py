"""
MODULE 2/3 (fusion) — Scoring final et publication en base.

Combine :
- le poids de son type d'acteur, tel que défini dans la campagne active
  (table `campagnes`, champ types_acteur_cibles[].poids — générique,
  aucun type en dur ici) ;
- le signal d'activité chantiers de sa commune (module 1b, agrégé, jamais
  nominatif).

Publie ensuite chaque acteur ayant un contact exploitable dans la table
Supabase `leads_professionnels`, via l'endpoint générique /sb_insert déjà
exposé par l'API (api/main.py) — dédoublonnage sur (client_final,
nom_entreprise), cohérent avec le reste du projet.

Alerte temps réel (Discord + e-mail) dès qu'un acteur NOUVELLEMENT publié
dépasse SEUIL_LEAD_ULTRA_QUALIFIE (voir outbound_chantiers/config.py) — la
liste des acteurs déjà en base est récupérée une seule fois en début de run
pour ne jamais réalerter sur un acteur déjà connu à chaque nouveau passage du
pipeline (le sourcing quotidien peut retrouver les mêmes acteurs plusieurs
jours de suite).

Usage :
    python3 -m outbound_chantiers.scorer_et_publier
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from alertes import alerter_discord, envoyer_alerte_email
from email_validator import email_status
from outbound_chantiers.config import (
    BONUS_MAX_PRESENCE_WEB,
    BONUS_MAX_TAILLE_ENTREPRISE,
    CLIENT_FINAL,
    POIDS_ACTIVITE_CHANTIERS,
    POIDS_TYPE_ACTEUR,
    SEUIL_LEAD_ULTRA_QUALIFIE,
)
from outbound_chantiers.signal_activite_chantiers import recuperer_activite_par_commune, score_pour_commune

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCORING] %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

FICHIER_ENTREE = Path(__file__).parent / "acteurs_pro_enrichis.json"

# Code INSEE de tranche d'effectif salarié (établissement) -> score 0-1,
# croissant puis plafonné : au-delà d'une cinquantaine de salariés, "plus
# gros" n'apporte plus d'information supplémentaire utile pour juger de la
# pertinence d'un partenariat avec un client BTP de petite taille (le sujet
# ici est la taille probable des chantiers gérés, pas un objectif de score
# maximal pour les plus grosses structures). Absente/inconnue ('NN', valeur
# vide) -> 0.0, jamais de pénalité faute de donnée SIRENE.
SCORES_TRANCHE_EFFECTIF = {
    "00": 0.0,   # 0 salarié
    "01": 0.15,  # 1 à 2
    "02": 0.3,   # 3 à 5
    "03": 0.45,  # 6 à 9
    "11": 0.6,   # 10 à 19
    "12": 0.75,  # 20 à 49
    "21": 0.9,   # 50 à 99
    "22": 1.0,   # 100 à 199
    "31": 1.0, "32": 1.0, "41": 1.0, "42": 1.0, "51": 1.0, "52": 1.0, "53": 1.0,
}


def score_taille_entreprise(tranche_effectif_salarie: str | None) -> float:
    return SCORES_TRANCHE_EFFECTIF.get(tranche_effectif_salarie or "", 0.0)


def score_presence_web(acteur: dict) -> float:
    """Composite à partir des signaux déjà collectés par l'enrichissement
    (module 2), sans nouvelle source : site propre (0.5 — le plus engageant,
    signale une vraie vitrine professionnelle), LinkedIn (0.3), autres
    réseaux sociaux (0.2). Plafonné à 1.0."""
    score = 0.0
    if acteur.get("site_web"):
        score += 0.5
    if acteur.get("linkedin_url"):
        score += 0.3
    if acteur.get("reseaux_sociaux"):
        score += 0.2
    return min(score, 1.0)


def calculer_score(acteur: dict, activite_par_commune: dict[str, float]) -> float:
    poids_type = POIDS_TYPE_ACTEUR.get(acteur["type_acteur"], 0.5)
    score_activite = score_pour_commune(acteur.get("commune") or "", activite_par_commune)

    # Score de base : moyenne pondérée entre la pertinence du type d'acteur
    # et le dynamisme de sa zone — pondération INCHANGÉE (50/50).
    score_base = (poids_type * (1 - POIDS_ACTIVITE_CHANTIERS)) + (score_activite * POIDS_ACTIVITE_CHANTIERS)

    # Bonus plafonnés (taille d'entreprise, présence web) : affinent le
    # score en marge sans jamais dominer le score de base ci-dessus — voir
    # BONUS_MAX_TAILLE_ENTREPRISE / BONUS_MAX_PRESENCE_WEB (config.py).
    bonus = (
        score_taille_entreprise(acteur.get("tranche_effectif_salarie")) * BONUS_MAX_TAILLE_ENTREPRISE
        + score_presence_web(acteur) * BONUS_MAX_PRESENCE_WEB
    )

    return round(min(score_base + bonus, 1.0), 3)


def publier_en_base(acteur: dict, est_nouveau: bool = True) -> bool:
    # Statut structurel de l'email (voir email_validator.py, sql/init_email_status.sql) :
    # toujours mis à jour (utile pour le reporting), mais ne force
    # statut='invalide' QUE pour un acteur encore jamais publié (est_nouveau) —
    # jamais sur un republish (le sourcing quotidien retrouve les mêmes
    # acteurs plusieurs jours de suite), pour ne jamais écraser la
    # progression réelle d'un acteur déjà engagé (contacte_attente_reponse,
    # interested...) si son domaine s'avère invalide après coup.
    statut_email = email_status(acteur.get("email") or "")
    payload = {
        "client_final": CLIENT_FINAL,
        "type_acteur": acteur["type_acteur"],
        "nom_entreprise": acteur["nom_entreprise"],
        "siren": acteur.get("siren"),
        "commune": acteur.get("commune"),
        "code_postal": acteur.get("code_postal"),
        "adresse": acteur.get("adresse"),
        "site_web": acteur.get("site_web"),
        "email": acteur.get("email"),
        "email_status": statut_email,
        "telephone": acteur.get("telephone"),
        "linkedin_url": acteur.get("linkedin_url"),
        "reseaux_sociaux": acteur.get("reseaux_sociaux") or {},
        "enrichissement_statut": acteur.get("enrichissement_statut") or "non_tente",
        "enrichi_at": datetime.now(timezone.utc).isoformat(),
        "tranche_effectif_salarie": acteur.get("tranche_effectif_salarie"),
        "score_activite_chantiers": acteur["score_activite_chantiers"],
        "score_final": acteur["score_final"],
    }
    if est_nouveau and statut_email in ("invalid_syntax", "invalid_domain"):
        payload["statut"] = "invalide"
    elif est_nouveau and statut_email == "email_introuvable":
        # Sort proprement ces acteurs de la file d'envoi automatique
        # (outbound_pro_btp.py::lancer_campagne_initiale ne sélectionne que
        # statut='a_contacter' ET exige un email — sans cette bascule, un
        # acteur sans email reste indéfiniment en tête de file, re-sélectionné
        # puis silencieusement ignoré à chaque run) — reste visible et
        # filtrable dans le dashboard pour un contact manuel (téléphone :
        # quasi systématiquement disponible pour ce cas, voir diagnostic).
        payload["statut"] = "necessite_contact_manuel"
    try:
        reponse = requests.post(
            f"{SUPABASE_URL}/rest/v1/leads_professionnels",
            params={"on_conflict": "client_final,nom_entreprise"},
            json=payload,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation,resolution=merge-duplicates",
            },
            timeout=10,
        )
        if reponse.status_code >= 400:
            log.error(f"Échec publication {acteur['nom_entreprise']} : {reponse.status_code} {reponse.text}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur réseau publication {acteur['nom_entreprise']} : {e}")
        return False


def _noms_deja_en_base(client_final: str) -> set[str]:
    """Récupéré UNE fois par run (pas par acteur) pour savoir quels acteurs
    sont déjà connus — sert uniquement à ne pas ré-alerter sur un acteur
    ultra-qualifié déjà signalé lors d'un run précédent."""
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads_professionnels",
            params={"select": "nom_entreprise", "client_final": f"eq.{client_final}"},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            timeout=10,
        )
        if reponse.status_code == 200:
            return {a["nom_entreprise"] for a in reponse.json()}
    except requests.exceptions.RequestException as e:
        log.error(f"Impossible de vérifier les acteurs déjà en base (alerte lead qualifié ignorée) : {e}")
    return set()


def _alerter_si_ultra_qualifie(acteur: dict) -> None:
    if acteur["score_final"] < SEUIL_LEAD_ULTRA_QUALIFIE:
        return
    message = (
        f"🌟 **Lead ultra-qualifié** — {acteur['nom_entreprise']} ({acteur['type_acteur']}, "
        f"{acteur.get('commune') or '?'}) pour {CLIENT_FINAL} — score {acteur['score_final']:.2f}"
    )
    alerter_discord(message)
    envoyer_alerte_email(
        f"[{CLIENT_FINAL}] Lead ultra-qualifié : {acteur['nom_entreprise']}",
        message.replace("**", ""),
    )


def scorer_et_publier() -> None:
    if not FICHIER_ENTREE.exists():
        log.error(f"{FICHIER_ENTREE} introuvable — lancer d'abord enrichir_acteurs_pro.py")
        return

    acteurs = json.loads(FICHIER_ENTREE.read_text(encoding="utf-8"))
    activite_par_commune = recuperer_activite_par_commune()
    noms_existants = _noms_deja_en_base(CLIENT_FINAL)

    publies, ignores = 0, 0
    for acteur in acteurs:
        if not acteur.get("contact_exploitable"):
            ignores += 1
            continue

        try:
            est_nouveau = acteur.get("nom_entreprise") not in noms_existants
            acteur["score_activite_chantiers"] = score_pour_commune(acteur.get("commune") or "", activite_par_commune)
            acteur["score_final"] = calculer_score(acteur, activite_par_commune)

            if publier_en_base(acteur, est_nouveau=est_nouveau):
                publies += 1
                if est_nouveau:
                    _alerter_si_ultra_qualifie(acteur)
        except Exception as e:
            # Ne doit jamais interrompre la publication des acteurs restants
            # (ex: champ manquant/malformé sur cet acteur précis).
            log.error(f"Publication ignorée pour {acteur.get('nom_entreprise') or acteur.get('email') or '?'} : {e}")

    log.info(f"Publication terminée : {publies} acteurs publiés, {ignores} ignorés (aucun contact exploitable)")
    if not acteurs:
        log.warning(
            "Aucun acteur en entrée (acteurs_pro_enrichis.json vide) — le sourcing (module 1) "
            "n'a probablement rien trouvé. Vérifier les logs de sourcing_acteurs_pro.py."
        )
    elif publies == 0:
        log.warning(
            f"0 acteur publié sur {len(acteurs)} traités — soit aucun n'a de contact "
            "exploitable (site/email/téléphone introuvable), soit la publication Supabase "
            "échoue systématiquement (vérifier que la table leads_professionnels existe : "
            "sql/init_leads_professionnels.sql)."
        )


if __name__ == "__main__":
    scorer_et_publier()
