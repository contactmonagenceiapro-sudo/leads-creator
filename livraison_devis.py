#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
livraison_devis.py
===================

Rapproche chaque demande de devis publique (table demandes_devis_particuliers,
formulaire générique /demande-devis — voir dashboard/pages_publiques.py::
afficher_demande_devis) avec un artisan client actif (table leads,
status='paye') dont le corps de métier correspond exactement et dont la
zone d'intervention (intake_responses.communes_couvertes) couvre la commune
de la demande. Conception validée le 18/08/2026.

Deux formules de livraison, cohérentes avec le modèle déjà en place
(generation_contrats.py) :
- abonnement : livraison DIRECTE dans le quota mensuel inclus (statut
  'livree' immédiat, coordonnées complètes envoyées tout de suite), tant
  que le quota du cycle de facturation en cours n'est pas atteint (cycle
  ancré sur contracts.paid_at, cohérent avec la récurrence Stripe réelle).
- à l'unité : PROPOSITION d'abord (statut 'proposee', lien de paiement
  Stripe à usage unique envoyé, AUCUNE coordonnée du particulier
  communiquée) — la livraison réelle (coordonnées complètes) n'a lieu
  qu'après confirmation MANUELLE du paiement par l'admin (voir
  dashboard/data_access.py::marquer_demande_devis_payee_et_livree), jamais
  de facturation automatique : ce projet n'enregistre aucune carte
  (uniquement des Payment Links à usage unique, voir
  dashboard/contrats_signature.py::creer_et_envoyer_lien_paiement, même
  limite) — et révéler les coordonnées avant paiement reviendrait à
  livrer gratuitement un produit payant.

Plusieurs artisans éligibles pour la même demande -> UN SEUL candidat à la
fois, jamais de diffusion simultanée (cohérent avec un lead vendu comme
qualifié/exclusif, pas une marketplace de leads partagés), départagés par
ROUND-ROBIN (celui n'ayant pas reçu de livraison depuis le plus longtemps
en premier) plutôt que par leads.score : ce score mesure la qualité du
PROSPECT à l'acquisition (activité chantiers de sa commune + taille
d'entreprise), pas sa pertinence pour CETTE demande précise — le
réutiliser pour départager favoriserait toujours les mêmes gros clients.

Aucun artisan éligible -> statut 'en_attente_artisan', retraitée à chaque
run (un nouvel artisan peut signer entre-temps) — visible dans le dashboard
("Demandes de devis") pour prioriser le démarchage commercial sur ce corps
de métier/cette zone.

Proposition à l'unité périmée (DELAI_EXPIRATION_PROPOSITION_HEURES, 48h par
défaut) sans paiement : retombe sur le candidat round-robin suivant,
l'artisan qui vient d'expirer étant explicitement exclu de cette
ré-attribution immédiate (sinon le round-robin, basé sur les livraisons
RÉELLEMENT effectuées, le repropose aussitôt — une proposition non payée ne
compte pas comme "son tour" pour les FUTURES demandes, mais ne doit pas non
plus faire boucler indéfiniment sur les deux mêmes artisans pour LA MÊME
demande).

Limite connue (v1, non résolue ici) : le rapprochement compare
`demande.commune` à `intake.communes_couvertes` par égalité de chaîne
EXACTE — un particulier ayant saisi un code postal au lieu du nom de
commune (le formulaire public accepte les deux, voir pages_publiques.py)
ne matchera aucun artisan tant qu'aucune résolution code postal -> commune
n'est ajoutée.

Confirmation par email préalable (statut_confirmation, voir
sql/init_demandes_devis_particuliers_confirmation.sql) : le rapprochement
round-robin ci-dessus ne considère JAMAIS une demande dont le particulier
n'a pas cliqué le lien de confirmation reçu par email dans les 24h suivant
son dépôt (voir dashboard/pages_publiques.py::afficher_demande_devis /
afficher_devis pour l'envoi, afficher_confirmation_demande pour la
confirmation elle-même) — traiter_demandes_en_attente() filtre explicitement
statut_confirmation='confirme', et expirer_confirmations_perimees()
ci-dessous fait passer à 'expire' toute demande non confirmée sous 24h,
appelée par ce même script (donc par le même cron horaire,
.github/workflows/livraison_devis.yml) juste avant expirer_propositions_perimees().

Usage :
    python3 livraison_devis.py                  # expire les confirmations puis les propositions périmées, PUIS traite les demandes en attente
    python3 livraison_devis.py --expirer-seul    # n'expire que les confirmations et propositions périmées
"""

from __future__ import annotations

import argparse
import calendar
import logging
import os
from datetime import datetime, timedelta, timezone

import stripe
from dotenv import load_dotenv
from supabase import create_client

from alertes import CompteZohoBloqueError
from ceo_agent import send_email_prospect
from generation_contrats import (
    TYPE_OFFRE_ABONNEMENT,
    TYPE_OFFRE_UNITE,
    formule_abonnement_par_id,
    palier_pour_score,
    prix_lead_unite_eur,
)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LIVRAISON-DEVIS] %(message)s")
log = logging.getLogger(__name__)

DELAI_EXPIRATION_PROPOSITION_HEURES = int(os.getenv("DELAI_EXPIRATION_PROPOSITION_HEURES", "48"))
# Délai de confirmation par email avant tout rapprochement (voir
# sql/init_demandes_devis_particuliers_confirmation.sql et
# dashboard/pages_publiques.py::afficher_confirmation_demande) — distinct de
# DELAI_EXPIRATION_PROPOSITION_HEURES ci-dessus (celui-là court APRÈS la
# proposition à un artisan, celui-ci AVANT, sur le consentement du
# particulier lui-même).
DELAI_EXPIRATION_CONFIRMATION_HEURES = int(os.getenv("DELAI_EXPIRATION_CONFIRMATION_HEURES", "24"))

# Garde-fou de sécurité PROPRE à ce script, VOLONTAIREMENT indépendant du
# budget partagé de warmup cold-outreach (email_tracking.py::
# verifier_budget_quotidien, utilisé par ceo_agent.py/lead_worker.py/
# relance_prospects.py/outbound_pro_btp.py) : ces envois-ci sont
# transactionnels, adressés à des artisans DÉJÀ clients payants, sous
# l'engagement public de délai ("un professionnel qualifié vous recontacte
# sous 48h maximum", voir dashboard/pages_publiques.py) — ils ne doivent
# jamais être retardés par un budget de prospection à froid déjà épuisé
# (paliers très bas au départ, 2/jour les 6 premiers jours). Ce plafond
# protège seulement contre un bug qui enverrait un volume anormal en boucle,
# pas contre l'usage normal (voir memory livraison_devis_pas_de_plafond_envoi).
PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN = int(os.getenv("PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN", "30"))

# Verrou partagé (voir sql/init_livraison_devis_lock.sql) — même doctrine
# que mail_processor.py::_acquerir_verrou/_liberer_verrou (mail_check_lock) :
# le cron horaire (.github/workflows/livraison_devis.yml, runner GitHub
# Actions) et le bouton manuel du dashboard (dashboard/process_runner.py::
# lancer_livraison_devis, subprocess Streamlit Community Cloud) lancent
# tous les deux CE MÊME script, sur des machines différentes — aucun état
# en mémoire/fichier local ne peut donc coordonner les deux. Sans ce verrou,
# un chevauchement peut livrer la même demande à deux artisans différents
# (viole la garantie "un seul candidat à la fois" documentée plus haut) ou
# dépasser le quota d'un abonnement (_quota_disponible est un check-then-act).
LIVRAISON_DEVIS_SOURCE = os.getenv("LIVRAISON_DEVIS_SOURCE", "manuel")
VERROU_STALE_MINUTES = 15  # > le timeout-minutes:10 du workflow GitHub Actions


def _acquerir_verrou() -> bool:
    """Tentative d'acquisition atomique du verrou (UPDATE conditionnel —
    Postgres sérialise les UPDATE concurrents au niveau de la ligne, donc un
    seul appelant peut voir sa condition satisfaite même en cas de double
    déclenchement simultané). True si acquis, False si déjà tenu par un run
    en cours et non périmé."""
    seuil_perime = (datetime.now(timezone.utc) - timedelta(minutes=VERROU_STALE_MINUTES)).isoformat()
    try:
        reponse = (
            supabase.table("livraison_devis_lock")
            .update({"locked_at": datetime.now(timezone.utc).isoformat(), "locked_by": LIVRAISON_DEVIS_SOURCE})
            .eq("id", 1)
            .or_(f"locked_at.is.null,locked_at.lt.{seuil_perime}")
            .execute()
        )
        return bool(reponse.data)
    except Exception as e:
        # Panne Supabase (ou migration sql/init_livraison_devis_lock.sql pas
        # encore appliquée) au moment d'acquérir le verrou : on préfère
        # laisser passer le run (comportement d'avant l'introduction du
        # verrou) plutôt que de bloquer indéfiniment la livraison pour une
        # simple indisponibilité de la table de verrouillage.
        log.warning(f"Verrou livraison_devis indisponible, run lancé sans coordination : {e}")
        return True


def _liberer_verrou() -> None:
    try:
        supabase.table("livraison_devis_lock").update({"locked_at": None, "locked_by": None}).eq("id", 1).execute()
    except Exception as e:
        log.warning(f"Impossible de libérer le verrou livraison_devis (se libérera de lui-même après {VERROU_STALE_MINUTES} min) : {e}")


def _artisans_clients_actifs() -> list[dict]:
    """Artisans clients payants (leads.status='paye'), avec leur dernier
    intake (corps_metier/communes_couvertes) et leur contrat (type_offre/
    formule_abonnement/paid_at) déjà attachés — quelques requêtes groupées
    plutôt qu'une par artisan (le volume reste faible, mais autant éviter
    le pattern N+1 dès le départ)."""
    leads = supabase.table("leads").select("id,email,company,score").eq("status", "paye").execute().data
    if not leads:
        return []
    lead_ids = [l["id"] for l in leads]

    intakes = (
        supabase.table("intake_responses").select("lead_id,corps_metier,communes_couvertes,created_at")
        .in_("lead_id", lead_ids).order("created_at", desc=True).execute().data
    )
    dernier_intake_par_lead: dict[str, dict] = {}
    for intake in intakes:
        # Le premier rencontré pour un lead_id donné est le plus récent
        # (tri desc ci-dessus) : setdefault ignore silencieusement les
        # suivants, plus anciens.
        dernier_intake_par_lead.setdefault(intake["lead_id"], intake)

    contrats = (
        supabase.table("contracts").select("lead_id,type_offre,formule_abonnement,paid_at")
        .in_("lead_id", lead_ids).execute().data
    )
    contrat_par_lead = {c["lead_id"]: c for c in contrats}

    artisans = []
    for lead in leads:
        intake = dernier_intake_par_lead.get(lead["id"])
        contrat = contrat_par_lead.get(lead["id"])
        if not intake or not contrat:
            # Payant mais sans intake/contrat exploitable : donnée
            # incohérente (ne devrait pas arriver en usage normal), on
            # ignore ce candidat plutôt que de planter tout le run.
            continue
        artisans.append({**lead, "intake": intake, "contrat": contrat})
    return artisans


def _artisans_eligibles(corps_metier: str | None, commune: str | None, artisans: list[dict]) -> list[dict]:
    if not corps_metier or not commune:
        return []
    return [
        a for a in artisans
        if a["intake"].get("corps_metier") == corps_metier
        and commune in (a["intake"].get("communes_couvertes") or [])
    ]


def _derniere_livraison_par_artisan() -> dict[str, str]:
    lignes = (
        supabase.table("demandes_devis_particuliers").select("lead_id_livraison,livree_le")
        .eq("statut", "livree").not_.is_("lead_id_livraison", "null").execute().data
    )
    derniere: dict[str, str] = {}
    for ligne in lignes:
        lid, le = ligne["lead_id_livraison"], ligne.get("livree_le")
        if le and (lid not in derniere or le > derniere[lid]):
            derniere[lid] = le
    return derniere


def _ordonner_round_robin(candidats: list[dict]) -> list[dict]:
    """Le candidat n'ayant JAMAIS reçu de livraison passe en premier ; entre
    deux candidats déjà livrés au moins une fois, le moins récemment servi
    passe en premier. Une proposition 'à l'unité' non payée ne compte
    jamais comme une livraison ici (voir traiter_demande/exclure_ids pour
    la gestion propre du cas d'expiration)."""
    dernieres = _derniere_livraison_par_artisan()

    def cle(candidat: dict):
        derniere = dernieres.get(candidat["id"])
        return (derniere is not None, derniere or "")

    return sorted(candidats, key=cle)


def _ajouter_un_mois(dt: datetime) -> datetime:
    """+1 mois calendaire, sans dépendance externe (pas de python-dateutil
    dans requirements.txt) — gère le débordement de jour (ex: 31 janvier ->
    28/29 février) en retombant sur le dernier jour du mois cible."""
    mois = dt.month + 1
    annee = dt.year + (mois - 1) // 12
    mois = (mois - 1) % 12 + 1
    dernier_jour = calendar.monthrange(annee, mois)[1]
    return dt.replace(year=annee, month=mois, day=min(dt.day, dernier_jour))


def _debut_cycle_facturation(paid_at_iso: str | None) -> datetime:
    """Ancre le cycle mensuel du quota sur contracts.paid_at, cohérent avec
    la récurrence Stripe réelle (interval='month' depuis cette date, voir
    dashboard/contrats_signature.py::creer_et_envoyer_lien_paiement) plutôt
    qu'un mois calendaire arbitraire. Repli sur "il y a 30 jours" si
    paid_at est absent (ne devrait pas arriver pour un contrat 'paye', mais
    ne doit jamais lever d'exception)."""
    maintenant = datetime.now(timezone.utc)
    if not paid_at_iso:
        return maintenant - timedelta(days=30)
    debut_cycle = datetime.fromisoformat(paid_at_iso.replace("Z", "+00:00"))
    while True:
        prochain = _ajouter_un_mois(debut_cycle)
        if prochain > maintenant:
            return debut_cycle
        debut_cycle = prochain


def _quota_disponible(candidat: dict) -> bool:
    formule = formule_abonnement_par_id(candidat["contrat"].get("formule_abonnement"))
    debut_cycle = _debut_cycle_facturation(candidat["contrat"].get("paid_at"))
    deja_livrees = (
        supabase.table("demandes_devis_particuliers").select("id")
        .eq("lead_id_livraison", candidat["id"]).eq("statut", "livree")
        .gte("livree_le", debut_cycle.isoformat()).execute().data
    )
    return len(deja_livrees) < formule["volume"]


def _livrer_directement(demande: dict, candidat: dict) -> bool:
    """Formule abonnement : la livraison EST l'email (aucune ressource
    externe créée contrairement à _proposer) — si l'envoi échoue, la
    demande n'est PAS marquée livrée, pour être retentée au run suivant
    plutôt que de mentir sur l'état réel."""
    corps = (
        f"Bonjour,\n\nNouvelle demande de devis dans votre zone ({demande.get('commune') or '—'}), "
        f"corps de métier {demande.get('corps_metier')} :\n\n"
        f"Nom : {demande.get('nom')}\n"
        f"E-mail : {demande.get('email') or '—'}\n"
        f"Téléphone : {demande.get('telephone') or '—'}\n"
        f"Besoin décrit : {demande.get('message') or '—'}\n\n"
        f"Cette demande compte dans votre volume mensuel inclus.\n\nCordialement"
    )
    if not send_email_prospect(candidat["email"], "Nouvelle demande de devis", corps, lead_id=candidat["id"]):
        log.error(
            f"Échec d'envoi de la livraison à {candidat.get('company')} pour la demande "
            f"{demande['id']} — non marquée livrée, retentée au prochain run."
        )
        return False

    supabase.table("demandes_devis_particuliers").update({
        "statut": "livree",
        "lead_id_livraison": candidat["id"],
        "livree_le": datetime.now(timezone.utc).isoformat(),
    }).eq("id", demande["id"]).execute()
    log.info(f"Demande {demande['id']} livrée directement à {candidat.get('company')} (abonnement).")
    return True


def _proposer(demande: dict, candidat: dict) -> bool:
    """Formule à l'unité : propose SANS révéler les coordonnées complètes
    (nom/email/téléphone du particulier), lien de paiement Stripe à usage
    unique. Renvoie False si le lien n'a pas pu être créé (clé Stripe
    absente/invalide, ou erreur API) — la demande n'est ALORS PAS modifiée
    (reste dans son état d'avant l'appel), retentée au prochain run.

    Si le lien est créé avec succès mais que l'email échoue ensuite, l'état
    'proposee' est quand même persisté (le lien Stripe est une ressource
    réelle et valide, inutile de la jeter) — visible dans le dashboard,
    récupérable manuellement par l'admin si besoin."""
    montant_centimes = int(prix_lead_unite_eur(candidat.get("score")) * 100)
    try:
        prix = stripe.Price.create(
            unit_amount=montant_centimes, currency="eur",
            product_data={
                "name": f"Demande de devis — {demande.get('corps_metier')} — {demande.get('commune') or ''}"
            },
        )
        lien = stripe.PaymentLink.create(
            line_items=[{"price": prix.id, "quantity": 1}],
            metadata={"demande_id": demande["id"], "lead_id": candidat["id"]},
        )
    except stripe.error.StripeError as e:
        log.error(f"Échec création du lien de paiement pour la demande {demande['id']} : {e}")
        return False

    supabase.table("demandes_devis_particuliers").update({
        "statut": "proposee",
        "lead_id_livraison": candidat["id"],
        "proposee_le": datetime.now(timezone.utc).isoformat(),
        "stripe_payment_link_id": lien.id,
        "stripe_payment_url": lien.url,
        "montant_centimes": montant_centimes,
    }).eq("id", demande["id"]).execute()

    palier = palier_pour_score(candidat.get("score"))
    corps = (
        f"Bonjour,\n\nUne nouvelle demande de devis correspond à votre activité "
        f"({demande.get('corps_metier')}, {demande.get('commune') or '—'}) :\n\n"
        f"Besoin décrit : {demande.get('message') or '—'}\n\n"
        f"Palier : {palier} — {montant_centimes / 100:.2f} € TTC.\n"
        f"Les coordonnées complètes du client vous sont communiquées dès réception du paiement :\n{lien.url}\n\n"
        f"Cette proposition vous est réservée {DELAI_EXPIRATION_PROPOSITION_HEURES}h — passé ce délai, "
        f"elle sera proposée à un autre artisan.\n\nCordialement"
    )
    if not send_email_prospect(candidat["email"], "Nouvelle demande de devis disponible", corps, lead_id=candidat["id"]):
        log.warning(
            f"Lien de paiement créé pour la demande {demande['id']} mais échec d'envoi à "
            f"{candidat.get('company')} — la proposition reste valide (visible dans le dashboard)."
        )
    else:
        log.info(f"Demande {demande['id']} proposée à {candidat.get('company')} (à l'unité, {montant_centimes / 100:.2f}€).")
    return True


def _envois_livraison_aujourdhui() -> int:
    """Compte les livraisons/propositions déjà envoyées aujourd'hui par CE
    script (livree_le ou proposee_le du jour dans demandes_devis_particuliers)
    — délibérément lu depuis cette table plutôt que depuis email_events
    (email_tracking.py) : ce dernier agrège aussi la prospection à froid
    sous le même tag 'lead_artisan', ce qui empêcherait de distinguer
    proprement le volume de CE garde-fou de celui du warmup cold-outreach
    (voir PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN ci-dessus). proposee_le est
    posé même si l'email échoue ensuite (voir _proposer) : légèrement
    conservateur, ce qui convient à un garde-fou de sécurité."""
    debut_jour_iso = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    livrees = (
        supabase.table("demandes_devis_particuliers").select("id", count="exact")
        .gte("livree_le", debut_jour_iso).execute()
    )
    proposees = (
        supabase.table("demandes_devis_particuliers").select("id", count="exact")
        .gte("proposee_le", debut_jour_iso).execute()
    )
    return (livrees.count or 0) + (proposees.count or 0)


def traiter_demande(demande: dict, artisans: list[dict], exclure_ids: frozenset = frozenset()) -> str:
    """Traite une demande 'a_qualifier' ou 'en_attente_artisan' (ou une
    demande de proposition périmée déjà réinitialisée par
    expirer_propositions_perimees) : cherche le prochain candidat éligible
    (round-robin, en excluant `exclure_ids`), tente la livraison/
    proposition, essaie le candidat suivant si celui-ci échoue (quota
    atteint entre-temps, ou lien Stripe non créable/email non envoyable),
    et retombe sur 'en_attente_artisan' si aucun candidat ne fonctionne.

    Renvoie l'issue : 'livree' / 'proposee' / 'en_attente_artisan'."""
    eligibles = [
        a for a in _artisans_eligibles(demande.get("corps_metier"), demande.get("commune"), artisans)
        if a["id"] not in exclure_ids
    ]

    for candidat in _ordonner_round_robin(eligibles):
        type_offre = candidat["contrat"].get("type_offre")
        if type_offre == TYPE_OFFRE_ABONNEMENT:
            if not _quota_disponible(candidat):
                continue
            if _livrer_directement(demande, candidat):
                return "livree"
        elif type_offre == TYPE_OFFRE_UNITE:
            if _proposer(demande, candidat):
                return "proposee"
        # type_offre inconnu/absent (donnée incohérente) : candidat ignoré, suivant.

    if demande.get("statut") != "en_attente_artisan":
        supabase.table("demandes_devis_particuliers").update({"statut": "en_attente_artisan"}).eq("id", demande["id"]).execute()
    return "en_attente_artisan"


def traiter_demandes_en_attente() -> dict:
    # statut_confirmation='confirme' : une demande non confirmée par le
    # particulier (voir sql/init_demandes_devis_particuliers_confirmation.sql)
    # ne doit jamais entrer dans le rapprochement round-robin, quel que soit
    # son statut de traitement — voir expirer_confirmations_perimees()
    # ci-dessous pour la sortie automatique de la file d'attente (statut
    # 'expire') des demandes jamais confirmées.
    demandes = (
        supabase.table("demandes_devis_particuliers").select("*")
        .in_("statut", ["a_qualifier", "en_attente_artisan"])
        .eq("statut_confirmation", "confirme").execute().data
    )
    compteurs = {"livree": 0, "proposee": 0, "en_attente_artisan": 0}
    if not demandes:
        log.info("=== Terminé : aucune demande à traiter ===")
        return {"livrees": 0, "proposees": 0, "en_attente": 0, "total": 0}

    artisans = _artisans_clients_actifs()
    plafond_restant = PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN - _envois_livraison_aujourdhui()
    for i, demande in enumerate(demandes, start=1):
        if plafond_restant <= 0:
            log.error(
                f"Garde-fou de sécurité atteint ({PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN} envois/jour, "
                f"indépendant du warmup prospection) — {len(demandes) - i + 1} demande(s) non traitée(s), "
                f"retentées au prochain run."
            )
            break
        try:
            issue = traiter_demande(demande, artisans)
        except CompteZohoBloqueError:
            # Déjà loggé + alerté dans send_email_prospect — inutile de
            # continuer, les envois suivants échoueraient tous pareil tant
            # que le blocage n'est pas levé côté Zoho.
            log.error(f"Traitement interrompu après {i - 1}/{len(demandes)} demande(s) (compte Zoho bloqué).")
            break
        compteurs[issue] += 1
        if issue in ("livree", "proposee"):
            plafond_restant -= 1

    log.info(
        f"=== Terminé : {sum(compteurs.values())}/{len(demandes)} demande(s) traitée(s) — "
        f"{compteurs['livree']} livrée(s), {compteurs['proposee']} proposée(s), "
        f"{compteurs['en_attente_artisan']} en attente d'artisan ==="
    )
    return {
        "livrees": compteurs["livree"], "proposees": compteurs["proposee"],
        "en_attente": compteurs["en_attente_artisan"], "total": len(demandes),
    }


def expirer_propositions_perimees() -> dict:
    seuil = (datetime.now(timezone.utc) - timedelta(hours=DELAI_EXPIRATION_PROPOSITION_HEURES)).isoformat()
    perimees = (
        supabase.table("demandes_devis_particuliers").select("*")
        .eq("statut", "proposee").lt("proposee_le", seuil).execute().data
    )
    if not perimees:
        log.info("=== Terminé : aucune proposition périmée ===")
        return {"reattribuees": 0, "en_attente": 0, "total": 0}

    artisans = _artisans_clients_actifs()
    plafond_restant = PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN - _envois_livraison_aujourdhui()
    reattribuees, en_attente = 0, 0
    for i, demande in enumerate(perimees, start=1):
        if plafond_restant <= 0:
            log.error(
                f"Garde-fou de sécurité atteint ({PLAFOND_ENVOI_LIVRAISON_QUOTIDIEN} envois/jour, "
                f"indépendant du warmup prospection) — {len(perimees) - i + 1} proposition(s) périmée(s) "
                f"non ré-attribuée(s), retentées au prochain run."
            )
            break
        candidat_expire = demande.get("lead_id_livraison")
        if candidat_expire:
            # Module 9 (pilotage) — journalise l'expiration AVANT le reset
            # ci-dessous qui efface lead_id_livraison : seule table qui
            # conserve cette info dans la durée (voir sql/init_propositions_expirees.sql
            # pour pourquoi elle est nécessaire). Best-effort : un échec
            # d'écriture ne doit jamais bloquer la ré-attribution réelle.
            try:
                supabase.table("propositions_expirees").insert({
                    "demande_id": demande["id"],
                    "artisan_id": candidat_expire,
                    "corps_metier": demande.get("corps_metier"),
                    "commune": demande.get("commune"),
                    "montant_centimes": demande.get("montant_centimes"),
                    "proposee_le": demande.get("proposee_le"),
                }).execute()
            except Exception as e:
                log.error(f"Échec journalisation proposition expirée (demande {demande['id']}) : {e}")
        # Réinitialise AVANT de retenter : le candidat suivant qui échouerait
        # à son tour doit retomber sur le même état par défaut qu'une
        # demande jamais encore traitée (voir traiter_demande, qui gère
        # aussi bien 'a_qualifier' que 'en_attente_artisan').
        supabase.table("demandes_devis_particuliers").update({
            "statut": "a_qualifier",
            "lead_id_livraison": None,
            "proposee_le": None,
            "stripe_payment_link_id": None,
            "stripe_payment_url": None,
            "montant_centimes": None,
        }).eq("id", demande["id"]).execute()
        demande_reinitialisee = {**demande, "statut": "a_qualifier", "lead_id_livraison": None}
        exclure = frozenset({candidat_expire}) if candidat_expire else frozenset()

        try:
            issue = traiter_demande(demande_reinitialisee, artisans, exclure_ids=exclure)
        except CompteZohoBloqueError:
            log.error(f"Ré-attribution interrompue après {i - 1}/{len(perimees)} proposition(s) périmée(s) (compte Zoho bloqué).")
            break

        if issue in ("livree", "proposee"):
            reattribuees += 1
            plafond_restant -= 1
        else:
            en_attente += 1
        log.info(f"Proposition périmée pour la demande {demande['id']} (candidat précédent exclu) -> {issue}.")

    log.info(
        f"=== Terminé : {reattribuees + en_attente}/{len(perimees)} proposition(s) périmée(s) traitée(s) — "
        f"{reattribuees} ré-attribuée(s), {en_attente} en attente d'artisan ==="
    )
    return {"reattribuees": reattribuees, "en_attente": en_attente, "total": len(perimees)}


def expirer_confirmations_perimees() -> dict:
    """Toute demande encore 'en_attente_confirmation' plus de
    DELAI_EXPIRATION_CONFIRMATION_HEURES (24h par défaut) après
    date_envoi_confirmation passe automatiquement à 'expire' — jamais
    transmise à un artisan, jamais réattribuée (voir traiter_demandes_en_attente,
    qui exclut déjà tout statut_confirmation != 'confirme' ; ce passage à
    'expire' rend surtout l'état visible dans le dashboard admin plutôt que
    de changer un comportement déjà garanti par ce filtre).

    Repli sur created_at si date_envoi_confirmation est resté NULL (échec
    d'envoi de l'email de confirmation lui-même, voir
    dashboard/pages_publiques.py::_envoyer_email_confirmation_demande) :
    sans ce repli, une telle demande resterait bloquée indéfiniment en
    'en_attente_confirmation' (la comparaison SQL/postgrest sur une colonne
    NULL ne devient jamais vraie), invisible et jamais nettoyée."""
    en_attente = (
        supabase.table("demandes_devis_particuliers").select("id,date_envoi_confirmation,created_at")
        .eq("statut_confirmation", "en_attente_confirmation").execute().data
    )
    if not en_attente:
        log.info("=== Terminé : aucune confirmation en attente périmée ===")
        return {"expirees": 0, "total": 0}

    seuil = datetime.now(timezone.utc) - timedelta(hours=DELAI_EXPIRATION_CONFIRMATION_HEURES)
    expirees = 0
    for demande in en_attente:
        reference = demande.get("date_envoi_confirmation") or demande.get("created_at")
        if not reference:
            continue
        envoye_le = datetime.fromisoformat(reference.replace("Z", "+00:00"))
        if envoye_le < seuil:
            supabase.table("demandes_devis_particuliers").update({"statut_confirmation": "expire"}).eq("id", demande["id"]).execute()
            expirees += 1

    log.info(f"=== Terminé : {expirees}/{len(en_attente)} confirmation(s) en attente expirée(s) ===")
    return {"expirees": expirees, "total": len(en_attente)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapproche les demandes de devis avec les artisans clients actifs.")
    parser.add_argument(
        "--expirer-seul", action="store_true",
        help="N'expire que les propositions périmées, sans traiter les nouvelles demandes.",
    )
    args = parser.parse_args()

    if not _acquerir_verrou():
        log.warning("Une livraison de devis est déjà en cours (bouton manuel ou run automatique) — run ignoré.")
        return 0
    try:
        expirer_confirmations_perimees()
        expirer_propositions_perimees()
        if args.expirer_seul:
            return 0
        traiter_demandes_en_attente()
        return 0
    finally:
        _liberer_verrou()


if __name__ == "__main__":
    raise SystemExit(main())
