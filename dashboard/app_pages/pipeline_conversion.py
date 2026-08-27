"""
Interface admin "Pipeline de conversion" — module 2 de pilotage. Répartition
des leads par statut actuel (B2C et B2B) + taux de contact/intérêt/signature
dérivés — voir data_access.get_pipeline_conversion pour le détail de la
limite importante : c'est une PHOTO de l'état courant, pas un entonnoir de
cohorte (leads.status/statut est écrasé à chaque changement d'étape, aucun
historique en base).

Aucune nouvelle table, pas d'alerte automatique (pas de seuil "anormalement
bas" fiable sur un aussi petit volume de données à ce stade).

Espace admin uniquement.
"""

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import get_pipeline_conversion

st.title("🔀 Pipeline de conversion")
st.caption(
    "Répartition par statut ACTUEL des leads — pas un entonnoir de cohorte : "
    "un lead au statut 'payé' aujourd'hui n'est plus compté comme 'intéressé' "
    "même s'il l'a été hier (aucun historique de statut en base)."
)

resultat, erreur = safe_call(get_pipeline_conversion)
if erreur:
    st.error(f"Impossible de charger le pipeline de conversion : {erreur}")
    st.stop()

st.subheader("🔧 B2C — Artisans")
b2c = resultat["b2c"]
if not b2c["total"]:
    st.info("Aucun lead B2C pour l'instant.")
else:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Taux de contact", f"{b2c['taux_contact'] * 100:.1f} %", help="Contactés / total")
    col2.metric("Taux d'intérêt", f"{b2c['taux_interet'] * 100:.1f} %", help="Intéressés / contactés")
    col3.metric("Taux de signature", f"{b2c['taux_signature'] * 100:.1f} %", help="Signés (ou au-delà) / intéressés")
    col4.metric("Taux de paiement final", f"{b2c['taux_paiement_final'] * 100:.1f} %", help="Payés / total")

    st.dataframe(
        pd.DataFrame(sorted(b2c["repartition"].items(), key=lambda x: -x[1]), columns=["Statut", "Leads"]),
        use_container_width=True, hide_index=True,
    )

st.divider()

st.subheader("🏢 B2B — Acteurs pro")
b2b = resultat["b2b"]
if not b2b["total"]:
    st.info("Aucun lead B2B pour l'instant.")
else:
    col1, col2 = st.columns(2)
    col1.metric("Taux de contact", f"{b2b['taux_contact'] * 100:.1f} %", help="Contactés / total")
    col2.metric("Taux d'intérêt", f"{b2b['taux_interet'] * 100:.1f} %", help="Intéressés / contactés")
    st.caption(
        "Pas de taux de signature côté B2B : aucune facturation B2B persistée en base "
        "à ce jour (voir module 1 — Finances)."
    )

    st.dataframe(
        pd.DataFrame(sorted(b2b["repartition"].items(), key=lambda x: -x[1]), columns=["Statut", "Leads"]),
        use_container_width=True, hide_index=True,
    )
