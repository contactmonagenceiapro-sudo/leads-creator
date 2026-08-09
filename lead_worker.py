#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lead_worker.py
==============

Consomme `leads.json` (produit par `scraper_batiment.py`), ne garde que les
leads disposant d'un email réellement vérifié (aucun démarchage sur une
adresse devinée/non confirmée), génère un pitch commercial personnalisé via
Ollama pour chaque lead, puis ENVOIE l'email immédiatement — le pitch
propose le service d'apport de clients qualifiés (et non plus la refonte de
site web des versions précédentes). L'envoi et l'upsert Supabase sont
désormais dans le même passage : un lead qualifié est contacté tout de
suite, pas mis en attente d'une campagne différée.

Pipeline :
    scraper_batiment.py --> leads.json --> lead_worker.py --> email envoyé + Supabase

Utilisation :
    source venv/bin/activate
    python lead_worker.py
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from alertes import CompteZohoBloqueError
from ceo_agent import send_email_prospect
from email_blacklist import emails_blacklistes
from email_tracking import demarrer_tracking, verifier_budget_quotidien
from email_validator import email_blackliste_ou_a_risque, email_status
from llm_config import LLM_API_URL, LLM_MODEL_MAIN, LLM_TIMEOUT, generer_texte

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

LEADS_FILE = Path(__file__).resolve().parent / "leads.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# Pause anti-spam entre deux ENVOIS RÉELS (alignée sur ceo_agent.py,
# relance_prospects.py et outbound_pro_btp.py — tous partagent le même
# compte Zoho, donc la même limite anti-spam). Portée à 45-90s suite à un
# premier blocage Zoho ("Unusual sending activity detected"), puis à
# 60-120s après un second blocage malgré ce premier ajustement — configurable
# sans toucher au code si besoin de réajuster.
PAUSE_MIN_SEC = int(os.getenv("PAUSE_ENVOI_MIN_SEC", "60"))
PAUSE_MAX_SEC = int(os.getenv("PAUSE_ENVOI_MAX_SEC", "120"))


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

class FormatteurPrefixe(logging.Formatter):
    PREFIXES = {
        logging.DEBUG: "[*]",
        logging.INFO: "[+]",
        logging.WARNING: "[!]",
        logging.ERROR: "[x]",
        logging.CRITICAL: "[x]",
    }

    def format(self, record):
        prefixe = self.PREFIXES.get(record.levelno, "[*]")
        return f"{prefixe} {record.getMessage()}"


def configurer_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatteurPrefixe())
    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.handlers.clear()
    racine.addHandler(handler)


log = logging.getLogger(__name__)


@dataclass
class ResultatTraitement:
    total: int = 0
    succes: int = 0
    echecs: int = 0
    ignores: int = 0
    doublons: int = 0
    a_risque: int = 0
    interrompu_bloque_compte: bool = False
    interrompu_budget_quotidien: bool = False


# ---------------------------------------------------------------------------
# CHARGEMENT DES LEADS
# ---------------------------------------------------------------------------

def charger_leads(chemin: Path = LEADS_FILE) -> list[dict]:
    """Charge et valide le fichier leads.json. Ne lève jamais d'exception :
    retourne une liste vide si le fichier est absent, vide ou corrompu."""
    if not chemin.exists():
        log.error(f"Fichier introuvable : {chemin}")
        return []

    try:
        with open(chemin, "r", encoding="utf-8") as fichier:
            data = json.load(fichier)
    except json.JSONDecodeError as erreur:
        log.error(f"JSON invalide dans {chemin} : {erreur}")
        return []
    except (IOError, OSError) as erreur:
        log.error(f"Impossible de lire {chemin} : {erreur}")
        return []

    if not isinstance(data, list):
        log.error(f"{chemin} doit contenir une liste JSON, reçu : {type(data).__name__}")
        return []

    leads_valides = []
    emails_vus: dict[str, str] = {}  # email normalisé -> company_name déjà retenu
    champs_requis = ("company_name", "industry", "weakness", "email")
    for i, lead in enumerate(data):
        if not isinstance(lead, dict) or not all(champ in lead for champ in champs_requis):
            log.warning(f"Lead #{i} ignoré : champs requis manquants ({champs_requis})")
            continue
        # Filtre qualité : on ne démarche que les entreprises disposant de
        # toutes les données utiles, et surtout d'un email réellement
        # exploitable (jamais une adresse simplement devinée/non vérifiée —
        # cf. email_source posé par scraper_batiment.py/email_enricher.py).
        # Sans email, aucun envoi n'est possible : inutile de conserver ces
        # leads dans ce pipeline de prospection immédiate.
        email = (lead.get("email") or "").strip().lower()
        if not email:
            continue
        lead["email"] = email
        # Calculé UNE fois ici (point de passage unique de tous les leads
        # avant Supabase, que l'email vienne de scraper_batiment.py ou
        # d'email_enricher.py) et persisté via inserer_lead — voir
        # sql/init_email_status.sql. Un email structurellement invalide
        # n'est plus supprimé silencieusement (voir process_pipeline) mais
        # conservé et flaggé en base.
        lead["email_status"] = email_status(email)
        # Deux entreprises DIFFÉRENTES (SIREN distincts) peuvent partager la
        # même adresse email (accueil commun, franchise...) — cas vécu :
        # 'ICONE' et '[ICONE]', deux SIREN différents, même
        # contact@icone.fr. Un seul email = une seule boîte de réception :
        # inutile (et source d'échec permanent sur idx_leads_email_unique,
        # voir inserer_lead) de tenter d'insérer/contacter les deux. On ne
        # garde que la première occurrence, jamais un choix arbitraire côté
        # base — le doublon est filtré ICI, une fois pour toutes, plutôt que
        # de retenter et échouer à chaque run.
        if email in emails_vus:
            log.warning(
                f"Lead '{lead['company_name']}' ignoré : email {email} déjà utilisé par "
                f"'{emails_vus[email]}' — une seule boîte mail, un seul lead contacté."
            )
            continue
        emails_vus[email] = lead["company_name"]
        leads_valides.append(lead)

    log.info(f"{len(leads_valides)}/{len(data)} leads retenus après filtre qualité (email requis, sans doublon d'email)")
    return leads_valides


# ---------------------------------------------------------------------------
# GÉNÉRATION IA (OLLAMA)
# ---------------------------------------------------------------------------

# Nombre de tentatives (1 essai + N reprises) avant d'abandonner
# définitivement un lead — un premier appel qui échoue par timeout est
# SOUVENT dû au chargement à froid du modèle en mémoire par Ollama (plusieurs
# dizaines de secondes pour un modèle 7B la toute première fois, ou après une
# période d'inactivité où Ollama l'a déchargé) plutôt qu'à un vrai problème :
# sans reprise, ce lead échouait définitivement pour une lenteur ponctuelle,
# alors qu'un deuxième essai juste après (modèle déjà chargé) réussit
# généralement en quelques secondes.
TENTATIVES_OLLAMA = int(os.getenv("OLLAMA_TENTATIVES", "2"))
PAUSE_RETRY_OLLAMA_SEC = 5


# Obligation légale en prospection B2B/B2C en France (Code des postes et
# communications électroniques, art. L34-5) : chaque email de prospection
# doit offrir un moyen simple de refuser d'en recevoir d'autres. Ajoutée ICI
# (après coup, sur le texte final) plutôt que confiée au prompt LLM : un
# lead réel a montré que l'IA peut ignorer une consigne explicite du prompt
# (ex: "ne mets AUCUN placeholder" pourtant non respecté sur des pitches déjà
# en base) — mieux vaut une ligne garantie à 100% qu'une consigne de plus
# que le modèle pourrait sauter. "stop" est déjà reconnu comme mot-clé de
# désinscription par mail_processor.py::MOTS_NEGATIFS, donc une réponse à ce
# mail fonctionne immédiatement sans changement côté traitement des réponses.
MENTION_DESINSCRIPTION = "\n\nPour ne plus recevoir nos messages, répondez STOP."


def _pitch_generique_repli(lead: dict) -> str:
    """Modèle de secours si le LLM (local ou cloud, voir llm_config.py) est
    indisponible — un lead qualifié ne doit jamais rester sans email juste
    parce que la génération IA a échoué (même repli que
    outbound_chantiers/outbound_pro_btp.py::_pitch_generique_repli)."""
    return (
        f"Bonjour,\n\n"
        f"{AGENCY_NAME} accompagne des entreprises du secteur "
        f"« {lead['industry']} » en leur apportant régulièrement des "
        f"demandes de devis de particuliers/professionnels réellement "
        f"intéressés par leurs prestations, dans leur zone d'activité — sans "
        f"prospection ni compétence technique de leur côté.\n\n"
        f"Seriez-vous ouvert à un premier échange pour évaluer si cela peut "
        f"être utile à {lead['company_name']} ?\n\n"
        f"Cordialement,\n{AGENCY_NAME}"
    )


def generer_pitch(lead: dict) -> str:
    """Génère un pitch commercial personnalisé via le LLM configuré (voir
    llm_config.py — local en dev, cloud en prod selon LLM_API_URL/
    OLLAMA_HOST). Retombe sur un modèle générique (_pitch_generique_repli)
    en cas d'échec réseau, timeout ou réponse vide après épuisement des
    tentatives : ne retourne jamais None et ne lève jamais d'exception, pour
    ne jamais bloquer ni vider la boucle d'envoi faute d'IA disponible.

    Angle commercial : apport de clients qualifiés (génération de leads en
    tant que service), et non plus la refonte de site web des versions
    précédentes du pipeline — l'entreprise contactée reçoit régulièrement
    des demandes de devis réelles dans sa zone d'activité, sans prospection
    de sa part."""
    prompt = (
        f"Rédige un court email de prospection B2B pour proposer à "
        f"l'entreprise '{lead['company_name']}' (secteur : {lead['industry']}) "
        f"un service d'apport de clients qualifiés de la part de "
        f"'{AGENCY_NAME}' : nous leur envoyons régulièrement des demandes de "
        f"devis de particuliers/professionnels réellement intéressés par "
        f"leurs prestations, dans leur zone d'activité, sans prospection ni "
        f"compétence technique de leur côté. Objectif de l'email : "
        f"décrocher une réponse pour en discuter, pas vendre directement. "
        f"Ton direct et concret, pas de jargon marketing. "
        f"IMPORTANT : ne mets AUCUN placeholder entre crochets (pas de "
        f"'[Votre nom]', '[Votre fonction]' etc.) — termine simplement par "
        f"'Cordialement,' suivi de '{AGENCY_NAME}', rien d'autre après."
    )

    pitch = generer_texte(
        prompt,
        model=LLM_MODEL_MAIN,
        timeout=LLM_TIMEOUT,
        tentatives=TENTATIVES_OLLAMA,
        pause_retry_sec=PAUSE_RETRY_OLLAMA_SEC,
    )
    if pitch:
        return pitch + MENTION_DESINSCRIPTION

    log.warning(
        f"IA indisponible ({LLM_API_URL}) pour {lead['company_name']} — "
        f"utilisation du pitch générique de repli."
    )
    return _pitch_generique_repli(lead) + MENTION_DESINSCRIPTION


# ---------------------------------------------------------------------------
# ANTI-DOUBLON D'ENVOI
# ---------------------------------------------------------------------------

def deja_contacte(company_name: str) -> bool:
    """Vérifie directement dans Supabase si une entreprise a déjà été
    contactée, AVANT de générer/envoyer un nouveau pitch. Nécessaire car ce
    script tourne à intervalle régulier (leads_agent_job) : sans ce
    contrôle, relire le même leads.json à chaque run réenverrait un email au
    même destinataire à chaque exécution."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        reponse = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            params={"select": "contacted", "company": f"eq.{company_name}"},
            timeout=10,
        )
        lignes = reponse.json()
        return bool(lignes) and lignes[0].get("contacted") is True
    except (requests.exceptions.RequestException, ValueError) as erreur:
        log.error(f"Erreur vérification anti-doublon pour {company_name} : {erreur}")
        return False


# ---------------------------------------------------------------------------
# PERSISTANCE (upsert Supabase direct)
# ---------------------------------------------------------------------------

def _conflit_email_deja_existant(reponse: requests.Response) -> bool:
    """Vrai si l'échec Supabase est UNIQUEMENT dû à idx_leads_email_unique
    (code Postgres 23505, contrainte email) — voir inserer_lead. Un email
    peut légitimement déjà exister sur une AUTRE ligne (nom d'entreprise
    scrapé différemment d'un run à l'autre pour la même société) : ce n'est
    pas un problème d'infra/API à traiter comme un échec, juste un doublon
    de données à ignorer."""
    if reponse.status_code != 409:
        return False
    try:
        corps = reponse.json()
    except ValueError:
        return False
    return corps.get("code") == "23505" and "email" in (corps.get("message") or "").lower()


def inserer_lead(lead: dict, pitch: str | None, envoi_reussi: bool, marquer_invalide: bool = False) -> bool | None:
    """Upsert le lead dans Supabase directement, avec anti-doublon sur
    l'entreprise (on_conflict=company + resolution=merge-duplicates).

    Anciennement dédupliqué par email : depuis que scraper_batiment.py et
    email_enricher.py peuvent légitimement laisser email=None (aucun domaine
    réel trouvé pour l'entreprise), un upsert basé sur l'email seul ne
    protège plus contre les doublons — chaque nouveau run recréait une ligne
    par entreprise sans email au lieu de mettre à jour l'existante. Le nom de
    l'entreprise (company), lui, est toujours renseigné et constitue la clé
    d'identité stable côté Supabase (contrainte idx_leads_company_unique,
    cf. sql/init.sql).

    MAIS idx_leads_email_unique (contrainte historique, jamais retirée)
    existe TOUJOURS en parallèle : Postgres ne résout un conflit ON CONFLICT
    que sur la contrainte explicitement ciblée (company) — un email déjà
    présent sur une AUTRE ligne (même entreprise scrapée sous un nom
    légèrement différent d'un run à l'autre) fait donc toujours échouer cet
    upsert avec un 409, même si l'entreprise elle-même n'est pas en doublon.
    Sans distinction, ce cas — un simple doublon de données, pas un problème
    d'infra — comptait comme un échec métier à part entière (cf. code retour
    ci-dessous, cas vécu : lead_worker.py sorti en code 1 pour cette seule
    raison alors qu'aucune tentative n'avait réellement échoué).

    Renvoie True (upsert réussi), False (échec réel : réseau, autre
    contrainte...), ou None (doublon d'email bénin, voir ci-dessus — ni un
    succès ni un échec).

    envoi_reussi reflète si l'email de prospection a réellement été envoyé
    (cf. process_pipeline) : si oui, contacted/status/contacted_at sont mis à
    jour pour refléter l'envoi (cohérent avec ceo_agent.py) ; sinon le lead
    est simplement préparé (pitch stocké) pour un nouvel essai au run
    suivant, sans jamais marquer un envoi qui n'a pas eu lieu.

    marquer_invalide=True (email structurellement invalide ou déjà
    blacklisté, voir process_pipeline) force status='invalide' — jamais
    resélectionné par ceo_agent.py::get_leads_from_supabase, mais la ligne
    reste en base (pas supprimée) avec email_status renseigné pour
    traçabilité."""
    email_source = lead.get("email_source", "inconnu")
    ville = lead.get("ville", "")
    db_payload = {
        "company": lead["company_name"],
        "industry": lead["industry"],
        "weakness": lead["weakness"],
        "email": lead["email"],
        "email_status": lead.get("email_status", "unknown"),
        "telephone": lead.get("telephone"),
        "siren": lead.get("siren"),
        "adresse": lead.get("adresse"),
        # Colonne structurée (en plus de la mention "ville=" dans "notes" ci-
        # dessous, conservée telle quelle) : nécessaire pour croiser chaque
        # artisan avec le signal de dynamisme communal (voir
        # sql/init_commune_leads.sql pour la reconstitution des leads déjà
        # en base au moment de cette migration).
        "commune": ville or None,
        "tranche_effectif_salarie": lead.get("tranche_effectif_salarie"),
        "pitch_commercial": pitch,
        "source": "scraper_batiment",
        # Traçabilité de la confiance dans l'email (email_verifie_site,
        # domaine_verifie_sans_email, aucun_domaine_trouve) : à utiliser pour
        # prioriser/filtrer avant un envoi réel, cf. email_enricher.py.
        "notes": f"ville={ville} | email_source={email_source}",
    }
    if envoi_reussi:
        db_payload["status"] = "contacte_attente_reponse"
        db_payload["contacted"] = True
        db_payload["contacted_at"] = datetime.now(timezone.utc).isoformat()
    elif marquer_invalide:
        db_payload["status"] = "invalide"
    else:
        db_payload["status"] = "a_contacter"

    try:
        reponse = requests.post(
            f"{SUPABASE_URL}/rest/v1/leads",
            params={"on_conflict": "company"},
            json=db_payload,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation,resolution=merge-duplicates",
            },
            timeout=15,
        )
    except requests.exceptions.RequestException as erreur:
        log.error(f"Échec réseau Supabase pour {lead['company_name']} : {erreur}")
        return False

    if reponse.status_code in (200, 201):
        log.info(f"Lead '{lead['company_name']}' upserté avec succès dans Supabase (code {reponse.status_code})")
        if envoi_reussi:
            # Journalise l'envoi pour le budget quotidien partagé (voir
            # email_tracking.py::verifier_budget_quotidien) — l'id Supabase
            # n'est connu qu'après cet upsert (leads.json n'en a pas).
            try:
                lignes = reponse.json()
                if lignes:
                    demarrer_tracking("lead_artisan", lignes[0]["id"])
            except (ValueError, KeyError, IndexError):
                pass
        return True

    if _conflit_email_deja_existant(reponse):
        log.warning(
            f"Lead '{lead['company_name']}' non inséré : l'email {lead['email']} existe déjà "
            "sur une autre fiche — doublon probable (même entreprise scrapée sous un nom "
            "différent), pas une erreur à traiter."
        )
        return None

    log.error(f"Échec Supabase pour '{lead['company_name']}' (code {reponse.status_code}) : {reponse.text}")
    return False


# ---------------------------------------------------------------------------
# ORCHESTRATION
# ---------------------------------------------------------------------------

def process_pipeline() -> ResultatTraitement:
    configurer_logging()
    log.info("=== Démarrage du Lead Worker ===")

    leads = charger_leads()
    resultat = ResultatTraitement(total=len(leads))

    if not leads:
        log.info("Aucun nouveau lead à traiter (leads.json vide ou sans lead exploitable).")
        return resultat

    # Trie par score décroissant avant traitement : à budget d'envoi
    # quotidien limité (voir plus bas), les leads les mieux qualifiés
    # doivent être contactés en premier plutôt que dans l'ordre arbitraire
    # du scraping — même logique que
    # outbound_chantiers/outbound_pro_btp.py (order=score_final.desc).
    # Actuellement un NO-OP : leads.json ne porte pas encore de score (le
    # calcul du score lui-même est une étape à venir, pas encore
    # implémentée) — score absent ⇒ 0 partout ⇒ tri stable ⇒ ordre
    # inchangé. S'activera automatiquement dès qu'un score sera présent,
    # sans autre changement de code ici.
    leads = sorted(leads, key=lambda l: -(l.get("score") or 0))

    log.info(f"{len(leads)} leads valides chargés, début du traitement...")

    # Chargée UNE fois pour tout le run (pas par lead) : voir
    # email_blacklist.py / email_validator.py, même principe que partout
    # ailleurs dans ce projet.
    blacklist = emails_blacklistes()

    # Budget quotidien PARTAGÉ avec le pipeline B2B (même boîte Zoho, même
    # réputation à protéger) — voir email_tracking.py::verifier_budget_quotidien.
    # Décrémenté localement à chaque envoi réussi, jamais recalculé par lead.
    budget_restant = verifier_budget_quotidien()["budget_restant"]
    log.info(f"Budget d'envoi quotidien restant (partagé avec le B2B) : {budget_restant}")

    for index, lead in enumerate(leads, 1):
        log.info(f"--- [Lead {index}/{len(leads)}] {lead['company_name']} ---")

        if deja_contacte(lead["company_name"]):
            log.info(f"'{lead['company_name']}' déjà contacté précédemment, ignoré.")
            resultat.ignores += 1
            continue

        if budget_restant <= 0:
            log.warning(
                f"Plafond de warmup quotidien atteint (partagé avec le B2B) — "
                f"traitement interrompu après [Lead {index}/{len(leads)}], reprise possible demain."
            )
            resultat.interrompu_budget_quotidien = True
            break

        # Email structurellement invalide (voir charger_leads) : ni pitch IA
        # ni tentative d'envoi, mais conservé et flaggé en base plutôt que
        # silencieusement écarté — voir inserer_lead(marquer_invalide=True).
        # 'unknown' (vérification MX indisponible) n'est volontairement PAS
        # bloqué ici, même principe que email_validator.possede_enregistrement_mx :
        # on ne pénalise jamais un lead pour une panne d'infra qui nous est
        # propre.
        if lead["email_status"] in ("invalid_syntax", "invalid_domain"):
            log.warning(f"Email de '{lead['company_name']}' structurellement invalide ({lead['email_status']}) : {lead['email']}")
            resultat.a_risque += 1
            inserer_lead(lead, pitch=None, envoi_reussi=False, marquer_invalide=True)
            continue

        # Filet de sécurité en plus du filtrage déjà fait en amont par
        # email_enricher.py (qui écarte déjà les emails à risque avant même
        # d'écrire leads.json) : couvre un leads.json généré par une version
        # antérieure du pipeline, ou modifié manuellement. Ni un succès ni
        # un échec métier — juste un lead qu'on refuse sciemment de risquer.
        a_risque, raison = email_blackliste_ou_a_risque(lead["email"], blacklist=blacklist)
        if a_risque:
            log.warning(f"Email de '{lead['company_name']}' écarté ({raison}) : {lead['email']}")
            resultat.a_risque += 1
            inserer_lead(lead, pitch=None, envoi_reussi=False, marquer_invalide=True)
            continue

        pitch = generer_pitch(lead)

        sujet = f"{lead['company_name']} — apport de clients qualifiés"
        try:
            envoi_reussi = send_email_prospect(lead["email"], sujet, pitch)
        except CompteZohoBloqueError:
            # Déjà loggé + alerté dans send_email_prospect — inutile de
            # tenter les leads restants, ils échoueraient tous de la même
            # façon tant que le blocage n'est pas levé côté Zoho.
            log.error(f"Traitement interrompu après [Lead {index}/{len(leads)}] (compte Zoho bloqué).")
            resultat.interrompu_bloque_compte = True
            break
        if envoi_reussi:
            log.info(f"Email envoyé avec succès à {lead['company_name']} <{lead['email']}>")
        else:
            log.error(f"Échec d'envoi à {lead['company_name']} <{lead['email']}>")

        resultat_insert = inserer_lead(lead, pitch, envoi_reussi)
        if resultat_insert is True:
            resultat.succes += 1
        elif resultat_insert is None:
            resultat.doublons += 1
        else:
            resultat.echecs += 1

        if envoi_reussi:
            budget_restant -= 1

        time.sleep(random.uniform(PAUSE_MIN_SEC, PAUSE_MAX_SEC))

    if (
        resultat.succes == 0 and resultat.echecs == 0 and resultat.doublons == 0
        and resultat.a_risque == 0 and resultat.total > 0
        and not resultat.interrompu_bloque_compte and not resultat.interrompu_budget_quotidien
    ):
        # Tous les leads chargés étaient déjà contactés (cas normal d'un run
        # répété sur le même leads.json, ex: bouton "Traitement IA des leads"
        # cliqué deux fois) — jamais un échec, rien de neuf à signaler.
        log.info("Aucun nouveau lead à traiter (tous déjà contactés).")

    if resultat.interrompu_bloque_compte:
        suffixe_interruption = " — INTERROMPU (compte Zoho bloqué)"
    elif resultat.interrompu_budget_quotidien:
        suffixe_interruption = " — INTERROMPU (plafond de warmup quotidien atteint, reprise demain)"
    else:
        suffixe_interruption = ""
    log.info(
        f"=== Terminé : {resultat.succes} succès / {resultat.echecs} échecs / "
        f"{resultat.doublons} doublon(s) / {resultat.a_risque} email(s) à risque écarté(s) / "
        f"{resultat.ignores} déjà contacté(s) sur {resultat.total} leads{suffixe_interruption} ==="
    )
    return resultat


def main() -> int:
    """Code retour : 0 tant qu'aucune tentative n'a RÉELLEMENT échoué
    (échec Ollama, envoi ou écriture Supabase), y compris quand il n'y avait
    rien à faire (leads.json vide, ou tous les leads déjà contactés) — ce
    n'est pas une erreur, juste un run sans action nécessaire (cas vécu :
    172 leads tous déjà contactés faisait sortir ce script en code 1 avant
    ce correctif, rapporté à tort comme une erreur bloquante dans le
    dashboard)."""
    resultat = process_pipeline()
    return 0 if resultat.echecs == 0 and not resultat.interrompu_bloque_compte else 1


if __name__ == "__main__":
    sys.exit(main())
