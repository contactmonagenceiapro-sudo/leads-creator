"""
Interface admin "Réclamations" — traitement des réclamations Article 4 des
CGV (construire_articles_cgv_b2c et son équivalent B2B), B2C et B2B
confondues (voir sql/init_reclamations.sql, dashboard/data_access.py).

Espace admin uniquement — le dépôt d'une réclamation se fait côté client
depuis app_pages/portail_client.py, jamais ici (cette page ne fait que
consulter/trancher).

Coexiste avec l'onglet "🏗️ Avoirs commerciaux (B2B)" de gestion_clients.py
(mécanisme historique, motif libre, non remplacé) — cette page est le point
d'entrée du NOUVEAU mécanisme formel (motif contrôlé, délai vérifié,
décision tracée). Accepter/Refuser ici ne déclenche AUCUN remboursement ni
avoir automatique : uniquement un changement de statut tracé (voir demande
d'origine) — le lien vers une compensation effective reste une étape
ultérieure distincte.
"""

import streamlit as st

from common import safe_call
from data_access import (
    LIBELLES_MOTIFS_RECLAMATION,
    SEUIL_ALERTE_TAUX_RECLAMATION,
    calculer_taux_reclamation,
    get_clients_taux_reclamation_eleve,
    get_demandes_devis_par_ids,
    get_leads_par_ids,
    get_leads_pro_par_ids,
    get_reclamations,
    traiter_reclamation,
)

st.title("🚨 Réclamations")
st.caption(
    "Réclamations Article 4 des CGV (e-mail invalide, téléphone erroné, zone non conforme, "
    "type non conforme, doublon) — B2C et B2B. L'absence de réponse du prospect n'est jamais "
    "un motif recevable (exclu des CGV, absent des motifs sélectionnables)."
)


# ---------------------------------------------------------------------
# 1. Vue d'ensemble — compteurs + alerte taux de réclamation élevé
# ---------------------------------------------------------------------

en_attente_data, err_attente = safe_call(get_reclamations, "en_attente")
if err_attente:
    st.error(err_attente)
    st.stop()
liste_en_attente = (en_attente_data or {}).get("reclamations", [])

toutes_data, err_toutes = safe_call(get_reclamations)
liste_toutes = (toutes_data or {}).get("reclamations", []) if not err_toutes else []

col1, col2, col3 = st.columns(3)
col1.metric("⏳ En attente", len(liste_en_attente))
col2.metric("✅ Acceptées", len([r for r in liste_toutes if r.get("statut") == "acceptee"]))
col3.metric("❌ Refusées", len([r for r in liste_toutes if r.get("statut") == "refusee"]))

alertes_data, err_alertes = safe_call(get_clients_taux_reclamation_eleve)
if err_alertes:
    st.error(err_alertes)
else:
    alertes = (alertes_data or {}).get("alertes", [])
    if alertes:
        st.error(f"🔴 {len(alertes)} client(s) au-dessus de {SEUIL_ALERTE_TAUX_RECLAMATION * 100:.0f} % "
                 "de taux de réclamation sur 90 jours — à revoir manuellement :")
        for alerte in sorted(alertes, key=lambda a: a["taux"], reverse=True):
            identifiant = alerte.get("client_final") or alerte.get("client_lead_id")
            st.markdown(
                f"- **{identifiant}** ({alerte['type_lead'].upper()}) — "
                f"{alerte['taux'] * 100:.0f} % ({alerte['reclamations']}/{alerte['livres']} leads livrés)"
            )

st.divider()


def _libelles_leads(reclamations: list[dict]) -> dict[str, str]:
    """Résout id -> libellé lisible pour chaque lead concerné, séparément
    B2C (demandes_devis_particuliers) / B2B (leads_professionnels) — un
    seul aller-retour Supabase par table plutôt qu'un par réclamation."""
    ids_b2c = tuple(sorted({r["lead_id"] for r in reclamations if r.get("type_lead") == "b2c"}))
    ids_b2b = tuple(sorted({r["lead_id"] for r in reclamations if r.get("type_lead") == "b2b"}))
    ids_artisans = tuple(sorted({r["client_lead_id"] for r in reclamations if r.get("client_lead_id")}))

    demandes, err1 = safe_call(get_demandes_devis_par_ids, ids_b2c)
    leads_pro, err2 = safe_call(get_leads_pro_par_ids, ids_b2b)
    artisans, err3 = safe_call(get_leads_par_ids, ids_artisans)

    libelles: dict[str, str] = {}
    for d in (demandes or {}).get("demandes", []) if not err1 else []:
        libelles[d["id"]] = f"{d.get('corps_metier') or '—'} — {d.get('commune') or '—'} ({d.get('nom') or 'particulier'})"
    for lp in (leads_pro or {}).get("leads_pro", []) if not err2 else []:
        libelles[lp["id"]] = f"{lp.get('nom_entreprise') or '—'} ({lp.get('type_acteur') or '—'})"

    noms_artisans = {a["id"]: (a.get("company") or a.get("email") or a["id"]) for a in (artisans or {}).get("leads", [])} if not err3 else {}
    return libelles, noms_artisans


def _carte_reclamation(reclamation: dict, libelles: dict[str, str], noms_artisans: dict[str, str], montrer_actions: bool) -> None:
    lead_libelle = libelles.get(reclamation["lead_id"], "Lead introuvable")
    client_libelle = (
        noms_artisans.get(reclamation.get("client_lead_id"), reclamation.get("client_lead_id"))
        if reclamation.get("type_lead") == "b2c" else reclamation.get("client_final")
    )

    taux_info, err_taux = safe_call(
        calculer_taux_reclamation,
        reclamation.get("client_lead_id"), reclamation.get("client_final"),
    )

    with st.container(border=True):
        col_gauche, col_droite = st.columns([3, 1])
        with col_gauche:
            st.markdown(f"**{lead_libelle}**")
            st.caption(f"Client : {client_libelle} — {reclamation.get('type_lead', '').upper()}")
            st.caption(f"Motif : **{LIBELLES_MOTIFS_RECLAMATION.get(reclamation['motif'], reclamation['motif'])}**")
            if reclamation.get("description_libre"):
                st.write(reclamation["description_libre"])
            st.caption(
                ("🟢 Dans les délais" if reclamation.get("dans_les_delais") else "🟠 Délai de 7 jours dépassé")
                + f" — reçue le {(reclamation.get('date_reclamation') or '')[:16].replace('T', ' ')}"
            )
        with col_droite:
            if not err_taux and taux_info:
                st.metric(
                    "Taux réclam. (90j)", f"{taux_info['taux'] * 100:.0f} %",
                    help=f"{taux_info['reclamations']} réclamation(s) / {taux_info['livres']} lead(s) livré(s)",
                )
                if taux_info["alerte"]:
                    st.error("🔴 Client à revoir")

        if reclamation.get("statut") != "en_attente":
            st.caption(
                f"Décision : **{reclamation['statut']}** par {reclamation.get('traite_par') or '—'} "
                f"le {(reclamation.get('date_traitement') or '')[:16].replace('T', ' ')}"
            )
            if reclamation.get("commentaire_traitement"):
                st.caption(f"Motif de la décision : {reclamation['commentaire_traitement']}")

        if montrer_actions:
            traite_par = st.session_state.get("auth_email", "admin")
            col_accepter, col_refuser = st.columns(2)
            with col_accepter:
                if st.button(
                    "✅ Accepter", key=f"accepter_{reclamation['id']}", use_container_width=True, type="primary"
                ):
                    _, err = safe_call(traiter_reclamation, reclamation["id"], "acceptee", traite_par)
                    if err:
                        st.error(err)
                    else:
                        st.success("Réclamation acceptée.")
                        st.rerun()
            with col_refuser:
                with st.popover("❌ Refuser", use_container_width=True):
                    commentaire = st.text_area("Motif du refus (obligatoire)", key=f"commentaire_{reclamation['id']}")
                    if st.button("Confirmer le refus", key=f"confirmer_refus_{reclamation['id']}"):
                        if not commentaire.strip():
                            st.warning("Le motif de refus est obligatoire.")
                        else:
                            _, err = safe_call(
                                traiter_reclamation, reclamation["id"], "refusee", traite_par, commentaire.strip()
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Réclamation refusée.")
                                st.rerun()


onglet_attente, onglet_historique = st.tabs(["⏳ En attente", "📜 Historique"])

with onglet_attente:
    if not liste_en_attente:
        st.info("Aucune réclamation en attente.")
    else:
        tri = st.selectbox(
            "Trier par", ["Date (récent d'abord)", "Motif", "Client"], key="tri_reclamations_attente",
        )
        if tri == "Motif":
            lignes = sorted(liste_en_attente, key=lambda r: r.get("motif") or "")
        elif tri == "Client":
            lignes = sorted(liste_en_attente, key=lambda r: r.get("client_final") or r.get("client_lead_id") or "")
        else:
            lignes = sorted(liste_en_attente, key=lambda r: r.get("date_reclamation") or "", reverse=True)

        libelles, noms_artisans = _libelles_leads(lignes)
        for reclamation in lignes:
            _carte_reclamation(reclamation, libelles, noms_artisans, montrer_actions=True)

with onglet_historique:
    filtre_statut = st.selectbox(
        "Filtrer par statut", ["Tous", "Acceptées", "Refusées"], key="filtre_historique_reclamations",
    )
    lignes_historique = [r for r in liste_toutes if r.get("statut") != "en_attente"]
    if filtre_statut == "Acceptées":
        lignes_historique = [r for r in lignes_historique if r.get("statut") == "acceptee"]
    elif filtre_statut == "Refusées":
        lignes_historique = [r for r in lignes_historique if r.get("statut") == "refusee"]
    lignes_historique = sorted(lignes_historique, key=lambda r: r.get("date_traitement") or "", reverse=True)

    if not lignes_historique:
        st.info("Aucune réclamation traitée pour le moment.")
    else:
        libelles, noms_artisans = _libelles_leads(lignes_historique)
        for reclamation in lignes_historique:
            _carte_reclamation(reclamation, libelles, noms_artisans, montrer_actions=False)
