"""
Portail Client — vue en lecture seule, strictement limitée aux campagnes B2B
(dashboard/auth.py::campagnes_autorisees()) et/ou aux leads B2C
(dashboard/auth.py::mes_leads_autorises(), voir sql/init_utilisateur_leads.sql)
de l'utilisateur connecté. Un même compte peut être lié à l'un, l'autre, ou
les deux — chaque section ci-dessous ne s'affiche que si le compte a quelque
chose à y voir.

Actions d'écriture autorisées :
- signaler un lead B2B comme invalide (mécanisme existant, motif libre,
  inchangé — data_access.signaler_lead_pro_invalide) ;
- déposer une réclamation officielle Article 4 des CGV (B2C ou B2B, motif
  contrôlé, délai de 7 jours vérifié côté serveur — data_access.creer_reclamation,
  voir sql/init_reclamations.sql). Les deux mécanismes coexistent
  volontairement (voir data_access.py) : le second ne remplace pas le
  premier.
Dans les deux cas, le montant de l'avoir/la décision finale n'est jamais
saisi ici par un compte client — toujours en 'en_attente' de revue par
l'agence (voir est_admin=False passé ci-dessous).

Un compte admin accède aussi à cette page (voir dashboard/app.py), en
aperçu/test : des sélecteurs listant TOUTES les campagnes / TOUS les
artisans payants lui sont proposés ci-dessous à la place de
campagnes_autorisees()/mes_leads_autorises() — sans rien changer à ce qu'un
compte client peut lui-même voir.
"""

from datetime import datetime, timezone

import streamlit as st

from auth import campagnes_autorisees, est_admin, mes_leads_autorises
from common import executer_avec_spinner, liste_noms_campagnes, safe_call, to_dataframe
from data_access import (
    DELAI_RECLAMATION_JOURS,
    LIBELLES_MOTIFS_RECLAMATION,
    MOTIFS_RECLAMATION,
    creer_reclamation,
    get_campagne_stats,
    get_campagnes,
    get_demandes_devis_livrees_pour_lead,
    get_leads_par_ids,
    get_leads_payants,
    get_leads_pro,
    get_reclamations,
    get_remboursements,
    signaler_lead_pro_invalide,
)


def _formulaire_reclamation(
    type_lead: str, options_leads: dict[str, str], key_suffix: str,
    client_lead_id: str | None = None, client_final: str | None = None,
    afficher_selecteur: bool = True,
) -> None:
    """Formulaire de réclamation UNIQUE (Article 4 des CGV), réutilisé pour
    le B2C et le B2B — seul type_lead (et l'identité client associée)
    change. `afficher_selecteur=False` masque le choix du lead quand le
    formulaire est déjà affiché au sein d'une carte dédiée à UN lead précis
    (voir section B2C ci-dessous) : `options_leads` ne contient alors qu'une
    seule entrée, redondante avec le contexte déjà visible à l'écran."""
    if not options_leads:
        st.caption("Aucun lead à réclamer pour le moment.")
        return

    with st.form(f"form_reclamation_{key_suffix}"):
        if afficher_selecteur:
            choix = st.selectbox("Lead concerné", options=list(options_leads.keys()), key=f"sel_reclam_{key_suffix}")
        else:
            choix = next(iter(options_leads))
        motif = st.selectbox(
            "Motif (Article 4 des CGV)", options=MOTIFS_RECLAMATION,
            format_func=lambda m: LIBELLES_MOTIFS_RECLAMATION[m], key=f"motif_reclam_{key_suffix}",
        )
        description = st.text_area("Précisions (optionnel)", height=80, key=f"desc_reclam_{key_suffix}")
        envoyer = st.form_submit_button("Envoyer la réclamation", type="primary")

    if not envoyer:
        return
    resultat, err = executer_avec_spinner(
        "Envoi de la réclamation...", creer_reclamation,
        type_lead, options_leads[choix], motif, description.strip() or None,
        False,  # est_admin=False : toujours revue humaine côté client
        client_lead_id, client_final,
    )
    if err:
        st.error(err)
    elif resultat and resultat.get("dans_les_delais"):
        st.success("Réclamation enregistrée — elle sera examinée par l'agence.")
        st.rerun()
    else:
        st.warning(
            f"Réclamation enregistrée, mais au-delà du délai contractuel de {DELAI_RECLAMATION_JOURS} jours "
            "— elle sera quand même examinée par l'agence, qui tranchera au cas par cas."
        )
        st.rerun()


st.title("📊 Mon espace client")

if est_admin():
    st.caption(
        "🛠️ Aperçu admin — choisis une campagne et/ou un artisan pour prévisualiser le portail "
        "tel qu'un client le voit. Un compte client, lui, ne voit toujours que ses propres "
        "campagnes/leads."
    )
    campagnes_data, campagnes_error = safe_call(get_campagnes)
    if campagnes_error:
        st.error(campagnes_error)
        st.stop()
    mes_campagnes = liste_noms_campagnes(campagnes_data)

    leads_payants_data, leads_payants_error = safe_call(get_leads_payants)
    if leads_payants_error:
        st.error(leads_payants_error)
        st.stop()
    leads_disponibles = (leads_payants_data or {}).get("leads", [])
else:
    mes_campagnes = campagnes_autorisees()
    mes_leads_ids = mes_leads_autorises()
    leads_disponibles = []
    if mes_leads_ids:
        leads_data, leads_error = safe_call(get_leads_par_ids, tuple(sorted(mes_leads_ids)))
        if leads_error:
            st.error(leads_error)
            st.stop()
        leads_disponibles = (leads_data or {}).get("leads", [])

if not mes_campagnes and not leads_disponibles:
    st.warning("Aucune campagne ni aucun artisan n'est associé à votre compte. Contactez votre agence.")
    st.stop()

campagne_selectionnee = None
if mes_campagnes:
    if len(mes_campagnes) > 1:
        campagne_selectionnee = st.selectbox("📌 Campagne", options=mes_campagnes, key="select_campagne_client")
    else:
        campagne_selectionnee = mes_campagnes[0]
        st.caption(f"Campagne : **{campagne_selectionnee}**")

lead_selectionne = None
if leads_disponibles:
    if len(leads_disponibles) > 1:
        options_artisan = {(l.get("company") or l.get("email") or l["id"]): l for l in leads_disponibles}
        choix_artisan = st.selectbox("🧑‍🔧 Artisan", options=list(options_artisan.keys()), key="select_artisan_client")
        lead_selectionne = options_artisan[choix_artisan]
    else:
        lead_selectionne = leads_disponibles[0]
        st.caption(f"Artisan : **{lead_selectionne.get('company') or lead_selectionne.get('email')}**")

st.divider()


# ---------------------------------------------------------------------
# 1. KPIs (B2B — campagne sélectionnée)
# ---------------------------------------------------------------------

if campagne_selectionnee:
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
# 2. Leads B2B générés pour cette campagne + signalement + réclamation
# ---------------------------------------------------------------------

liste_leads: list[dict] = []
if campagne_selectionnee:
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
            options_leads_signalement = {
                f"{l['nom_entreprise']} ({l.get('commune', '?')})": l["id"] for l in leads_signalables
            }
            with st.form("form_signaler_client"):
                choix_lead = st.selectbox("Lead concerné", options=list(options_leads_signalement.keys()))
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
                        options_leads_signalement[choix_lead], motif.strip(), False,  # est_admin=False : montant ignoré, revue humaine requise
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Lead signalé — votre demande sera examinée par l'agence.")
                        st.rerun()

        st.markdown("#### 📋 Déposer une réclamation officielle (Article 4 des CGV)")
        st.caption(
            f"Motif contrôlé, délai de {DELAI_RECLAMATION_JOURS} jours vérifié automatiquement — "
            "mécanisme distinct du signalement ci-dessus, suivi par l'agence dans son interface "
            "dédiée aux réclamations."
        )
        options_leads_reclamation = {
            f"{l['nom_entreprise']} ({l.get('commune', '?')})": l["id"] for l in liste_leads
        }
        _formulaire_reclamation(
            type_lead="b2b", options_leads=options_leads_reclamation, key_suffix="b2b",
            client_final=campagne_selectionnee,
        )

    st.divider()


# ---------------------------------------------------------------------
# 3. Avoirs / remboursements liés à cette campagne (B2B)
# ---------------------------------------------------------------------

if campagne_selectionnee:
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

    st.divider()


# ---------------------------------------------------------------------
# 4. Vos demandes de devis (B2C) — artisan sélectionné
# ---------------------------------------------------------------------

if lead_selectionne:
    st.subheader("🧾 Vos demandes de devis")

    demandes_data, demandes_error = safe_call(get_demandes_devis_livrees_pour_lead, lead_selectionne["id"])
    if demandes_error:
        st.error(demandes_error)
    else:
        demandes = (demandes_data or {}).get("demandes", [])
        if not demandes:
            st.info("Aucune demande de devis livrée pour le moment.")
        else:
            for demande in demandes:
                livree_le = demande.get("livree_le")
                dans_delais, jours_restants = False, None
                if livree_le:
                    try:
                        date_livraison = datetime.fromisoformat(livree_le.replace("Z", "+00:00"))
                        jours_restants = DELAI_RECLAMATION_JOURS - (datetime.now(timezone.utc) - date_livraison).days
                        dans_delais = jours_restants >= 0
                    except ValueError:
                        pass

                with st.container(border=True):
                    st.markdown(f"**{demande.get('corps_metier') or '—'}** — {demande.get('commune') or '—'}")
                    st.caption(f"Livrée le {(livree_le or '')[:16].replace('T', ' ')}")
                    if dans_delais:
                        st.caption(
                            f"🟢 Réclamation possible — {jours_restants} jour(s) restant(s) "
                            f"sur {DELAI_RECLAMATION_JOURS}."
                        )
                    else:
                        st.caption(
                            f"🟠 Délai de réclamation de {DELAI_RECLAMATION_JOURS} jours dépassé — "
                            "toujours possible, mais signalé comme tel à l'agence."
                        )

                    with st.expander("🚩 Signaler un problème"):
                        _formulaire_reclamation(
                            type_lead="b2c", options_leads={"Cette demande": demande["id"]},
                            key_suffix=f"b2c_{demande['id']}", client_lead_id=lead_selectionne["id"],
                            afficher_selecteur=False,
                        )

    st.divider()


# ---------------------------------------------------------------------
# 5. Historique de vos réclamations (B2C + B2B, tout ce qui est en scope)
# ---------------------------------------------------------------------

st.subheader("📋 Historique de vos réclamations")

reclamations_visibles: list[dict] = []
if campagne_selectionnee:
    data_b2b, err_b2b = safe_call(get_reclamations, None, None, campagne_selectionnee)
    if err_b2b:
        st.error(err_b2b)
    else:
        reclamations_visibles += (data_b2b or {}).get("reclamations", [])
if lead_selectionne:
    data_b2c, err_b2c = safe_call(get_reclamations, None, lead_selectionne["id"], None)
    if err_b2c:
        st.error(err_b2c)
    else:
        reclamations_visibles += (data_b2c or {}).get("reclamations", [])

if not reclamations_visibles:
    st.info("Aucune réclamation déposée pour le moment.")
else:
    df_reclam = to_dataframe({"reclamations": reclamations_visibles})
    colonnes_reclam = [c for c in [
        "motif", "description_libre", "dans_les_delais", "statut",
        "date_reclamation", "date_traitement", "commentaire_traitement",
    ] if c in df_reclam.columns]
    st.dataframe(
        df_reclam[colonnes_reclam].sort_values("date_reclamation", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
