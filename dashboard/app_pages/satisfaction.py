"""
Interface admin "Satisfaction" — module 4 de pilotage. Enquêtes envoyées
automatiquement 1 semaine après le premier paiement d'un artisan (voir
scripts/envoyer_enquetes_satisfaction.py, sql/init_satisfaction_enquetes.sql).

Réponse saisie manuellement ici par un admin après lecture de l'e-mail de
retour (pas de formulaire public, pas de webhook — doctrine détaillée dans
la migration) : cette page sert à la fois de file d'attente ("en attente de
saisie") et d'historique consultable.

Espace admin uniquement.
"""

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import enregistrer_reponse_satisfaction, get_satisfaction_enquetes

st.title("⭐ Satisfaction")
st.caption(
    "Enquêtes envoyées automatiquement 1 semaine après le premier paiement — réponse reçue "
    "par e-mail, à saisir manuellement ci-dessous après lecture."
)

resultat, erreur = safe_call(get_satisfaction_enquetes)
if erreur:
    st.error(f"Impossible de charger les enquêtes de satisfaction : {erreur}")
    st.stop()

if resultat["note_moyenne"] is not None:
    col1, col2 = st.columns(2)
    col1.metric("Note moyenne", f"{resultat['note_moyenne']} / 10", help=f"Sur {resultat['nombre_notes']} réponse(s)")
    col2.metric("Enquêtes en attente de réponse", len(resultat["en_attente"]))
else:
    st.info("Aucune réponse enregistrée pour l'instant.")

st.divider()

st.subheader("En attente de réponse")
en_attente = resultat["en_attente"]
if not en_attente:
    st.caption("Aucune enquête en attente de saisie.")
else:
    for e in en_attente:
        lead = e.get("leads") or {}
        nom = lead.get("company") or lead.get("nom_entreprise") or lead.get("email") or "—"
        with st.expander(f"{nom} — envoyée le {(e['envoyee_le'] or '')[:10]}"):
            with st.form(f"reponse_{e['id']}"):
                note = st.slider("Note (0 à 10)", 0, 10, 8)
                commentaire = st.text_area("Commentaire (optionnel)")
                if st.form_submit_button("Enregistrer la réponse"):
                    _, err = safe_call(enregistrer_reponse_satisfaction, e["id"], note, commentaire)
                    if err:
                        st.error(err)
                    else:
                        st.success("Réponse enregistrée.")
                        st.rerun()

st.divider()

with st.expander("Historique des réponses"):
    repondues = resultat["repondues"]
    if not repondues:
        st.caption("Aucune réponse pour l'instant.")
    else:
        df = pd.DataFrame([
            {
                "Artisan": (e.get("leads") or {}).get("company") or (e.get("leads") or {}).get("nom_entreprise") or "—",
                "Note": e.get("note"),
                "Commentaire": e.get("commentaire") or "",
                "Répondu le": (e.get("repondu_le") or "")[:10],
            }
            for e in repondues
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
