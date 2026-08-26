"""
Interface admin "Coûts d'infrastructure" — module 3 de pilotage. Coûts
remplis manuellement (pas d'API de facturation branchée dans un premier
temps, voir sql/init_couts_infrastructure.sql) : Supabase, Streamlit Cloud,
Zoho, futur nom de domaine, frais Stripe (en pourcentage du CA réel, pas un
montant fixe).

Vue centrale : coût mensuel total vs CA du mois (data_access.calculer_ca_du_mois,
réutilisée telle quelle par le futur module 1 finances) — seuil de
rentabilité visible en un coup d'œil. CA actuellement à 0€ (aucun contrat
payé/demande livrée à l'unité en base à ce jour) : la page reste utile dès
maintenant pour suivre les coûts fixes, le CA suivra une fois les premières
ventes réelles enregistrées.

Espace admin uniquement.
"""

from datetime import date, datetime, timezone

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import (
    ajouter_cout_infrastructure,
    calculer_ca_du_mois,
    get_couts_infrastructure,
    terminer_cout_infrastructure,
)

st.title("💰 Coûts d'infrastructure")
st.caption("Coûts fixes + frais Stripe (% du CA) vs chiffre d'affaires réel du mois — seuil de rentabilité.")

resultat, erreur = safe_call(get_couts_infrastructure)
if erreur:
    st.error(f"Impossible de charger les coûts : {erreur}")
    st.stop()
couts = resultat["couts"]
couts_actifs = [c for c in couts if not c.get("date_fin")]

aujourdhui = datetime.now(timezone.utc)
ca_resultat, erreur_ca = safe_call(calculer_ca_du_mois, aujourdhui.year, aujourdhui.month)
ca_du_mois_centimes = ca_resultat["ca_total_centimes"] if ca_resultat else 0

couts_fixes_centimes = sum(c["cout_mensuel_centimes"] for c in couts_actifs if c.get("cout_mensuel_centimes") is not None)
couts_pourcentage = [c for c in couts_actifs if c.get("pourcentage_du_ca") is not None]
couts_variables_centimes = sum(round(ca_du_mois_centimes * (c["pourcentage_du_ca"] / 100)) for c in couts_pourcentage)
couts_totaux_centimes = couts_fixes_centimes + couts_variables_centimes
marge_centimes = ca_du_mois_centimes - couts_totaux_centimes

col1, col2, col3 = st.columns(3)
col1.metric("CA du mois", f"{ca_du_mois_centimes / 100:.2f} €")
col2.metric("Coûts du mois", f"{couts_totaux_centimes / 100:.2f} €", help=f"Fixe : {couts_fixes_centimes/100:.2f} € + variable (Stripe...) : {couts_variables_centimes/100:.2f} €")
col3.metric(
    "Marge du mois", f"{marge_centimes / 100:.2f} €",
    delta=None if ca_du_mois_centimes == 0 else f"{(marge_centimes / ca_du_mois_centimes * 100):.0f}%",
)

if ca_du_mois_centimes == 0:
    st.info(
        "Aucun CA encaissé ce mois-ci pour l'instant (aucun contrat payé, aucune demande "
        "à l'unité livrée) — la marge ci-dessus n'est donc pas représentative tant qu'il "
        "n'y a pas de vente réelle."
    )
elif marge_centimes < 0:
    st.error("⚠️ Coûts supérieurs au CA ce mois-ci — sous le seuil de rentabilité.")
else:
    st.success("✅ Au-dessus du seuil de rentabilité ce mois-ci.")

st.divider()

st.subheader("Coûts actifs")
if not couts_actifs:
    st.info("Aucun coût enregistré pour l'instant — ajoutez-en un ci-dessous.")
else:
    lignes = []
    for c in couts_actifs:
        if c.get("cout_mensuel_centimes") is not None:
            montant = f"{c['cout_mensuel_centimes'] / 100:.2f} €/mois"
        else:
            montant = f"{c['pourcentage_du_ca']:.2f} % du CA"
        lignes.append({
            "Service": c["service"], "Coût": montant, "Depuis": c["date_debut"],
            "Notes": c.get("notes") or "", "id": c["id"],
        })
    df = pd.DataFrame(lignes)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)

    with st.expander("Terminer un coût (service abandonné / changement de plan)"):
        service_a_terminer = st.selectbox(
            "Service", options=[c["id"] for c in couts_actifs],
            format_func=lambda cid: next(c["service"] for c in couts_actifs if c["id"] == cid),
        )
        date_fin_saisie = st.date_input("Date de fin", value=date.today())
        if st.button("Terminer ce coût"):
            _, err = safe_call(terminer_cout_infrastructure, service_a_terminer, date_fin_saisie.isoformat())
            if err:
                st.error(err)
            else:
                st.success("Coût clôturé.")
                st.rerun()

st.divider()

st.subheader("Ajouter un coût")
with st.form("ajouter_cout_infrastructure"):
    service = st.text_input("Service", placeholder="Supabase, Streamlit Cloud, Zoho Mail, nom de domaine...")
    type_cout = st.radio("Type", ["Montant fixe mensuel", "Pourcentage du CA (ex. frais Stripe)"], horizontal=True)
    if type_cout == "Montant fixe mensuel":
        montant_eur = st.number_input("Coût mensuel (€)", min_value=0.0, step=1.0, format="%.2f")
        pourcentage = None
    else:
        montant_eur = None
        pourcentage = st.number_input("Pourcentage du CA (%)", min_value=0.0, max_value=100.0, step=0.1, format="%.2f")
    date_debut_saisie = st.date_input("Actif depuis le", value=date.today())
    notes = st.text_area("Notes (optionnel)")
    if st.form_submit_button("Ajouter"):
        _, err = safe_call(
            ajouter_cout_infrastructure,
            service,
            round(montant_eur * 100) if montant_eur is not None else None,
            pourcentage,
            date_debut_saisie.isoformat(),
            notes,
        )
        if err:
            st.error(err)
        else:
            st.success(f"Coût « {service} » ajouté.")
            st.rerun()
