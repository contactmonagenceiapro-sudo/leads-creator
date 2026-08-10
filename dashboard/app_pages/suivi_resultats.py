"""
Interface "Suivi et Résultats des Actions" — vue centralisée du statut,
des logs et de l'historique de chaque action de fond (scraping, traitement
IA, campagnes, relances, vérification IMAP), déclenchées en subprocess par
process_runner.py (voir sa docstring).

Espace admin uniquement (même contrainte que sourcing.py/gestion_clients.py
— ce sont des actions d'infrastructure/campagne, jamais une donnée propre à
un client).

Différence avec le "✅ .../❌ ... lancé(e)" déjà affiché sur les pages
Sourcing/Gestion : ici, chaque action a son propre historique PERSISTANT
(fichiers logs/historique_<action>.json, écrits par
common.py::afficher_suivi() à la fin de chaque run) — visible même après
un redémarrage du serveur ou depuis un autre onglet, contrairement à
process_runner.statut() qui ne connaît que les runs suivis dans la session
navigateur courante (voir process_runner.py, section "Limite connue").
Cette page ne duplique pas la logique de lancement : elle réutilise
exactement les mêmes fonctions process_runner.lancer_*() que
sourcing.py/gestion_clients.py, donc lancer une action ici ou là-bas
revient strictement au même (même clé de suivi, même log, même historique).
"""

from datetime import datetime, timezone

import streamlit as st

import process_runner
from common import afficher_suivi, executer_avec_spinner, liste_noms_campagnes, safe_call
from data_access import compter_relances_envoyees_depuis, get_campagnes, get_stats_par_zone_artisans

st.title("📈 Suivi et Résultats des Actions")
st.caption(
    "Statut, logs et historique de chaque action de fond — un run lancé "
    "depuis cette page ou depuis Sourcing/Gestion & Réponse est exactement "
    "le même run (même suivi, mêmes logs)."
)


def _horodatage_lisible(iso: str | None) -> str:
    if not iso:
        return "?"
    return iso[:19].replace("T", " ") + " UTC"


def _ligne_historique(entree: dict) -> str:
    emoji = "✅" if entree.get("statut") == "succes" else "❌"
    ligne = f"{emoji} {_horodatage_lisible(entree.get('termine_a'))} — {entree.get('duree_secondes', '?')}s"
    if entree.get("resume"):
        ligne += f" — {entree['resume']}"
    return ligne


def _cle_debut(action: str, campagne: str | None) -> str:
    """Horodatage de lancement, capturé juste avant fn_lancer() dans
    bloc_action() ci-dessous — sert de borne basse à
    compter_relances_envoyees_depuis() une fois le run terminé (voir le
    resume_a_la_fin de l'onglet Relances), pour ne compter que les relances
    de CE run, jamais celles d'un run précédent."""
    return f"_debut_at::{action}::{campagne or '_default_'}"


def bloc_action(
    action: str,
    titre: str,
    fn_lancer,
    estimation_secondes: int,
    campagne: str | None = None,
    resume_a_la_fin=None,
    desactive: bool = False,
    raison_desactive: str = "",
) -> None:
    """Un bloc complet et autonome pour UNE action : dernier statut connu
    (persistant, voir docstring du module), bouton de lancement, suivi en
    direct si en cours, logs bruts, et historique des derniers runs."""
    st.markdown(f"#### {titre}")

    historique = process_runner.lire_historique(action, campagne, limite=10)
    if historique:
        st.caption(_ligne_historique(historique[0]))
    else:
        st.caption("Aucun run enregistré pour l'instant.")

    en_cours = process_runner.est_en_cours(action, campagne)
    col_bouton, _ = st.columns([1, 2])
    with col_bouton:
        cle = f"lancer_{action}_{campagne or '_default_'}"
        if st.button(
            "▶️ Lancer", key=cle, use_container_width=True, disabled=en_cours or desactive
        ):
            # Capturé AVANT le lancement (pas après) : voir _cle_debut, sert
            # de référence à un éventuel resume_a_la_fin (ex: onglet
            # Relances) pour ne compter que le résultat de CE run.
            st.session_state[_cle_debut(action, campagne)] = datetime.now(timezone.utc).isoformat()
            _, err = executer_avec_spinner("Déclenchement en cours...", fn_lancer)
            if err:
                st.error(err)
            else:
                st.success("Lancé.")
        elif en_cours:
            st.caption("⏳ Déjà en cours...")
        elif desactive and raison_desactive:
            st.caption(raison_desactive)

    afficher_suivi(
        action,
        estimation_secondes=estimation_secondes,
        libelle=titre,
        campagne=campagne,
        resume_a_la_fin=resume_a_la_fin,
    )

    log_tail = process_runner.dernieres_lignes_log(action, campagne)
    if log_tail:
        with st.expander("📄 Détails techniques (fin des logs)"):
            st.code(log_tail, language=None)

    if len(historique) > 1:
        with st.expander(f"🕓 Historique ({len(historique)} derniers runs)"):
            for entree in historique:
                st.caption(_ligne_historique(entree))

    st.divider()


campagnes_data, campagnes_error = safe_call(get_campagnes)
noms_campagnes = liste_noms_campagnes(campagnes_data) if not campagnes_error else []

tab_sourcing, tab_campagnes, tab_relances, tab_bounces = st.tabs(
    [
        "🔍 Sourcing & Traitement IA",
        "🚀 Campagnes & Rapports",
        "🔄 Relances automatiques",
        "📥 Réponses & Bounces",
    ]
)

with tab_sourcing:
    st.markdown("#### 🗺️ Répartition par zone")
    st.caption(
        "Le pipeline artisans n'a pas de notion de campagne (contrairement au B2B, "
        "onglet suivant) : Lyon et Grand Est partagent le même scraping, le même "
        "traitement IA et le même budget d'envoi quotidien — cette vue reconstitue "
        "juste une visibilité séparée à partir de la commune de chaque lead."
    )
    zones_data, zones_error = safe_call(get_stats_par_zone_artisans)
    if zones_error:
        st.error(zones_error)
    elif zones_data:
        colonnes = st.columns(3)
        for col, nom_zone in zip(colonnes, ("Lyon", "Grand Est", "Autre")):
            zone = zones_data.get(nom_zone, {})
            with col:
                st.metric(nom_zone, zone.get("total", 0))
                st.caption(
                    f"À contacter : {zone.get('a_contacter', 0)} · "
                    f"Contactés : {zone.get('contactes', 0)} · "
                    f"Score moyen : {zone.get('score_moyen', 0)}"
                )
    st.divider()

    bloc_action(
        "scraping", "🔍 Scraping des artisans", process_runner.lancer_scraping, estimation_secondes=90
    )
    bloc_action(
        "leads",
        "🤖 Traitement IA des leads",
        process_runner.lancer_leads,
        estimation_secondes=120,
        desactive=not process_runner.leads_json_disponible(),
        raison_desactive="⚠️ Lance d'abord le scraping — leads.json n'existe pas encore.",
    )

with tab_campagnes:
    bloc_action(
        "ceo_report",
        "🚀 Campagne + rapport — artisans",
        process_runner.lancer_ceo_report,
        estimation_secondes=180,
    )

    st.markdown("---")

    if noms_campagnes:
        campagne_b2b = st.selectbox(
            "📌 Campagne B2B", options=noms_campagnes, key="select_campagne_suivi"
        )
        bloc_action(
            "envoi_pro",
            f"🏗️ Campagne + relances B2B — {campagne_b2b}",
            lambda: process_runner.lancer_pipeline_b2b("envoi_pro", campagne_b2b),
            estimation_secondes=60,
            campagne=campagne_b2b,
        )
    else:
        st.info("Aucune campagne B2B configurée — crée-en une depuis « Sourcing / Scraping ».")

with tab_relances:
    bloc_action(
        "relance",
        "🔄 Relance des sans-réponse — artisans",
        process_runner.lancer_relance,
        estimation_secondes=90,
        resume_a_la_fin=lambda: (
            f"{compter_relances_envoyees_depuis(st.session_state[_cle_debut('relance', None)])} "
            "e-mail(s) de relance envoyé(s)."
            if st.session_state.get(_cle_debut("relance", None))
            else None
        ),
    )
    st.caption(
        "Les relances B2B sont incluses dans « Campagne + relances B2B » "
        "(onglet Campagnes & Rapports) — pas d'action séparée côté B2B."
    )

with tab_bounces:
    bloc_action(
        "mail_check",
        "📥 Vérifier les réponses (IMAP) & traiter les bounces",
        process_runner.lancer_mail_check,
        estimation_secondes=30,
    )
