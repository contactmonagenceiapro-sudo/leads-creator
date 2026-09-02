"""
Interface "Finances" — chiffre d'affaires, MRR et activité commerciale dans
le temps (24h / 7j / 30j / total).

PÉRIMÈTRE IMPORTANT (diagnostic fait avant de construire cette page, voir
data_access.get_contracts_finances) : la table `contracts` (tunnel intake
-> Yousign -> Stripe) couvre EXCLUSIVEMENT le B2C (vente de leads aux
artisans). Le B2B (campagnes clients type S.B.G Travaux, tables
`campagnes`/`leads_professionnels`) n'a AUCUNE facturation persistée en
base à ce jour — le bon de commande généré depuis "Administration &
Contrats" ne produit qu'un PDF, rien n'est écrit côté Supabase (voir sa
docstring : "Rien n'est enregistré côté serveur pour les bons de
commande"). Cette page l'affiche donc explicitement (encart ci-dessous)
plutôt que d'inventer ou d'omettre silencieusement un CA B2B.

Confirmation de signature 100% MANUELLE (pas de webhook Yousign, voir
data_access.marquer_contrat_signe). Le paiement, lui, est confirmé
automatiquement par le webhook Stripe (scripts/traiter_paiements_stripe.py) —
data_access.marquer_contrat_paye reste un filet de secours manuel. D'où la
distinction explicite "Contrats signés" vs "Paiements confirmés" en KPI,
jamais confondus l'un avec l'autre.

Toute la logique de calcul (KPIs, répartitions, série cumulée, table des
transactions) vit dans finances_calc.py, volontairement sans dépendance à
Streamlit ni à Supabase — ce fichier ne fait que l'assemblage/l'affichage.

Espace admin uniquement (même contrainte que les autres pages financières,
voir dashboard/app.py) : ces données ne doivent jamais être visibles depuis
le Portail Client.
"""

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import get_contracts_finances
from finances_calc import (
    calculer_kpis,
    enrichir,
    repartition_formule_abonnement,
    repartition_palier_unite,
    repartition_type_offre,
    serie_ca_cumule,
    table_transactions,
)

st.title("💰 Finances")
st.caption(
    "Chiffre d'affaires, MRR et activité commerciale — périmètre B2C uniquement "
    "(vente de leads aux artisans). Voir l'encart ci-dessous pour le B2B."
)

contrats_data, contrats_error = safe_call(get_contracts_finances)
if contrats_error:
    st.error(contrats_error)
    st.stop()

lignes = enrichir((contrats_data or {}).get("contracts", []))

st.info(
    "📌 **CA B2B non disponible ici** — la vente de leads aux entreprises (campagnes "
    "type S.B.G Travaux) n'a aucune facturation persistée en base à ce jour : le bon "
    "de commande généré depuis « Administration & Contrats » produit un PDF, rien "
    "n'est enregistré côté Supabase. Toutes les données de cette page concernent "
    "exclusivement le B2C (vente de leads aux artisans).",
    icon="ℹ️",
)

# ---------------------------------------------------------------------
# A. KPIs
# ---------------------------------------------------------------------
kpis = calculer_kpis(lignes)

st.subheader("Vue d'ensemble")
col1, col2, col3, col4 = st.columns(4)
col1.metric("CA 24h", f"{kpis['ca_24h']:.2f} €")
col2.metric("CA 7 jours", f"{kpis['ca_7j']:.2f} €")
col3.metric("CA 30 jours", f"{kpis['ca_30j']:.2f} €")
col4.metric("CA total", f"{kpis['ca_total']:.2f} €")

col5, col6, col7 = st.columns(3)
col5.metric("MRR (abonnements payés)", f"{kpis['mrr']:.2f} €")
col6.metric("Contrats signés", kpis["nb_signes"])
col7.metric("Paiements confirmés", kpis["nb_payes"])
st.caption(
    "« Contrats signés » (signature Yousign confirmée) et « Paiements confirmés » "
    "(paiement Stripe confirmé) sont deux statuts distincts, vérifiés manuellement "
    "par un admin — un contrat signé n'est pas toujours encore payé, voir Gestion & "
    "Réponse. MRR = somme des abonnements B2C au statut « payé » ; aucune date de "
    "résiliation n'étant trackée en base, ce montant suppose que tous les abonnements "
    "payés sont toujours actifs. CA affiché brut, avant éventuels remboursements."
)

st.divider()

# ---------------------------------------------------------------------
# B. Répartition
# ---------------------------------------------------------------------
st.subheader("Répartition du CA B2C")

rep = repartition_type_offre(lignes)
col_rep1, col_rep2 = st.columns(2)

with col_rep1:
    st.markdown("**À l'unité vs abonnement**")
    df_rep = pd.DataFrame([
        {"Offre": "À l'unité", "CA (€)": rep["unite"]["ca"], "Nb contrats": rep["unite"]["nb"]},
        {"Offre": "Abonnement", "CA (€)": rep["abonnement"]["ca"], "Nb contrats": rep["abonnement"]["nb"]},
    ])
    st.dataframe(df_rep, hide_index=True, use_container_width=True)
    if df_rep["CA (€)"].sum() > 0:
        st.bar_chart(df_rep.set_index("Offre")["CA (€)"])

with col_rep2:
    st.markdown("**Par palier (à l'unité)**")
    palier_data = repartition_palier_unite(lignes)
    if palier_data:
        df_palier = pd.DataFrame([
            {"Palier": k, "CA (€)": v["ca"], "Nb": v["nb"]} for k, v in palier_data.items()
        ])
        st.dataframe(df_palier, hide_index=True, use_container_width=True)
    else:
        st.caption("Aucune vente à l'unité payée pour le moment.")

    st.markdown("**Par formule (abonnement)**")
    formule_data = repartition_formule_abonnement(lignes)
    if formule_data:
        df_formule = pd.DataFrame([
            {"Formule": k, "CA (€)": v["ca"], "Nb": v["nb"]} for k, v in formule_data.items()
        ])
        st.dataframe(df_formule, hide_index=True, use_container_width=True)
    else:
        st.caption("Aucun abonnement payé pour le moment.")

st.divider()

# ---------------------------------------------------------------------
# C. Évolution du CA cumulé
# ---------------------------------------------------------------------
st.subheader("Évolution du CA cumulé")

periode_libelle = st.radio(
    "Période",
    options=["7 derniers jours", "30 derniers jours", "Tout l'historique"],
    horizontal=True,
    index=1,
)
jours_periode = {"7 derniers jours": 7, "30 derniers jours": 30, "Tout l'historique": None}[periode_libelle]

serie = serie_ca_cumule(lignes, jours_periode)
if serie:
    df_serie = pd.DataFrame(serie, columns=["Date", "CA cumulé (€)"]).set_index("Date")
    st.line_chart(df_serie)
else:
    st.info("Aucun paiement confirmé sur cette période — rien à afficher pour l'instant.")

st.divider()

# ---------------------------------------------------------------------
# D. Dernières transactions
# ---------------------------------------------------------------------
st.subheader("Dernières transactions")

transactions = table_transactions(lignes)
if not transactions:
    st.info("Aucune transaction enregistrée pour le moment.")
else:
    df_trans = pd.DataFrame(transactions)
    df_trans["Date"] = df_trans["Date"].apply(lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—")
    df_trans["Montant"] = df_trans["Montant"].apply(lambda m: f"{m:.2f} €")

    voir_tout = st.checkbox(f"Afficher toutes les transactions ({len(df_trans)})", value=False)
    st.dataframe(
        df_trans if voir_tout else df_trans.head(20),
        hide_index=True,
        use_container_width=True,
    )
    if not voir_tout and len(df_trans) > 20:
        st.caption(f"20 sur {len(df_trans)} transactions affichées — coche la case ci-dessus pour tout voir.")

st.divider()

# ---------------------------------------------------------------------
# E. Projection
# ---------------------------------------------------------------------
st.subheader("Projection")

if kpis["mrr"] > 0:
    st.metric("ARR estimé (MRR × 12)", f"{kpis['arr_estime']:.2f} €")
    st.caption(
        "⚠️ Projection théorique basée sur le MRR actuel, **pas une garantie de revenu "
        "futur** — suppose que tous les abonnements payés restent actifs 12 mois, sans "
        "tenir compte d'éventuelles résiliations (non trackées en base actuellement)."
    )
else:
    st.caption("Aucun abonnement payé actif pour le moment — pas d'ARR à projeter.")
