"""
Interface admin "Santé de la base" — vue du système de surveillance continue
de Supabase (voir sql/init_sante_base_donnees.sql,
scripts/controle_sante_bdd.py, .github/workflows/controle_sante_bdd.yml,
cron quotidien). Objectif : rendre visible ici ce que le cron détecte déjà
tout seul (RLS manquant, demandes de devis bloquées, réclamations en
retard...) plutôt que de le découvrir par hasard lors d'un test manuel —
voir sql/init_demandes_devis_particuliers_confirmation.sql pour l'incident
qui a motivé ce système.

Espace admin uniquement — lecture seule, cette page ne déclenche jamais les
contrôles elle-même (ils tournent par cron), elle n'en affiche que le
résultat déjà écrit en base.
"""

import pandas as pd
import streamlit as st

from common import safe_call, to_dataframe
from data_access import get_sante_bdd_derniers_par_type, get_sante_bdd_historique

try:
    from scripts.controle_sante_bdd import ACTIONS_CONCRETES
except ImportError:
    ACTIONS_CONCRETES = {}

LIBELLES_CONTROLES = {
    "couverture_rls": "Couverture RLS",
    "fonctions_rpc_exposees": "Fonctions SECURITY DEFINER exposées",
    "demandes_devis_bloquees": "Demandes de devis bloquées",
    "reclamations_en_retard": "Réclamations en retard",
    "erreurs_non_resolues": "Erreurs non résolues",
    "croissance_table": "Croissance anormale d'une table",
    "donnees_test_residuelles": "Données de test résiduelles",
    "fk_non_indexees": "Clés étrangères non indexées",
}

ICONES_STATUT = {"ok": "✅", "attention": "⚠️", "critique": "🚨"}
ORDRE_GRAVITE = {"critique": 0, "attention": 1, "ok": 2}
COULEUR_STATUT = {"ok": "#2d6a4f", "attention": "#e9a13b", "critique": "#c1121f"}

st.title("🩺 Santé de la base de données")
st.caption(
    "Résultat du contrôle automatisé quotidien (`scripts/controle_sante_bdd.py`, "
    "voir `.github/workflows/controle_sante_bdd.yml`) — cette page n'exécute rien, "
    "elle affiche uniquement ce que le dernier run a trouvé."
)

derniers, erreur_derniers = safe_call(get_sante_bdd_derniers_par_type)
historique, erreur_historique = safe_call(get_sante_bdd_historique, 30)

if erreur_derniers or erreur_historique:
    st.error(f"Impossible de charger les données de santé : {erreur_derniers or erreur_historique}")
    st.stop()

derniers_par_type = derniers["derniers"]
controles_historique = historique["controles"]

if not derniers_par_type:
    st.info(
        "Aucun contrôle enregistré pour l'instant — la table `sante_base_donnees` est vide. "
        "Lancez `python3 scripts/controle_sante_bdd.py` une première fois, ou attendez le "
        "prochain run automatique (`.github/workflows/controle_sante_bdd.yml`, cron quotidien)."
    )
    st.stop()

# --- Statut global, en un coup d'œil ---------------------------------------

pire_statut = min((d["statut"] for d in derniers_par_type.values()), key=lambda s: ORDRE_GRAVITE[s])
date_derniere_ligne = max(d["date_controle"] for d in derniers_par_type.values())

st.markdown(
    f"""
    <div style="border-left: 6px solid {COULEUR_STATUT[pire_statut]}; padding: 0.75rem 1rem;
                background: {COULEUR_STATUT[pire_statut]}22; border-radius: 4px; margin-bottom: 1rem;">
        <span style="font-size: 1.4rem; font-weight: 600;">
            {ICONES_STATUT[pire_statut]} Statut global : {pire_statut.upper()}
        </span><br/>
        <span style="opacity: 0.8;">Dernier contrôle enregistré : {date_derniere_ligne}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

colonnes_resume = st.columns(len(LIBELLES_CONTROLES))
for col, (type_controle, libelle) in zip(colonnes_resume, LIBELLES_CONTROLES.items()):
    ligne = derniers_par_type.get(type_controle)
    with col:
        if ligne:
            st.metric(libelle, ICONES_STATUT[ligne["statut"]] + " " + ligne["statut"])
        else:
            st.metric(libelle, "— jamais exécuté")

st.divider()

# --- Détail des contrôles en attention/critique, avec action concrète ------

problemes = sorted(
    (d for d in derniers_par_type.values() if d["statut"] != "ok"),
    key=lambda d: ORDRE_GRAVITE[d["statut"]],
)

st.subheader("Contrôles à traiter")
if not problemes:
    st.success("Aucun contrôle en attention ou critique — tout est ok.")
else:
    for ligne in problemes:
        type_controle = ligne["type_controle"]
        libelle = LIBELLES_CONTROLES.get(type_controle, type_controle)
        with st.expander(
            f"{ICONES_STATUT[ligne['statut']]} {libelle} — {ligne['statut']} (contrôlé le {ligne['date_controle']})",
            expanded=(ligne["statut"] == "critique"),
        ):
            action = ACTIONS_CONCRETES.get(type_controle)
            if action:
                st.markdown(f"**Action à prendre :** {action}")
            st.json(ligne["detail"])

st.divider()

# --- Historique 30 jours, avec tendance -------------------------------------

st.subheader("Historique (30 derniers jours)")

df_historique = to_dataframe(controles_historique)
if df_historique.empty:
    st.caption("Pas assez d'historique sur les 30 derniers jours pour afficher une tendance.")
else:
    df_historique["date_controle"] = pd.to_datetime(df_historique["date_controle"])

    onglet_statuts, onglet_croissance, onglet_brut = st.tabs(
        ["Statuts par contrôle", "Croissance des tables", "Détail brut"]
    )

    with onglet_statuts:
        statut_num = {"ok": 0, "attention": 1, "critique": 2}
        df_statuts = df_historique.copy()
        df_statuts["libelle"] = df_statuts["type_controle"].map(LIBELLES_CONTROLES).fillna(df_statuts["type_controle"])
        df_statuts["gravite"] = df_statuts["statut"].map(statut_num)
        pivot = df_statuts.pivot_table(
            index="date_controle", columns="libelle", values="gravite", aggfunc="max"
        )
        st.caption("0 = ok, 1 = attention, 2 = critique — une ligne qui monte est un contrôle qui se dégrade.")
        st.line_chart(pivot)

    with onglet_croissance:
        lignes_croissance = df_historique[df_historique["type_controle"] == "croissance_table"]
        if lignes_croissance.empty:
            st.caption("Aucune donnée de croissance enregistrée sur la période.")
        else:
            comptes = pd.json_normalize(lignes_croissance["detail"])["comptes"]
            df_comptes = pd.json_normalize(comptes)
            df_comptes.index = lignes_croissance["date_controle"].values
            df_comptes = df_comptes.sort_index()
            st.caption("Nombre de lignes par table principale, au fil des contrôles quotidiens.")
            st.line_chart(df_comptes)

    with onglet_brut:
        st.dataframe(
            df_historique[["date_controle", "type_controle", "statut"]].sort_values(
                "date_controle", ascending=False
            ),
            use_container_width=True,
            hide_index=True,
        )
