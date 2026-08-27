"""
Interface "Demandes de devis" — suivi du mécanisme de livraison qui
rapproche les demandes publiques (formulaire générique /demande-devis, voir
dashboard/pages_publiques.py::afficher_demande_devis) avec les artisans
clients actifs (leads.status='paye'), voir livraison_devis.py pour la
logique complète (round-robin, quota abonnement, proposition/paiement à
l'unité, expiration à 48h). Conception validée le 18/08/2026.

Espace admin uniquement — même contrainte que sourcing.py/gestion_clients.py
(pilotage d'un mécanisme de fond, jamais une donnée propre à un client du
Portail Client).

Trois vues : en attente (pas encore de destinataire, ou aucun artisan
correspondant — utile pour prioriser le démarchage commercial sur un corps
de métier/zone en tension), propositions en attente de paiement (formule à
l'unité, avec confirmation manuelle du paiement — pas de webhook Stripe
dans ce projet, voir data_access.marquer_demande_devis_payee_et_livree),
livrées (historique, avec artisan destinataire et date).
"""

from datetime import datetime, timedelta, timezone

import streamlit as st

import process_runner
from common import afficher_suivi, safe_call
from data_access import get_demandes_devis, get_leads_par_ids, marquer_demande_devis_payee_et_livree
from livraison_devis import DELAI_EXPIRATION_PROPOSITION_HEURES

st.title("📮 Demandes de devis")
st.caption("Rapprochement des demandes de devis publiques avec les artisans clients actifs.")

if st.button(
    "🔄 Lancer le rapprochement maintenant",
    disabled=process_runner.est_en_cours("livraison_devis"),
    help="Expire d'abord les propositions à l'unité de plus de 48h, puis traite les nouvelles demandes.",
):
    try:
        process_runner.lancer_livraison_devis()
    except RuntimeError as e:
        st.warning(str(e))

afficher_suivi("livraison_devis", estimation_secondes=15, libelle="Rapprochement des demandes de devis")

st.divider()


def _noms_artisans(demandes: list[dict]) -> dict:
    ids = tuple(sorted({d["lead_id_livraison"] for d in demandes if d.get("lead_id_livraison")}))
    leads, erreur = safe_call(get_leads_par_ids, ids)
    if erreur or not leads:
        return {}
    return {lead["id"]: lead for lead in leads["leads"]}


onglet_attente, onglet_propositions, onglet_livrees = st.tabs([
    "⏳ En attente", "💳 Propositions en attente de paiement", "✅ Livrées",
])

def _libelle_statut_attente(d: dict) -> str:
    """Le rapprochement round-robin exige statut_confirmation='confirme'
    (voir livraison_devis.py::traiter_demandes_en_attente et
    sql/init_demandes_devis_particuliers_confirmation.sql) : une demande pas
    encore confirmée ou dont la confirmation a expiré reste 'a_qualifier'
    indéfiniment, sans jamais être vue par le round-robin — sans ce libellé,
    elle serait indiscernable ici d'une demande confirmée mais simplement
    pas encore traitée."""
    confirmation = d.get("statut_confirmation")
    if confirmation == "en_attente_confirmation":
        return "En attente de confirmation e-mail du client"
    if confirmation == "expire":
        return "Confirmation e-mail expirée (jamais confirmée)"
    return "Pas encore traitée" if d["statut"] == "a_qualifier" else "Aucun artisan correspondant"


with onglet_attente:
    st.caption(
        "Demandes sans destinataire pour l'instant — en attente de confirmation e-mail du "
        "client, jamais confirmées (expirées), pas encore traitées, ou sans aucun artisan "
        "client actif correspondant (corps de métier + zone). Une forte concentration sur un "
        "corps de métier/une zone confirmée est un signal direct pour prioriser le démarchage "
        "commercial de nouveaux artisans clients dans ce créneau."
    )
    a_qualifier, erreur_aq = safe_call(get_demandes_devis, "a_qualifier")
    en_attente_artisan, erreur_ea = safe_call(get_demandes_devis, "en_attente_artisan")
    if erreur_aq or erreur_ea:
        st.error(erreur_aq or erreur_ea)
    else:
        lignes = (a_qualifier or {}).get("demandes", []) + (en_attente_artisan or {}).get("demandes", [])
        if not lignes:
            st.info("Aucune demande en attente.")
        else:
            st.dataframe(
                [
                    {
                        "Statut": _libelle_statut_attente(d),
                        "Corps de métier": d.get("corps_metier") or "—",
                        "Commune": d.get("commune") or "—",
                        "Description": (d.get("message") or "")[:80],
                        "Reçue le": d.get("created_at", "")[:16].replace("T", " "),
                    }
                    for d in sorted(lignes, key=lambda d: d.get("created_at") or "", reverse=True)
                ],
                use_container_width=True,
                hide_index=True,
            )

with onglet_propositions:
    st.caption(
        f"Formule à l'unité : proposée à un artisan candidat, en attente de son paiement — "
        f"expire après {DELAI_EXPIRATION_PROPOSITION_HEURES}h sans paiement (retombe alors sur le "
        f"candidat round-robin suivant, voir livraison_devis.py::expirer_propositions_perimees)."
    )
    proposees, erreur = safe_call(get_demandes_devis, "proposee")
    if erreur:
        st.error(erreur)
    else:
        lignes = (proposees or {}).get("demandes", [])
        if not lignes:
            st.info("Aucune proposition en attente de paiement.")
        else:
            artisans = _noms_artisans(lignes)
            for d in sorted(lignes, key=lambda d: d.get("proposee_le") or "", reverse=True):
                artisan = artisans.get(d.get("lead_id_livraison"), {})
                proposee_le = d.get("proposee_le")
                expire_le = None
                if proposee_le:
                    try:
                        expire_le = (
                            datetime.fromisoformat(proposee_le.replace("Z", "+00:00"))
                            + timedelta(hours=DELAI_EXPIRATION_PROPOSITION_HEURES)
                        )
                    except ValueError:
                        expire_le = None

                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(
                            f"**{artisan.get('company', 'Artisan inconnu')}** — "
                            f"{d.get('corps_metier') or '—'} — {d.get('commune') or '—'}"
                        )
                        st.caption(
                            f"Proposée le {(proposee_le or '')[:16].replace('T', ' ')}"
                            + (f" — expire le {expire_le.strftime('%d/%m %H:%M UTC')}" if expire_le else "")
                        )
                        if d.get("stripe_payment_url"):
                            st.caption(f"Lien de paiement : {d['stripe_payment_url']}")
                    with col2:
                        montant = (d.get("montant_centimes") or 0) / 100
                        st.metric("Montant", f"{montant:.2f} €")

                    with st.form(f"form_paiement_{d['id']}"):
                        payment_intent_id = st.text_input(
                            "Payment Intent ID Stripe (optionnel)", key=f"pi_{d['id']}",
                            help="Visible dans le dashboard Stripe une fois le paiement passé — facultatif, gardé pour traçabilité.",
                        )
                        confirmer = st.form_submit_button("✅ Confirmer le paiement et livrer", type="primary")
                    if confirmer:
                        _, erreur_confirmation = safe_call(
                            marquer_demande_devis_payee_et_livree, d["id"], payment_intent_id
                        )
                        if erreur_confirmation:
                            st.error(erreur_confirmation)
                        else:
                            st.success("Paiement confirmé — coordonnées envoyées à l'artisan.")
                            st.rerun()

with onglet_livrees:
    livrees, erreur = safe_call(get_demandes_devis, "livree")
    if erreur:
        st.error(erreur)
    else:
        lignes = (livrees or {}).get("demandes", [])
        if not lignes:
            st.info("Aucune demande livrée pour l'instant.")
        else:
            artisans = _noms_artisans(lignes)
            st.dataframe(
                [
                    {
                        "Artisan": artisans.get(d.get("lead_id_livraison"), {}).get("company", "—"),
                        "Corps de métier": d.get("corps_metier") or "—",
                        "Commune": d.get("commune") or "—",
                        "Livrée le": (d.get("livree_le") or "")[:16].replace("T", " "),
                        "Montant": f"{(d.get('montant_centimes') or 0) / 100:.2f} €" if d.get("montant_centimes") else "Inclus abonnement",
                    }
                    for d in sorted(lignes, key=lambda d: d.get("livree_le") or "", reverse=True)
                ],
                use_container_width=True,
                hide_index=True,
            )
