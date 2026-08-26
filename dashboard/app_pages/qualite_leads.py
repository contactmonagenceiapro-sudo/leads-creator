"""
Interface admin "Qualité des leads" — module 8 de pilotage. Score global +
détail actionnable : doublons potentiels (leads/leads_professionnels),
champs manquants sur les leads actifs (surtout l'e-mail, seul canal de
prospection utilisé par ce projet), pourcentage de leads pro jamais
enrichis depuis trop longtemps.

Calculé à la volée (voir data_access.score_qualite_leads — pas de table
dédiée, raisonnement dans son docstring) : toujours à jour, jamais de
décalage avec l'état réel de la base. Même logique réutilisée par
scripts/controle_qualite_leads.py pour un lancement en CLI/cron optionnel.

Espace admin uniquement.
"""

import streamlit as st

from common import safe_call, to_dataframe
from data_access import score_qualite_leads

st.title("🧹 Qualité des leads")
st.caption("Recalculé à chaque chargement de page — reflète toujours l'état actuel de la base.")

resultat, erreur = safe_call(score_qualite_leads)
if erreur:
    st.error(f"Impossible de calculer le score qualité : {erreur}")
    st.stop()

r = resultat
col1, col2, col3 = st.columns(3)
col1.metric("Score qualité global", f"{r['score']}/100")
col2.metric("Groupes de doublons potentiels", r["nb_groupes_doublons"])
col3.metric("Leads actifs sans e-mail", f"{r['taux_sans_email_pct']}%")

if r["score"] < 50:
    st.warning("Score sous 50/100 — une part significative de la base n'est pas exploitable en l'état (voir détail ci-dessous).")

st.divider()

st.subheader("📧 Champs manquants — priorité de correction")
st.caption("L'e-mail est le SEUL canal de prospection utilisé par ce projet — un lead actif sans e-mail n'est contactable par aucun mécanisme existant.")

tab_b2c, tab_b2b = st.tabs(["Leads B2C (artisans)", "Leads pro (B2B)"])
with tab_b2c:
    m = r["manquants_leads"]
    st.write(f"**{len(m['sans_email'])}/{m['total_actifs']}** leads actifs sans e-mail (priorité absolue) — **{len(m['sans_telephone'])}/{m['total_actifs']}** sans téléphone (secondaire).")
    if m["sans_email"]:
        df = to_dataframe(m["sans_email"])
        st.dataframe(df[["company", "status", "telephone"]], use_container_width=True, hide_index=True)
with tab_b2b:
    m = r["manquants_leads_professionnels"]
    st.write(f"**{len(m['sans_email'])}/{m['total_actifs']}** leads pro actifs sans e-mail — **{len(m['sans_telephone'])}/{m['total_actifs']}** sans téléphone.")
    if m["sans_email"]:
        df = to_dataframe(m["sans_email"])
        st.dataframe(df[["nom_entreprise", "client_final", "statut", "telephone"]], use_container_width=True, hide_index=True)

st.divider()

st.subheader("🔁 Doublons / partages potentiels")
st.caption(
    "Email et nom d'entreprise exacts sont déjà impossibles en doublon côté leads B2C "
    "(contraintes UNIQUE en base) — reste détectable : même téléphone, même SIREN, ou nom "
    "d'entreprise quasi-identique. Côté B2B, comparé uniquement au sein d'une même campagne "
    "(la même entreprise sourcée pour deux clients différents est normal, pas un doublon)."
)

tab_doublons_b2c, tab_doublons_b2b = st.tabs(["Leads B2C", "Leads pro (B2B)"])
with tab_doublons_b2c:
    total = sum(len(v) for v in r["doublons_leads"].values())
    if total == 0:
        st.success("Aucun doublon potentiel détecté.")
    for champ, groupes in r["doublons_leads"].items():
        for g in groupes:
            with st.expander(f"[{champ}] {g['valeur']} — {len(g['lignes'])} fiches"):
                st.write(", ".join(l["company"] for l in g["lignes"]))
with tab_doublons_b2b:
    total = sum(len(v) for v in r["doublons_leads_professionnels"].values())
    if total == 0:
        st.success("Aucun doublon/partage potentiel détecté.")
    else:
        st.warning(
            "Un même numéro partagé par plusieurs entreprises DIFFÉRENTES peut être un bug "
            "d'enrichissement (numéro mal attribué) plutôt qu'un vrai doublon — à vérifier "
            "au cas par cas avant toute correction."
        )
    for champ, groupes in r["doublons_leads_professionnels"].items():
        for g in groupes:
            with st.expander(f"[{champ}] {g['valeur']} — campagne {g['client_final']} — {len(g['lignes'])} fiches"):
                st.write(", ".join(l["nom_entreprise"] for l in g["lignes"]))

st.divider()

st.subheader("⏳ Enrichissement B2B stagnant")
e = r["enrichissement"]
st.write(f"**{e['nb_stagnants']}/{e['total']}** leads pro ({e['pourcentage_stagnants']}%) jamais enrichis depuis plus de {e['seuil_jours']} jours.")
if e["stagnants"]:
    df = to_dataframe(e["stagnants"])
    st.dataframe(df[["nom_entreprise", "client_final", "created_at"]], use_container_width=True, hide_index=True)
