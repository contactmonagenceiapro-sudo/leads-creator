"""
Interface admin "Journal d'audit" — historique consultable/filtrable des
actions admin sensibles (voir sql/init_journal_audit_admin.sql,
data_access.journaliser_action_admin). Branché directement dans les
fonctions existantes qui font déjà ces actions (traiter_reclamation,
maj_statut_verification_pro, marquer_contrat_signe, marquer_contrat_paye,
executer_remboursement) — pas un système de log séparé.

Espace admin uniquement — lecture seule, cette page n'écrit jamais dans le
journal elle-même.
"""

import streamlit as st

from common import safe_call, to_dataframe
from data_access import get_journal_audit

LIBELLES_ACTIONS = {
    "traiter_reclamation": "Réclamation traitée",
    "maj_statut_verification_pro": "Statut vérification pro modifié",
    "marquer_contrat_signe": "Contrat marqué signé",
    "marquer_contrat_paye": "Contrat marqué payé",
    "executer_remboursement": "Remboursement Stripe exécuté",
}

st.title("🗂️ Journal d'audit")
st.caption(
    "Historique des actions admin sensibles — traçabilité pour comprendre qui a fait quoi, "
    "quand, sans avoir à recouper plusieurs pages."
)

col_filtre_action, col_filtre_email = st.columns(2)
with col_filtre_action:
    action_choisie = st.selectbox(
        "Filtrer par action", ["Toutes"] + list(LIBELLES_ACTIONS.keys()),
        format_func=lambda a: "Toutes les actions" if a == "Toutes" else LIBELLES_ACTIONS.get(a, a),
    )
with col_filtre_email:
    email_filtre = st.text_input("Filtrer par e-mail (exact)", placeholder="admin@exemple.fr")

resultat, erreur = safe_call(
    get_journal_audit,
    utilisateur_email=email_filtre.strip() or None,
    action=None if action_choisie == "Toutes" else action_choisie,
)
if erreur:
    st.error(f"Impossible de charger le journal : {erreur}")
    st.stop()

entrees = resultat["entrees"]
if not entrees:
    st.info("Aucune entrée pour ces filtres.")
    st.stop()

st.caption(f"{len(entrees)} entrée(s) (200 les plus récentes maximum, non filtrées par date).")

for e in entrees:
    libelle = LIBELLES_ACTIONS.get(e["action"], e["action"])
    with st.expander(f"{e['created_at']} — {libelle} — {e.get('utilisateur_email') or 'utilisateur inconnu'}"):
        st.write(f"**Cible :** {e['cible_type']} `{e.get('cible_id') or '—'}`")
        st.json(e.get("detail") or {})

with st.expander("Vue tableau (export/tri)"):
    df = to_dataframe(entrees)
    if not df.empty:
        st.dataframe(
            df[["created_at", "action", "utilisateur_email", "cible_type", "cible_id"]],
            use_container_width=True,
            hide_index=True,
        )
