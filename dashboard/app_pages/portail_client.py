"""
Portail Client — vue en lecture seule, strictement limitée aux campagnes de
l'utilisateur connecté (voir dashboard/auth.py::campagnes_autorisees()).

Seule action d'écriture autorisée : signaler un lead comme invalide. Le
montant de l'avoir n'est jamais saisi ici (data_access.signaler_lead_pro_invalide
l'ignore pour un compte client et crée la demande en 'en_attente' de revue
par l'agence — voir l'argument est_admin=False passé ci-dessous).

Un compte admin accède aussi à cette page (voir dashboard/app.py), en
aperçu/test : un sélecteur listant TOUTES les campagnes lui est proposé
ci-dessous à la place de campagnes_autorisees() — sans rien changer à ce
qu'un compte client peut lui-même voir.
"""

import streamlit as st

from auth import campagnes_autorisees, est_admin
from common import executer_avec_spinner, liste_noms_campagnes, safe_call, to_dataframe
from data_access import (
    get_campagne_stats,
    get_campagnes,
    get_leads_pro,
    get_remboursements,
    signaler_lead_pro_invalide,
)

st.title("📊 Mon espace client")

if est_admin():
    st.caption(
        "🛠️ Aperçu admin — choisis une campagne pour prévisualiser le portail tel qu'un "
        "client le voit. Un compte client, lui, ne voit toujours que ses propres campagnes."
    )
    campagnes_data, campagnes_error = safe_call(get_campagnes)
    if campagnes_error:
        st.error(campagnes_error)
        st.stop()
    mes_campagnes = liste_noms_campagnes(campagnes_data)
    if not mes_campagnes:
        st.info("Aucune campagne configurée pour le moment — crée-en une depuis « Sourcing / Scraping ».")
        st.stop()
else:
    mes_campagnes = campagnes_autorisees()
    if not mes_campagnes:
        st.warning("Aucune campagne n'est associée à votre compte. Contactez votre agence.")
        st.stop()

if len(mes_campagnes) > 1:
    campagne_selectionnee = st.selectbox("📌 Campagne", options=mes_campagnes, key="select_campagne_client")
else:
    campagne_selectionnee = mes_campagnes[0]
    st.caption(f"Campagne : **{campagne_selectionnee}**")

st.divider()


# ---------------------------------------------------------------------
# 1. KPIs
# ---------------------------------------------------------------------

st.subheader("Vos indicateurs")

stats, stats_error = safe_call(get_campagne_stats, campagne_selectionnee)
if stats_error:
    st.error(stats_error)
elif stats:
    # 2 rangées plutôt que 5 colonnes d'un coup : cette page est consultée
    # par des comptes clients, potentiellement sur mobile — Streamlit empile
    # les colonnes verticalement en dessous d'un certain seuil de largeur,
    # 5 blocs à la suite allongent inutilement la page.
    col1, col2, col3 = st.columns(3)
    col1.metric("Leads générés", stats.get("leads_total", 0))
    col2.metric("Contactés", stats.get("contactes", 0))
    col3.metric("Taux de contact", f"{stats.get('taux_contact', 0) * 100:.0f} %")
    col4, col5 = st.columns(2)
    col4.metric("Opportunités", stats.get("opportunites", 0))
    col5.metric("🌟 Ultra-qualifiés", stats.get("leads_ultra_qualifies", 0))

st.divider()


# ---------------------------------------------------------------------
# 2. Leads générés pour cette campagne + signalement d'un lead invalide
# ---------------------------------------------------------------------

st.subheader("🏗️ Vos leads")

leads_pro_data, leads_pro_error = safe_call(get_leads_pro, campagne_selectionnee)
liste_leads = (leads_pro_data or {}).get("leads_pro", []) if leads_pro_data else []

if leads_pro_error:
    st.error(leads_pro_error)
else:
    df_leads = to_dataframe({"leads_pro": liste_leads})
    if df_leads.empty:
        st.info("Aucun lead pour le moment.")
    else:
        colonnes_utiles = [c for c in [
            "nom_entreprise", "type_acteur", "commune", "score_final",
            "statut", "email", "telephone", "site_web", "linkedin_url", "signale_invalide",
        ] if c in df_leads.columns]
        st.dataframe(df_leads[colonnes_utiles], use_container_width=True, hide_index=True)
        st.caption(f"{len(df_leads)} lead(s)")

    st.markdown("#### 🚩 Signaler un lead invalide")
    leads_signalables = [l for l in liste_leads if not l.get("signale_invalide")]
    if not leads_signalables:
        st.caption("Aucun lead à signaler pour le moment.")
    else:
        options_leads = {
            f"{l['nom_entreprise']} ({l.get('commune', '?')})": l["id"] for l in leads_signalables
        }
        with st.form("form_signaler_client"):
            choix_lead = st.selectbox("Lead concerné", options=list(options_leads.keys()))
            motif = st.text_area(
                "Motif (contact invalide, hors zone, aucun besoin réel...)", height=80
            )
            envoyer = st.form_submit_button("Signaler comme invalide", type="primary")

        if envoyer:
            if not motif.strip():
                st.warning("Le motif est requis.")
            else:
                _, err = executer_avec_spinner(
                    "Envoi du signalement...", signaler_lead_pro_invalide,
                    options_leads[choix_lead], motif.strip(), False,  # est_admin=False : montant ignoré, revue humaine requise
                )
                if err:
                    st.error(err)
                else:
                    st.success("Lead signalé — votre demande sera examinée par l'agence.")
                    st.rerun()

st.divider()


# ---------------------------------------------------------------------
# 3. Avoirs / remboursements liés à cette campagne
# ---------------------------------------------------------------------

st.subheader("💳 Vos avoirs / remboursements")

remb_data, remb_error = safe_call(get_remboursements, None, campagne_selectionnee)
if remb_error:
    st.error(remb_error)
else:
    liste_remb = (remb_data or {}).get("remboursements", []) if remb_data else []
    df_remb = to_dataframe({"remboursements": liste_remb})
    if df_remb.empty:
        st.info("Aucun avoir/remboursement pour le moment.")
    else:
        colonnes_remb = [c for c in ["montant_centimes", "motif", "statut", "created_at"] if c in df_remb.columns]
        st.dataframe(
            df_remb[colonnes_remb].sort_values("created_at", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
