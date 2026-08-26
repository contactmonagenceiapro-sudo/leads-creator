"""
Interface admin "Échéances" — module 5 de pilotage. Échéances légales et
administratives (statut légal définitif, renouvellement nom de domaine,
validité assurance décennale — voir sql/init_echeances.sql pour pourquoi
aucune de ces trois n'a de date insérée automatiquement) + vérification
récurrente de l'usage Supabase (fusion du module 11, voir la migration).

Alerte hebdomadaire automatique sur les échéances à moins de 30 jours :
scripts/controle_echeances.py, .github/workflows/controle_echeances.yml.
Cette page affiche le même calcul (data_access.get_echeances_a_relancer),
en plus de la liste complète et du formulaire d'ajout/clôture.

Espace admin uniquement.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import SEUIL_ALERTE_ECHEANCE_JOURS, ajouter_echeance, get_echeances, terminer_echeance

st.title("📅 Échéances légales et administratives")

resultat, erreur = safe_call(get_echeances)
if erreur:
    st.error(f"Impossible de charger les échéances : {erreur}")
    st.stop()

echeances = resultat["echeances"]
a_traiter = [e for e in echeances if e["statut"] == "a_traiter"]
aujourdhui = date.today()
seuil = (aujourdhui + timedelta(days=SEUIL_ALERTE_ECHEANCE_JOURS)).isoformat()

urgentes = [e for e in a_traiter if e["date_echeance"] <= seuil]
if urgentes:
    st.warning(f"⚠️ {len(urgentes)} échéance(s) à moins de {SEUIL_ALERTE_ECHEANCE_JOURS} jours (alerte hebdomadaire automatique, voir controle_echeances.yml).")
else:
    st.success(f"Aucune échéance à moins de {SEUIL_ALERTE_ECHEANCE_JOURS} jours.")

st.divider()

st.subheader("À traiter")
if not a_traiter:
    st.info("Aucune échéance en attente.")
else:
    for e in a_traiter:
        depassee = e["date_echeance"] < aujourdhui.isoformat()
        proche = e["date_echeance"] <= seuil
        icone = "🔴" if depassee else ("🟠" if proche else "⚪")
        with st.expander(f"{icone} {e['type']} — {e['description']} — {e['date_echeance']}"):
            if e.get("notes"):
                st.write(e["notes"])
            if e.get("recurrence_jours"):
                st.caption(f"Récurrente : une nouvelle échéance sera recréée {e['recurrence_jours']} jours après celle-ci une fois clôturée.")
            if st.button("Marquer traité", key=f"traiter_{e['id']}"):
                _, err = safe_call(terminer_echeance, e["id"])
                if err:
                    st.error(err)
                else:
                    st.success("Échéance clôturée.")
                    st.rerun()

with st.expander("Historique (échéances traitées)"):
    traitees = [e for e in echeances if e["statut"] == "traite"]
    if traitees:
        df = pd.DataFrame(traitees)
        st.dataframe(df[["type", "description", "date_echeance", "date_traitement"]], use_container_width=True, hide_index=True)
    else:
        st.caption("Aucune échéance traitée pour l'instant.")

st.divider()

st.subheader("Ajouter une échéance")
with st.form("ajouter_echeance"):
    type_echeance = st.text_input("Type", placeholder="légal, infra, assurance, domaine...")
    description = st.text_area("Description")
    date_echeance_saisie = st.date_input("Date d'échéance", value=date.today() + timedelta(days=30))
    recurrente = st.checkbox("Récurrente (recrée automatiquement la suivante à la clôture)")
    recurrence_jours = st.number_input("Tous les combien de jours", min_value=1, value=30, step=1) if recurrente else None
    notes = st.text_area("Notes (optionnel)")
    if st.form_submit_button("Ajouter"):
        _, err = safe_call(
            ajouter_echeance, type_echeance, description, date_echeance_saisie.isoformat(),
            int(recurrence_jours) if recurrence_jours else None, notes,
        )
        if err:
            st.error(err)
        else:
            st.success("Échéance ajoutée.")
            st.rerun()
