"""
Calculs financiers purs pour app_pages/finances.py — aucune dépendance à
Streamlit ni appel réseau (uniquement des dict/list en entrée/sortie), pour
rester testable en isolation avec des données synthétiques, sans avoir
besoin d'un contexte Streamlit ni d'écrire quoi que ce soit en base.

PÉRIMÈTRE : les contrats traités ici couvrent EXCLUSIVEMENT le B2C (vente
de leads aux artisans, tunnel intake -> Yousign -> Stripe) — voir
data_access.get_contracts_finances(). Le B2B (campagnes clients) n'a aucune
facturation persistée en base à ce jour ; ce module ne produit donc aucun
chiffre B2B, à la page appelante de l'expliquer plutôt que d'inventer un
CA B2B à partir de rien.

Confirmation de signature 100% MANUELLE (pas de webhook Yousign, voir
data_access.marquer_contrat_signe) : `yousign_status` est mis à jour par un
admin qui vérifie lui-même dans Yousign. Le paiement (`payment_status`), lui,
est confirmé automatiquement par le webhook Stripe (voir
scripts/traiter_paiements_stripe.py, sql/init_stripe_webhook_events.sql) —
data_access.marquer_contrat_paye reste un filet de secours manuel. D'où
deux indicateurs distincts ci-dessous ("signé" et "payé"), jamais confondus
l'un avec l'autre.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from generation_contrats import (
    FORMULES_ABONNEMENT,
    LIBELLE_PALIER,
    PRIX_PAR_PALIER_EUR,
    TYPE_OFFRE_ABONNEMENT,
    TYPE_OFFRE_UNITE,
)

# Reconstruit le palier (Premium/Standard/Basique) à partir du montant payé
# — non stocké tel quel sur `contracts` (seul le prix final l'est), voir
# generation_contrats.py::PRIX_PAR_PALIER_EUR pour le point de vérité unique.
_PALIER_PAR_MONTANT_EUR = {prix: cle for cle, prix in PRIX_PAR_PALIER_EUR.items()}
_LABEL_FORMULE_ABONNEMENT = {f["id"]: f["label"] for f in FORMULES_ABONNEMENT}


def _parse_dt(valeur: str | None) -> datetime | None:
    if not valeur:
        return None
    try:
        dt = datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def enrichir(contrats: list[dict]) -> list[dict]:
    """Ajoute les champs dérivés (`_...`) utilisés par toutes les fonctions
    ci-dessous, sans muter les dicts d'origine — un seul passage de parsing/
    calcul pour toute la page plutôt que de le refaire à chaque section."""
    lignes = []
    for c in contrats:
        lead = c.get("leads") or {}
        montant_eur = (c.get("montant_centimes") or 0) / 100
        lignes.append({
            **c,
            "_client": lead.get("company") or "—",
            "_email": lead.get("email") or "",
            "_paid_at": _parse_dt(c.get("paid_at")),
            "_signed_at": _parse_dt(c.get("signed_at")),
            "_created_at": _parse_dt(c.get("created_at")),
            "_montant_eur": montant_eur,
            # payment_status='paye' sans montant connu ne devrait jamais
            # arriver en pratique (montant fixé avant facturation, voir
            # contrats_signature.creer_et_envoyer_lien_paiement) mais un
            # montant NULL ne doit jamais être compté comme payé ici.
            "_est_paye": c.get("payment_status") == "paye" and c.get("montant_centimes") is not None,
            "_est_signe": c.get("yousign_status") == "signe",
        })
    return lignes


def calculer_kpis(lignes: list[dict], maintenant: datetime | None = None) -> dict:
    maintenant = maintenant or datetime.now(timezone.utc)
    payes = [l for l in lignes if l["_est_paye"] and l["_paid_at"]]

    def ca_depuis(delta: timedelta | None) -> float:
        if delta is None:
            return sum(l["_montant_eur"] for l in payes)
        seuil = maintenant - delta
        return sum(l["_montant_eur"] for l in payes if l["_paid_at"] >= seuil)

    mrr = sum(
        l["_montant_eur"] for l in lignes
        if l["_est_paye"] and l.get("type_offre") == TYPE_OFFRE_ABONNEMENT
    )

    return {
        "ca_24h": ca_depuis(timedelta(hours=24)),
        "ca_7j": ca_depuis(timedelta(days=7)),
        "ca_30j": ca_depuis(timedelta(days=30)),
        "ca_total": ca_depuis(None),
        "mrr": mrr,
        "arr_estime": mrr * 12,
        "nb_signes": sum(1 for l in lignes if l["_est_signe"]),
        "nb_payes": len(payes),
        "nb_contrats": len(lignes),
    }


def repartition_type_offre(lignes: list[dict]) -> dict:
    payes = [l for l in lignes if l["_est_paye"]]
    unite = [l for l in payes if l.get("type_offre") == TYPE_OFFRE_UNITE]
    abonnement = [l for l in payes if l.get("type_offre") == TYPE_OFFRE_ABONNEMENT]
    return {
        "unite": {"nb": len(unite), "ca": sum(l["_montant_eur"] for l in unite)},
        "abonnement": {"nb": len(abonnement), "ca": sum(l["_montant_eur"] for l in abonnement)},
    }


def repartition_palier_unite(lignes: list[dict]) -> dict:
    """Uniquement les contrats payés « à l'unité » — le palier n'est
    reconnu que si le montant correspond EXACTEMENT à un des 3 prix de
    PRIX_PAR_PALIER_EUR (une remise/erreur de saisie manuelle tomberait
    dans "Autre / prix personnalisé" plutôt que d'être mal classée)."""
    compteur: dict[str, dict] = {}
    for l in lignes:
        if not (l["_est_paye"] and l.get("type_offre") == TYPE_OFFRE_UNITE):
            continue
        palier = _PALIER_PAR_MONTANT_EUR.get(round(l["_montant_eur"], 2))
        libelle = LIBELLE_PALIER.get(palier, "Autre / prix personnalisé")
        entree = compteur.setdefault(libelle, {"nb": 0, "ca": 0.0})
        entree["nb"] += 1
        entree["ca"] += l["_montant_eur"]
    return compteur


def repartition_formule_abonnement(lignes: list[dict]) -> dict:
    compteur: dict[str, dict] = {}
    for l in lignes:
        if not (l["_est_paye"] and l.get("type_offre") == TYPE_OFFRE_ABONNEMENT):
            continue
        libelle = _LABEL_FORMULE_ABONNEMENT.get(l.get("formule_abonnement"), "Formule inconnue")
        entree = compteur.setdefault(libelle, {"nb": 0, "ca": 0.0})
        entree["nb"] += 1
        entree["ca"] += l["_montant_eur"]
    return compteur


def serie_ca_cumule(lignes: list[dict], jours: int | None, maintenant: datetime | None = None) -> list[tuple[str, float]]:
    """CA cumulé jour par jour, sur `jours` derniers jours (None = tout
    l'historique). Renvoie une liste vide (jamais d'exception) s'il n'y a
    aucun paiement confirmé sur la période — à l'appelant d'afficher un
    état vide propre plutôt qu'un graphique cassé."""
    maintenant = maintenant or datetime.now(timezone.utc)
    payes = [l for l in lignes if l["_est_paye"] and l["_paid_at"]]
    if jours is not None:
        seuil = maintenant - timedelta(days=jours)
        payes = [l for l in payes if l["_paid_at"] >= seuil]
    if not payes:
        return []

    par_jour: dict[str, float] = {}
    for l in payes:
        cle = l["_paid_at"].date().isoformat()
        par_jour[cle] = par_jour.get(cle, 0.0) + l["_montant_eur"]

    cumule = 0.0
    serie = []
    for jour in sorted(par_jour):
        cumule += par_jour[jour]
        serie.append((jour, round(cumule, 2)))
    return serie


def table_transactions(lignes: list[dict]) -> list[dict]:
    """Une ligne par contrat, triée par date décroissante (date de paiement
    si connue, sinon signature, sinon création — toujours la donnée la plus
    récente/pertinente disponible pour ce contrat)."""
    def cle_tri(l: dict) -> datetime:
        return l["_paid_at"] or l["_signed_at"] or l["_created_at"] or datetime.min.replace(tzinfo=timezone.utc)

    resultat = []
    for l in sorted(lignes, key=cle_tri, reverse=True):
        if l["_est_paye"]:
            statut = "✅ Payé"
        elif l["_est_signe"]:
            statut = "✍️ Signé, en attente de paiement"
        else:
            statut = "⏳ En attente de signature"
        type_offre_libelle = "Abonnement" if l.get("type_offre") == TYPE_OFFRE_ABONNEMENT else "À l'unité"
        resultat.append({
            "Date": cle_tri(l),
            "Client": l["_client"],
            "Type": f"B2C · {type_offre_libelle}",
            "Montant": l["_montant_eur"],
            "Statut": statut,
        })
    return resultat
