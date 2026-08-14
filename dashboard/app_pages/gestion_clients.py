"""
Interface "Gestion & Réponse aux clients" — suivi des leads, visualisation
des scores, et pilotage des campagnes d'e-mailing / relances, filtrable par
client/campagne (plateforme multi-clients / multi-secteurs).

Espace volontairement séparé de "Sourcing / Scraping" (app_pages/sourcing.py) :
ici on suit et on pilote, là-bas on configure et on lance la recherche.
"""

from datetime import date, datetime, timezone
from urllib.parse import quote

import pandas as pd
import streamlit as st

import process_runner
from common import afficher_suivi, executer_avec_spinner, liste_noms_campagnes, safe_call, to_dataframe
from contrats_signature import creer_et_envoyer_lien_paiement
from data_access import (
    compter_relances_envoyees_depuis,
    creer_remboursement,
    enrichir_lead_pro,
    executer_remboursement,
    get_campagne_stats,
    get_campagnes,
    get_contents,
    get_contracts,
    get_leads,
    get_leads_pro,
    get_remboursements,
    get_stats,
    marquer_contrat_paye,
    marquer_contrat_signe,
    signaler_lead_pro_invalide,
)


def construire_recap_leads_pro(leads_coches: pd.DataFrame, email_destinataire: str, campagne: str | None) -> str:
    """Récapitulatif texte propre d'une sélection de leads B2B, prêt à être
    copié/collé ou transmis par e-mail au client (nom, coordonnées, besoin)."""
    lignes = [
        "Sélection de leads qualifiés" + (f" — {campagne}" if campagne else ""),
        f"Destinataire : {email_destinataire}",
        f"Date : {date.today().strftime('%d/%m/%Y')}",
        f"Nombre de leads : {len(leads_coches)}",
        "",
    ]
    for _, lead in leads_coches.iterrows():
        lignes.append(f"— {lead.get('nom_entreprise') or 'Entreprise non renseignée'}")
        if lead.get("type_acteur"):
            lignes.append(f"    Type d'acteur : {lead['type_acteur']}")
        if lead.get("commune"):
            lignes.append(f"    Commune : {lead['commune']}")
        if lead.get("email"):
            lignes.append(f"    Email : {lead['email']}")
        if lead.get("telephone"):
            lignes.append(f"    Téléphone : {lead['telephone']}")
        if lead.get("site_web"):
            lignes.append(f"    Site web : {lead['site_web']}")
        if lead.get("notes"):
            lignes.append(f"    Besoin / notes : {lead['notes']}")
        lignes.append("")
    return "\n".join(lignes)


st.title("📇 Gestion & Réponse aux clients")
st.caption("Suivi des leads, visualisation des scores, pilotage des campagnes et relances.")


# ---------------------------------------------------------------------
# 1. Vue d'ensemble
# ---------------------------------------------------------------------

st.subheader("Vue d'ensemble")

stats, stats_error = safe_call(get_stats)
if stats_error:
    st.error(stats_error)
elif stats:
    col1, col2 = st.columns(2)
    col1.metric("Leads générés", stats.get("leads_total", "—"))

    last_report = stats.get("last_ceo_report")
    with col2:
        st.markdown("**Dernier rapport du CEO**")
        if isinstance(last_report, dict):
            st.caption(last_report.get("date", ""))
            st.write(last_report.get("summary", "—"))
        elif last_report:
            st.write(last_report)
        else:
            st.write("Aucun rapport disponible")
else:
    st.info("Aucune donnée de statistiques disponible.")

st.divider()


# ---------------------------------------------------------------------
# 2. Sélecteur de campagne — pilote tout ce qui suit (leads B2B, KPIs, envoi)
# ---------------------------------------------------------------------

campagnes_data, campagnes_error = safe_call(get_campagnes)
if campagnes_error:
    st.error(campagnes_error)
    st.stop()
liste_campagnes = (campagnes_data or {}).get("campagnes", []) if campagnes_data else []
noms_campagnes = liste_noms_campagnes(campagnes_data)

campagne_selectionnee = None
if noms_campagnes:
    campagne_selectionnee = st.selectbox(
        "📌 Campagne / client à suivre",
        options=noms_campagnes,
        key="select_campagne_gestion",
    )
else:
    st.info(
        "Aucune campagne configurée — crée-en une depuis l'interface « Sourcing / Scraping » "
        "pour suivre des leads B2B ici."
    )

st.divider()


# ---------------------------------------------------------------------
# 3. Performance de la campagne sélectionnée
# ---------------------------------------------------------------------

if campagne_selectionnee:
    st.subheader(f"📊 Performance — {campagne_selectionnee}")

    campagne_stats, campagne_stats_error = safe_call(get_campagne_stats, campagne_selectionnee)
    if campagne_stats_error:
        st.error(campagne_stats_error)
    elif campagne_stats:
        # 2 rangées plutôt que 5 colonnes d'un coup : sur mobile, Streamlit
        # empile les colonnes verticalement en dessous d'un certain seuil de
        # largeur — 5 blocs à la suite allongent inutilement la page avant
        # d'atteindre le contenu utile ; 3+2 reste lisible sur desktop tout
        # en réduisant cet effet sur petit écran.
        col_k1, col_k2, col_k3 = st.columns(3)
        col_k1.metric("Leads générés", campagne_stats.get("leads_total", 0))
        col_k2.metric("Contactés", campagne_stats.get("contactes", 0))
        col_k3.metric("Taux de contact", f"{campagne_stats.get('taux_contact', 0) * 100:.0f} %")
        col_k4, col_k5 = st.columns(2)
        col_k4.metric("Opportunités / chantiers ouverts", campagne_stats.get("opportunites", 0))
        col_k5.metric("🌟 Ultra-qualifiés", campagne_stats.get("leads_ultra_qualifies", 0))

    st.divider()


# ---------------------------------------------------------------------
# 4. Leads (artisans / B2B) et contenus
# ---------------------------------------------------------------------

titre_onglet_pro = f"🏗️ Leads B2B ({campagne_selectionnee})" if campagne_selectionnee else "🏗️ Leads B2B"
tab_leads, tab_pro, tab_contents = st.tabs(["🧑‍💼 Leads artisans", titre_onglet_pro, "📝 Contenus"])

with tab_leads:
    leads, leads_error = safe_call(get_leads)
    if leads_error:
        st.error(leads_error)
    else:
        df_leads = to_dataframe(leads)
        if df_leads.empty:
            st.info("Aucun lead pour le moment.")
        else:
            st.dataframe(df_leads, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_leads)} lead(s)")

with tab_pro:
    if not campagne_selectionnee:
        st.info("Sélectionne une campagne ci-dessus pour voir ses leads B2B.")
    else:
        leads_pro, leads_pro_error = safe_call(get_leads_pro, campagne_selectionnee)
        if leads_pro_error:
            st.error(leads_pro_error)
        else:
            df_pro = to_dataframe(leads_pro)
            if df_pro.empty:
                st.info(
                    "Aucun acteur professionnel trouvé pour le moment — lance une recherche "
                    "depuis l'interface « Sourcing / Scraping »."
                )
            else:
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    types_dispo = sorted(df_pro["type_acteur"].dropna().unique()) if "type_acteur" in df_pro else []
                    types_filtre = st.multiselect("Filtrer par type d'acteur", options=types_dispo, default=types_dispo)
                with col_f2:
                    statuts_dispo = sorted(df_pro["statut"].dropna().unique()) if "statut" in df_pro else []
                    statuts_filtre = st.multiselect("Filtrer par statut", options=statuts_dispo, default=statuts_dispo)

                df_filtre = df_pro.copy()
                if "type_acteur" in df_filtre.columns and types_filtre:
                    df_filtre = df_filtre[df_filtre["type_acteur"].isin(types_filtre)]
                if "statut" in df_filtre.columns and statuts_filtre:
                    df_filtre = df_filtre[df_filtre["statut"].isin(statuts_filtre)]

                colonnes_utiles = [c for c in [
                    "nom_entreprise", "type_acteur", "commune", "score_final",
                    "statut", "email", "telephone", "site_web", "linkedin_url",
                    "enrichissement_statut",
                ] if c in df_filtre.columns]

                df_affiche = df_filtre[colonnes_utiles]
                if "score_final" in colonnes_utiles:
                    df_affiche = df_affiche.sort_values("score_final", ascending=False)
                st.dataframe(df_affiche, use_container_width=True, hide_index=True)
                st.caption(f"{len(df_filtre)} / {len(df_pro)} acteur(s) affiché(s)")

                st.markdown("###### 🔄 Ré-enrichir un lead (relance la recherche de contact)")
                st.caption("⏱️ Jusqu'à ~30-40 secondes (requêtes réelles vers le site du lead).")
                options_reenrichir = {
                    f"{row['nom_entreprise']} — statut actuel : {row.get('enrichissement_statut', '?')}": row["id"]
                    for _, row in df_pro.iterrows()
                }
                col_re1, col_re2 = st.columns([3, 1])
                with col_re1:
                    choix_reenrichir = st.selectbox(
                        "Lead à ré-enrichir", options=list(options_reenrichir.keys()),
                        key="select_reenrichir", label_visibility="collapsed",
                    )
                with col_re2:
                    reenrichir_declenche = st.button("🔄 Ré-enrichir", use_container_width=True)
                forcer_reecriture = st.checkbox(
                    "Forcer la mise à jour même si déjà rempli",
                    value=False, key="checkbox_forcer_reecriture",
                    help=(
                        "Par défaut, un champ déjà rempli (site web, e-mail, téléphone, LinkedIn...) "
                        "n'est jamais écrasé, même si le nouveau résultat est différent — protège une "
                        "donnée déjà vérifiée. À cocher uniquement si tu sais que la valeur actuelle "
                        "est fausse et veux la remplacer par le nouveau résultat trouvé."
                    ),
                )
                if reenrichir_declenche:
                    resultat, err = executer_avec_spinner(
                        "Recherche de contact en cours (peut prendre jusqu'à 40s)...",
                        enrichir_lead_pro, options_reenrichir[choix_reenrichir], forcer_reecriture,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success(f"Statut : {resultat['resultat']['enrichissement_statut']}")
                        st.rerun()

                st.divider()
                st.markdown("###### 📤 Envoi rapide de leads sélectionnés")
                st.caption(
                    "Coche les leads à transmettre, renseigne l'e-mail du client destinataire "
                    "et génère un récapitulatif propre — prêt à copier/coller, télécharger ou "
                    "ouvrir directement dans ton client mail."
                )

                colonnes_recap = [c for c in [
                    "nom_entreprise", "type_acteur", "commune", "email", "telephone",
                    "site_web", "statut", "score_final", "notes",
                ] if c in df_filtre.columns]
                df_selection = df_filtre[colonnes_recap].reset_index(drop=True).copy()
                df_selection.insert(0, "Sélectionner", False)

                df_edite = st.data_editor(
                    df_selection,
                    use_container_width=True,
                    hide_index=True,
                    disabled=colonnes_recap,
                    column_config={"Sélectionner": st.column_config.CheckboxColumn(required=True)},
                    key="editeur_selection_envoi_leads",
                )
                leads_coches = df_edite[df_edite["Sélectionner"]]

                col_email, col_bouton = st.columns([3, 1])
                with col_email:
                    email_destinataire = st.text_input(
                        "Email du client destinataire",
                        placeholder="ex. contact@sbg-travaux.fr",
                        key="email_destinataire_leads",
                    )
                with col_bouton:
                    st.write("")
                    envoyer_clic = st.button(
                        "📤 Envoyer les leads sélectionnés",
                        type="primary",
                        use_container_width=True,
                        disabled=leads_coches.empty,
                    )

                if envoyer_clic:
                    if not email_destinataire.strip() or "@" not in email_destinataire:
                        st.warning("Merci de renseigner une adresse e-mail valide pour le destinataire.")
                    else:
                        st.session_state["recap_leads_pro"] = construire_recap_leads_pro(
                            leads_coches, email_destinataire.strip(), campagne_selectionnee
                        )

                recap_leads = st.session_state.get("recap_leads_pro")
                if recap_leads:
                    st.success("Récapitulatif prêt — copie-le, télécharge-le, ou ouvre-le directement dans ton client mail.")
                    st.text_area("Récapitulatif à transmettre", value=recap_leads, height=280, key="apercu_recap_leads")

                    col_dl, col_mail = st.columns(2)
                    with col_dl:
                        st.download_button(
                            "⬇️ Télécharger le récapitulatif (.txt)",
                            data=recap_leads,
                            file_name=f"leads_selection_{date.today():%Y%m%d}.txt",
                            mime="text/plain",
                            use_container_width=True,
                        )
                    with col_mail:
                        if email_destinataire.strip():
                            sujet = quote(f"Sélection de leads qualifiés - {campagne_selectionnee or ''}".strip(" -"))
                            corps = quote(recap_leads)
                            st.link_button(
                                "✉️ Ouvrir dans mon client mail",
                                f"mailto:{email_destinataire.strip()}?subject={sujet}&body={corps}",
                                use_container_width=True,
                            )
                    if len(recap_leads) > 1500:
                        st.caption(
                            "⚠️ Sélection volumineuse : certains clients mail tronquent les liens "
                            "'mailto' trop longs — privilégie le téléchargement ou le copier/coller "
                            "du récapitulatif ci-dessus dans ce cas."
                        )

with tab_contents:
    contents, contents_error = safe_call(get_contents)
    if contents_error:
        st.error(contents_error)
    else:
        df_contents = to_dataframe(contents)
        if df_contents.empty:
            st.info("Aucun contenu pour le moment.")
        else:
            st.dataframe(df_contents, use_container_width=True, hide_index=True)
            st.caption(f"{len(df_contents)} contenu(s)")

st.divider()


# ---------------------------------------------------------------------
# 5. Campagnes d'e-mailing & relances
# ---------------------------------------------------------------------

st.subheader("📬 Campagnes & relances")

col_a, col_b, col_c, col_d = st.columns(4)
with col_a:
    mail_check_en_cours = process_runner.est_en_cours("mail_check")
    if st.button("📨 Vérifier les réponses (IMAP)", use_container_width=True, disabled=mail_check_en_cours):
        _, err = executer_avec_spinner("Vérification en cours...", process_runner.lancer_mail_check)
        if err:
            st.error(err)
        else:
            st.success("Vérification des réponses lancée.")
    elif mail_check_en_cours:
        st.caption("⏳ Déjà en cours...")
with col_b:
    ceo_report_en_cours = process_runner.est_en_cours("ceo_report")
    if st.button("🚀 Campagne + rapport — artisans", use_container_width=True, disabled=ceo_report_en_cours):
        _, err = executer_avec_spinner("Déclenchement en cours...", process_runner.lancer_ceo_report)
        if err:
            st.error(err)
        else:
            st.success("Campagne d'e-mails + rapport CEO lancés.")
    elif ceo_report_en_cours:
        st.caption("⏳ Déjà en cours...")
with col_c:
    relance_en_cours = process_runner.est_en_cours("relance")
    if st.button("🔁 Relancer les sans-réponse — artisans", use_container_width=True, disabled=relance_en_cours):
        # Horodatage capturé AVANT le lancement (pas après) : sert de borne
        # basse à compter_relances_envoyees_depuis() une fois le subprocess
        # terminé (voir afficher_suivi ci-dessous) — ne compte que les
        # relances de CE run, jamais celles d'un run précédent.
        st.session_state["_relance_artisans_debut_at"] = datetime.now(timezone.utc).isoformat()
        _, err = executer_avec_spinner("Déclenchement en cours...", process_runner.lancer_relance)
        if err:
            st.error(err)
        else:
            st.success("Relances (artisans) lancées.")
    elif relance_en_cours:
        st.caption("⏳ Déjà en cours...")
with col_d:
    libelle_bouton_b2b = (
        f"📧 Campagne + relances B2B — {campagne_selectionnee}" if campagne_selectionnee else "📧 Campagne + relances B2B"
    )
    envoi_pro_en_cours = campagne_selectionnee and process_runner.est_en_cours("envoi_pro", campagne_selectionnee)
    if st.button(libelle_bouton_b2b, use_container_width=True, disabled=not campagne_selectionnee or envoi_pro_en_cours):
        _, err = executer_avec_spinner(
            "Déclenchement de la campagne B2B...",
            process_runner.lancer_pipeline_b2b, "envoi_pro", campagne_selectionnee,
        )
        if err:
            st.error(err)
    elif envoi_pro_en_cours:
        st.caption("⏳ Déjà en cours...")

# En dehors du `if st.button(...)` (voir sourcing.py pour la même logique) :
# reste actif après un rerun déclenché par le bouton "Vérifier l'avancement"
# d'afficher_suivi, pas seulement au moment du clic initial.
if campagne_selectionnee:
    afficher_suivi(
        "envoi_pro",
        estimation_secondes=60,
        libelle=f"Campagne + relances B2B ({campagne_selectionnee})",
        campagne=campagne_selectionnee,
    )

afficher_suivi(
    "relance",
    estimation_secondes=90,
    libelle="Relance des sans-réponse (artisans)",
    resume_a_la_fin=lambda: (
        f"{compter_relances_envoyees_depuis(st.session_state['_relance_artisans_debut_at'])} e-mail(s) de relance envoyé(s)."
        if st.session_state.get("_relance_artisans_debut_at")
        else None
    ),
)

st.divider()


# ---------------------------------------------------------------------
# 6. Remboursements
# ---------------------------------------------------------------------
#
# Deux mécanismes bien distincts, jamais confondus dans l'interface :
# - Remboursements Stripe RÉELS, liés aux contrats artisans payés (de
#   l'argent bouge réellement) ;
# - Avoirs commerciaux pour les leads B2B signalés invalides (simple
#   écriture de crédit — aucune facturation B2B n'existe à ce jour, donc
#   aucun paiement réel n'est en jeu ici).

st.subheader("💳 Remboursements")

tab_remb_stripe, tab_remb_b2b, tab_remb_historique = st.tabs(
    ["💶 Remboursements Stripe (artisans)", "🏗️ Avoirs commerciaux (B2B)", "📜 Historique complet"]
)

with tab_remb_stripe:
    st.caption(
        "Remboursement réel via Stripe, lié à un contrat artisan payé. "
        "L'éligibilité est validée automatiquement à la création ; l'exécution "
        "(mouvement d'argent réel) reste une action volontaire, jamais automatique."
    )
    contrats_data, contrats_error = safe_call(get_contracts)
    if contrats_error:
        st.error(contrats_error)
    liste_contrats = (contrats_data or {}).get("contracts", []) if contrats_data else []

    if not liste_contrats:
        st.info("Aucun contrat artisan enregistré pour le moment.")
    else:
        options_contrats = {
            f"{c.get('leads', {}).get('company', '—') if c.get('leads') else '—'} "
            f"— {(c.get('montant_centimes') or 0) / 100:.2f} € — {c.get('payment_status', '?')} ({c['id'][:8]})": c["id"]
            for c in liste_contrats
        }
        with st.form("form_remboursement_stripe"):
            choix_contrat = st.selectbox("Contrat à rembourser", options=list(options_contrats.keys()))
            montant_euros = st.number_input(
                "Montant à rembourser (€, laisser à 0 pour rembourser l'intégralité du contrat)",
                min_value=0.0, step=10.0, value=0.0,
            )
            motif_remb = st.text_area("Motif du remboursement", height=80)
            demander = st.form_submit_button("📝 Créer la demande de remboursement", type="primary")

        if demander:
            if not motif_remb.strip():
                st.warning("Le motif est requis.")
            else:
                contract_id = options_contrats[choix_contrat]
                resultat, err = executer_avec_spinner(
                    "Création de la demande de remboursement...",
                    creer_remboursement,
                    {
                        "contract_id": contract_id,
                        "montant_centimes": int(montant_euros * 100) if montant_euros > 0 else None,
                        "motif": motif_remb.strip(),
                        "demande_par": "dashboard",
                    },
                )
                if err:
                    st.error(err)
                else:
                    st.success("Demande enregistrée — vérifie son statut ci-dessous (validé / rejeté automatiquement).")
                    st.rerun()

    st.divider()

    remb_stripe_data, remb_stripe_error = safe_call(get_remboursements, None, None)
    if remb_stripe_error:
        st.error(remb_stripe_error)
    else:
        liste_remb = (remb_stripe_data or {}).get("remboursements", []) if remb_stripe_data else []
        remb_stripe = [r for r in liste_remb if r.get("type_remboursement") == "stripe"]
        if not remb_stripe:
            st.info("Aucune demande de remboursement Stripe pour le moment.")
        else:
            for r in sorted(remb_stripe, key=lambda x: x.get("created_at", ""), reverse=True):
                with st.container(border=True):
                    col_info, col_action = st.columns([3, 1])
                    with col_info:
                        st.markdown(
                            f"**{(r.get('montant_centimes') or 0) / 100:.2f} €** — "
                            f"statut : `{r['statut']}` — {r.get('motif', '')}"
                        )
                        st.caption(f"Créé le {r.get('created_at', '')[:16].replace('T', ' ')}")
                    with col_action:
                        if r["statut"] == "valide":
                            if st.button("💸 Exécuter", key=f"executer_{r['id']}", type="primary"):
                                _, err = executer_avec_spinner(
                                    "Exécution du remboursement Stripe...", executer_remboursement, r["id"],
                                )
                                if err:
                                    st.error(err)
                                else:
                                    st.success("Remboursement Stripe exécuté avec succès.")
                                    st.rerun()
                        elif r["statut"] == "rembourse":
                            st.success("Remboursé ✅")
                        elif r["statut"] == "rejete":
                            st.error("Rejeté")
                        elif r["statut"] == "echoue":
                            st.error("Échoué")

with tab_remb_b2b:
    st.caption(
        "Signale un lead B2B invalide/non qualifié (garantie annoncée aux clients) : "
        "un avoir commercial est créé automatiquement, sans paiement réel en jeu "
        "(aucune facturation par lead n'existe à ce jour dans le système)."
    )
    if not campagne_selectionnee:
        st.info("Sélectionne une campagne plus haut pour signaler un de ses leads.")
    else:
        leads_pro_b2b, leads_pro_b2b_error = safe_call(get_leads_pro, campagne_selectionnee)
        if leads_pro_b2b_error:
            st.error(leads_pro_b2b_error)
        else:
            df_leads_b2b = to_dataframe(leads_pro_b2b)
            leads_signalables = (
                df_leads_b2b[~df_leads_b2b.get("signale_invalide", False).fillna(False)]
                if not df_leads_b2b.empty and "signale_invalide" in df_leads_b2b.columns
                else df_leads_b2b
            )
            if df_leads_b2b.empty:
                st.info("Aucun lead B2B pour cette campagne.")
            else:
                options_leads = {
                    f"{row['nom_entreprise']} ({row.get('commune', '?')})": row["id"]
                    for _, row in leads_signalables.iterrows()
                } if not leads_signalables.empty else {}

                if not options_leads:
                    st.info("Tous les leads de cette campagne sont déjà signalés.")
                else:
                    with st.form("form_signaler_invalide"):
                        choix_lead = st.selectbox("Lead à signaler invalide/non qualifié", options=list(options_leads.keys()))
                        motif_invalidite = st.text_area("Motif (contact invalide, hors zone, aucun besoin réel...)", height=80)
                        montant_avoir_euros = st.number_input("Montant de l'avoir (€)", min_value=0.0, step=10.0, value=0.0)
                        signaler = st.form_submit_button("🚩 Signaler et créer l'avoir", type="primary")

                    if signaler:
                        if not motif_invalidite.strip():
                            st.warning("Le motif est requis.")
                        else:
                            _, err = executer_avec_spinner(
                                "Signalement et création de l'avoir...",
                                signaler_lead_pro_invalide,
                                options_leads[choix_lead],
                                motif_invalidite.strip(),
                                True,  # est_admin : montant libre, validation immédiate
                                int(montant_avoir_euros * 100),
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Lead signalé — avoir commercial créé automatiquement.")
                                st.rerun()

    st.divider()

    remb_b2b_data, remb_b2b_error = safe_call(
        get_remboursements, None, campagne_selectionnee if campagne_selectionnee else None
    )
    if remb_b2b_error:
        st.error(remb_b2b_error)
    else:
        liste_remb_b2b = (remb_b2b_data or {}).get("remboursements", []) if remb_b2b_data else []
        avoirs = [r for r in liste_remb_b2b if r.get("type_remboursement") == "avoir_commercial"]
        if not avoirs:
            st.info("Aucun avoir commercial pour le moment.")
        else:
            df_avoirs = to_dataframe({"avoirs": avoirs})
            colonnes_avoirs = [c for c in ["client_final", "montant_centimes", "motif", "statut", "created_at"] if c in df_avoirs.columns]
            st.dataframe(df_avoirs[colonnes_avoirs], use_container_width=True, hide_index=True)

with tab_remb_historique:
    remb_tout_data, remb_tout_error = safe_call(get_remboursements)
    if remb_tout_error:
        st.error(remb_tout_error)
    else:
        df_remb_tout = to_dataframe(remb_tout_data)
        if df_remb_tout.empty:
            st.info("Aucun remboursement enregistré pour le moment.")
        else:
            colonnes_hist = [c for c in [
                "type_remboursement", "client_final", "montant_centimes", "motif",
                "statut", "demande_par", "created_at", "traite_at",
            ] if c in df_remb_tout.columns]
            st.dataframe(
                df_remb_tout[colonnes_hist].sort_values("created_at", ascending=False),
                use_container_width=True, hide_index=True,
            )
            st.caption(f"{len(df_remb_tout)} remboursement(s)/avoir(s) au total")

st.divider()


# ---------------------------------------------------------------------
# 7. Contrats — signature (interne, auto ; Yousign, manuelle) / paiement Stripe
# ---------------------------------------------------------------------
#
# Contrats signature_provider='interne' (défaut depuis le 10/08, voir
# signature_interne.py) : yousign_status passe automatiquement à 'signe' dès
# le clic du client sur la page publique — le bouton "Marquer signé"
# ci-dessous ne leur sert jamais (déjà signés à l'affichage). Contrats
# signature_provider='yousign' (option conservée, voir contrats_signature.py)
# : toujours confirmés manuellement, pas de webhook (supprimé avec le
# backend FastAPI) — l'admin vérifie dans Yousign, puis confirme ici.
# Paiement Stripe : toujours confirmé manuellement pour les deux, même
# principe (pas de webhook).

st.subheader("💼 Contrats — signature & paiement")
st.caption(
    "Signature électronique interne (par défaut) : le statut passe à « Signé » "
    "automatiquement dès que le client valide sur sa page. Signature Yousign "
    "(option) : vérifie toi-même dans Yousign, puis confirme avec le bouton. "
    "Paiement Stripe : toujours à confirmer manuellement ici."
)

contrats_liste_data, contrats_liste_error = safe_call(get_contracts)
if contrats_liste_error:
    st.error(contrats_liste_error)
else:
    liste_contrats_tous = (contrats_liste_data or {}).get("contracts", []) if contrats_liste_data else []
    if not liste_contrats_tous:
        st.info("Aucun contrat pour le moment.")
    else:
        for c in sorted(liste_contrats_tous, key=lambda x: x.get("created_at", ""), reverse=True):
            entreprise = (c.get("leads") or {}).get("company", "—")
            with st.container(border=True):
                col_info, col_signe, col_paye = st.columns([2, 1, 1])
                with col_info:
                    st.markdown(
                        f"**{entreprise}** — {(c.get('montant_centimes') or 0) / 100:.2f} € — "
                        f"signature : `{c.get('yousign_status', '?')}` "
                        f"({c.get('signature_provider') or 'interne'}) — "
                        f"paiement : `{c.get('payment_status') or 'en_attente'}`"
                    )
                    st.caption(f"Créé le {c.get('created_at', '')[:16].replace('T', ' ')}")
                with col_signe:
                    if c.get("yousign_status") != "signe":
                        if st.button("✅ Marquer signé", key=f"signe_{c['id']}", use_container_width=True):
                            _, err = executer_avec_spinner("Mise à jour...", marquer_contrat_signe, c["id"])
                            if err:
                                st.error(err)
                            else:
                                st.success("Contrat marqué signé.")
                                st.rerun()
                    else:
                        st.success("Signé ✅")
                with col_paye:
                    if c.get("payment_status") != "paye":
                        if c.get("yousign_status") == "signe":
                            with st.popover("💰 Marquer payé", use_container_width=True):
                                st.caption(
                                    "Renseigne le payment_intent_id affiché dans le dashboard "
                                    "Stripe (indispensable pour un remboursement futur)."
                                )
                                pi_id = st.text_input("Stripe payment_intent_id", key=f"pi_{c['id']}")
                                if st.button("Confirmer le paiement", key=f"confirmer_paye_{c['id']}"):
                                    _, err = executer_avec_spinner(
                                        "Mise à jour...", marquer_contrat_paye, c["id"], pi_id,
                                    )
                                    if err:
                                        st.error(err)
                                    else:
                                        st.success("Contrat marqué payé.")
                                        st.rerun()
                        else:
                            st.caption("Signez d'abord le contrat.")
                    else:
                        st.success("Payé ✅")

                if c.get("yousign_status") == "signe" and not c.get("stripe_payment_link_id"):
                    if st.button("🔗 Générer et envoyer le lien de paiement", key=f"lien_paiement_{c['id']}"):
                        resultat, err = executer_avec_spinner(
                            "Génération du lien de paiement Stripe...",
                            creer_et_envoyer_lien_paiement, c["lead_id"], c["id"],
                        )
                        if err or not resultat:
                            st.error(err or "Échec de la génération du lien de paiement (voir logs).")
                        else:
                            st.success("Lien de paiement généré et envoyé.")
                            st.rerun()
