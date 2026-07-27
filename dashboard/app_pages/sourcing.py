"""
Interface "Sourcing / Scraping" — recherche de futurs clients ET
configuration des campagnes (plateforme multi-clients / multi-secteurs).

Espace volontairement séparé de "Gestion & Réponse" (app_pages/gestion_clients.py) :
ici on définit QUI on cible et on lance la recherche ; là-bas on pilote le
suivi et les campagnes d'envoi.
"""

import pandas as pd
import streamlit as st

from api_client import creer_ou_modifier_campagne, get_campagnes, trigger_agent_action
from common import afficher_suivi, demarrer_suivi, executer_avec_spinner, safe_call

st.title("🔍 Sourcing / Scraping")
st.caption("Recherche de futurs clients — configuration des campagnes, lancement et ciblage.")


# ---------------------------------------------------------------------
# 1. Artisans sans site web (Expertise Digitale)
# ---------------------------------------------------------------------

st.subheader("Artisans sans site web (Expertise Digitale)")
st.caption("Scraping SIRENE puis traitement IA (scoring + pitch) des nouveaux prospects.")

col1, col2 = st.columns(2)
with col1:
    st.caption("⏱️ Estimation : environ 1 à 2 minutes selon le nombre de nouvelles entreprises détectées.")
    if st.button("🕸️ Lancer le scraping", use_container_width=True):
        _, err = executer_avec_spinner("Déclenchement du scraping...", trigger_agent_action, "scraping")
        if err:
            st.error(err)
        else:
            demarrer_suivi("scraping")
with col2:
    st.caption("⏱️ Estimation : environ 1 à 3 minutes selon le nombre de leads à traiter (un appel IA par lead).")
    if st.button("🤖 Traitement IA des leads", use_container_width=True):
        _, err = executer_avec_spinner("Déclenchement du traitement IA...", trigger_agent_action, "leads")
        if err:
            st.error(err)
        else:
            demarrer_suivi("leads")

# En dehors des blocs `if st.button(...)` : ces deux appels doivent rester
# actifs après un rerun déclenché par autre chose que le clic initial (ex:
# le bouton "Vérifier l'avancement" d'afficher_suivi elle-même) — voir
# common.py::afficher_suivi. Ne s'affichent que si demarrer_suivi() a été
# appelé plus haut pour cette action (aucun effet sinon).
afficher_suivi("scraping", estimation_secondes=90, libelle="Scraping des artisans")
afficher_suivi("leads", estimation_secondes=120, libelle="Traitement IA des leads")

st.divider()


# ---------------------------------------------------------------------
# 2. Configuration des campagnes (plateforme multi-clients / multi-secteurs)
# ---------------------------------------------------------------------

st.subheader("⚙️ Configuration des campagnes")
st.caption(
    "Chaque campagne = un client + son secteur + sa description de services + "
    "sa zone géographique + les types d'acteurs professionnels à cibler. Le "
    "sourcing, l'enrichissement, le scoring et la personnalisation des e-mails "
    "lisent cette configuration dynamiquement — aucun secteur ni zone n'est "
    "plus codé en dur dans les scripts."
)

campagnes_data, campagnes_error = safe_call(get_campagnes)
if campagnes_error:
    st.error(campagnes_error)
liste_campagnes = (campagnes_data or {}).get("campagnes", []) if campagnes_data else []
noms_campagnes = [c["nom_client"] for c in liste_campagnes]

with st.expander("➕ Créer une nouvelle campagne / modifier une campagne existante", expanded=not liste_campagnes):
    campagne_a_editer = st.selectbox(
        "Modifier une campagne existante (laisser vide pour en créer une nouvelle)",
        options=[""] + noms_campagnes,
        format_func=lambda x: x or "— Nouvelle campagne —",
        key="select_campagne_a_editer",
    )
    donnees_existantes = next((c for c in liste_campagnes if c["nom_client"] == campagne_a_editer), {})

    with st.form("form_campagne"):
        nom_client = st.text_input("Nom du client", value=donnees_existantes.get("nom_client", ""))
        secteur = st.text_input("Secteur d'activité", value=donnees_existantes.get("secteur", ""))
        description_services = st.text_area(
            "Description des services proposés (sert à personnaliser le pitch généré par IA)",
            value=donnees_existantes.get("description_services", ""),
            height=100,
        )
        communes_texte = st.text_area(
            "Communes ciblées (une par ligne)",
            value="\n".join(donnees_existantes.get("communes_cibles", [])),
            height=120,
        )
        statuts_possibles = ["active", "en_pause", "archivee"]
        statut = st.selectbox(
            "Statut",
            options=statuts_possibles,
            index=statuts_possibles.index(donnees_existantes.get("statut", "active")),
        )

        st.markdown(
            "**Types d'acteurs ciblés** — clé technique, libellé affiché, codes NAF "
            "séparés par des virgules (format pointé, ex : `71.11Z`), poids de 0 à 1."
        )
        types_existants = donnees_existantes.get("types_acteur_cibles", [])
        lignes_defaut = [
            {
                "cle": t.get("cle", ""),
                "libelle": t.get("libelle", ""),
                "codes_naf": ", ".join(t.get("codes_naf", [])),
                "poids": t.get("poids", 0.5),
            }
            for t in types_existants
        ] or [{"cle": "", "libelle": "", "codes_naf": "", "poids": 0.5}]

        df_types = st.data_editor(
            pd.DataFrame(lignes_defaut),
            num_rows="dynamic",
            use_container_width=True,
            key="editeur_types_acteur",
            column_config={
                "poids": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.1),
            },
        )

        enregistrer = st.form_submit_button(
            "💾 Enregistrer la campagne", type="primary", use_container_width=True
        )

    if enregistrer:
        if not nom_client.strip():
            st.warning("Le nom du client est requis.")
        else:
            types_acteur_cibles = [
                {
                    "cle": str(row["cle"] or "").strip(),
                    "libelle": str(row["libelle"] or "").strip() or str(row["cle"] or "").strip(),
                    "codes_naf": [c.strip() for c in str(row["codes_naf"] or "").split(",") if c.strip()],
                    "poids": float(row["poids"]) if row["poids"] not in (None, "") else 0.5,
                }
                for _, row in df_types.iterrows()
                if str(row["cle"] or "").strip()
            ]
            communes_cibles = [c.strip() for c in communes_texte.splitlines() if c.strip()]

            _, err = executer_avec_spinner(
                "Enregistrement de la campagne...",
                creer_ou_modifier_campagne,
                {
                    "nom_client": nom_client.strip(),
                    "secteur": secteur.strip(),
                    "description_services": description_services.strip(),
                    "communes_cibles": communes_cibles,
                    "types_acteur_cibles": types_acteur_cibles,
                    "statut": statut,
                },
            )
            if err:
                st.error(err)
            else:
                st.success(f"Campagne « {nom_client} » enregistrée.")
                st.rerun()

if liste_campagnes:
    st.dataframe(
        pd.DataFrame(liste_campagnes)[
            ["nom_client", "secteur", "statut", "communes_cibles", "types_acteur_cibles"]
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Aucune campagne configurée pour le moment — crée-en une ci-dessus.")

st.divider()


# ---------------------------------------------------------------------
# 3. Lancement du sourcing B2B pour une campagne sélectionnée
# ---------------------------------------------------------------------

st.subheader("🏗️ Lancer le sourcing B2B pour une campagne")

if not noms_campagnes:
    st.info("Crée d'abord une campagne ci-dessus pour pouvoir lancer un sourcing.")
else:
    campagne_active = st.selectbox("Campagne à sourcer", options=noms_campagnes, key="select_campagne_sourcing")
    campagne_courante = next((c for c in liste_campagnes if c["nom_client"] == campagne_active), {})

    communes_dispo = campagne_courante.get("communes_cibles") or []
    types_dispo = campagne_courante.get("types_acteur_cibles") or []
    types_dispo_cles = [t["cle"] for t in types_dispo]
    libelles_types = {t["cle"]: t.get("libelle", t["cle"]) for t in types_dispo}

    with st.form("filtres_sourcing_pro"):
        communes_choisies = st.multiselect(
            "Communes ciblées (pour ce run)", options=communes_dispo, default=communes_dispo
        )
        types_choisis = st.multiselect(
            "Types d'acteur ciblés (pour ce run)",
            options=types_dispo_cles,
            default=types_dispo_cles,
            format_func=lambda x: libelles_types.get(x, x),
        )
        st.caption(
            "⏱️ Estimation : environ 2 à 4 minutes selon le volume (sourcing SIRENE + "
            "enrichissement des contacts + scoring) — plus long si plusieurs sites "
            "d'entreprises ciblées répondent lentement ou sont injoignables."
        )
        lancer_pro = st.form_submit_button(
            "🔍 Lancer la recherche de leads B2B", type="primary", use_container_width=True
        )

    if lancer_pro:
        if not communes_choisies or not types_choisis:
            st.warning("Sélectionne au moins une commune et un type d'acteur avant de lancer la recherche.")
        else:
            options_ciblage = {"campagne": campagne_active}
            if set(communes_choisies) != set(communes_dispo):
                options_ciblage["communes"] = communes_choisies
            if set(types_choisis) != set(types_dispo_cles):
                options_ciblage["types_acteur"] = types_choisis

            _, err = executer_avec_spinner(
                "Déclenchement de la recherche de leads B2B...",
                trigger_agent_action, "pipeline_pro", options_ciblage,
            )
            if err:
                st.error(err)
            else:
                demarrer_suivi("pipeline_pro", campagne=campagne_active)
                st.info("Va sur l'interface « Gestion & Réponse » pour consulter les résultats en détail.")

    afficher_suivi(
        "pipeline_pro",
        estimation_secondes=220,
        libelle=f"Recherche de leads B2B ({campagne_active})",
        campagne=campagne_active,
    )

    st.divider()
    st.subheader("⚡ Pipeline Automatique (bout en bout, en un clic)")
    st.caption(
        "Enchaîne automatiquement : sourcing → enrichissement → scoring → "
        "enregistrement des leads qualifiés en base → campagne d'e-mailing "
        "personnalisée par IA (selon le secteur) → relances J+3/J+7. Tourne "
        "aussi automatiquement chaque jour pour toutes les campagnes actives "
        "(tâche planifiée côté serveur)."
    )
    if st.button("⚡ Lancer le Pipeline Automatique complet", type="primary", use_container_width=True):
        _, err = executer_avec_spinner(
            "Déclenchement du Pipeline Automatique...",
            trigger_agent_action, "pipeline_auto", {"campagne": campagne_active},
        )
        if err:
            st.error(err)
        else:
            demarrer_suivi("pipeline_auto", campagne=campagne_active)

    # Tâche la plus longue de l'app (jusqu'à 15 min, timeout_secondes=900) :
    # c'est justement celle où afficher_suivi() rend la main le plus souvent
    # (plafond DUREE_MAX_BLOCAGE_SECONDES par exécution) — le message de
    # complétion vient d'afficher_suivi elle-même, jamais affiché ici en dur,
    # pour ne jamais annoncer "terminé" avant que ce soit confirmé côté API.
    afficher_suivi(
        "pipeline_auto",
        estimation_secondes=240,
        libelle=f"Pipeline Automatique complet ({campagne_active})",
        campagne=campagne_active,
        timeout_secondes=900,
    )
