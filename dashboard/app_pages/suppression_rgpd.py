"""
[Admin] Suppression RGPD — droit à l'effacement (art. 17 RGPD).

Formalise un processus jusqu'ici manuel : recherche un e-mail dans `leads`
(B2C) ET `leads_professionnels` (B2B), affiche ce qui SERAIT supprimé pour
vérification visuelle, puis (sur double confirmation explicite) :
    1) supprime définitivement les lignes trouvées,
    2) blackliste l'e-mail (email_blacklist.py — empêche tout recontact
       futur, même après un nouveau scraping qui recréerait une ligne),
    3) trace l'opération dans registre_suppressions_rgpd (voir
       sql/init_registre_suppressions_rgpd.sql, à exécuter une fois dans
       Supabase avant la première utilisation de cette page).

Traite aussi le cas où RIEN n'est trouvé (email déjà supprimé, ou jamais
présent en base) : la demande doit être tracée comme traitée quand même,
pas silencieusement ignorée — voir le formulaire de confirmation plus bas,
qui reste disponible même sans résultat.

Ce que l'admin voit et confirme est exactement ce qui est supprimé : les
ids affichés lors de la recherche sont transmis tels quels à
data_access.supprimer_donnees_rgpd, qui ne re-filtre jamais par email au
moment de l'action (voir sa docstring).
"""

from datetime import datetime, time, timezone

import streamlit as st

from common import safe_call, to_dataframe
from data_access import get_registre_suppressions_rgpd, rechercher_email_rgpd, supprimer_donnees_rgpd

st.title("🗑️ Suppression RGPD (droit à l'effacement)")
st.caption(
    "Recherche un e-mail dans `leads` et `leads_professionnels`, vérifie "
    "visuellement ce qui serait supprimé, puis supprime définitivement + "
    "blackliste l'adresse (empêche tout recontact futur, même après un "
    "nouveau scraping)."
)

# ---------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------

with st.form("form_recherche_rgpd"):
    email_saisi = st.text_input("E-mail à rechercher / supprimer").strip().lower()
    rechercher = st.form_submit_button("🔍 Rechercher", type="primary")

if rechercher:
    if not email_saisi:
        st.warning("Merci de renseigner un e-mail.")
    else:
        resultat, erreur = safe_call(rechercher_email_rgpd, email_saisi)
        if erreur:
            st.error(erreur)
        else:
            st.session_state["rgpd_recherche"] = resultat
            # Nouvelle recherche -> on annule toute confirmation en attente
            # d'une recherche précédente, pour ne jamais pouvoir valider la
            # suppression d'un email autre que celui juste recherché.
            st.session_state.pop("rgpd_confirme", None)

recherche = st.session_state.get("rgpd_recherche")

# ---------------------------------------------------------------------
# Résultat + confirmation
# ---------------------------------------------------------------------

if recherche:
    email = recherche["email"]
    leads = recherche["leads"]
    leads_pro = recherche["leads_professionnels"]
    total_trouve = len(leads) + len(leads_pro)

    st.divider()
    st.subheader(f"Résultat pour « {email} »")

    if total_trouve == 0:
        st.info(
            "Aucune ligne trouvée dans `leads` ni `leads_professionnels` pour cet "
            "e-mail. La demande peut quand même être tracée ci-dessous (cas d'un "
            "email déjà supprimé, ou jamais présent en base) — aucune ligne ne "
            "sera supprimée, mais l'email sera blacklisté et l'opération "
            "enregistrée dans le registre pour prouver que la demande a été traitée."
        )
    else:
        if leads:
            st.markdown(f"**{len(leads)} ligne(s) dans `leads` (B2C artisans)**")
            st.dataframe(
                to_dataframe(leads)[["company", "name", "status", "created_at"]],
                use_container_width=True, hide_index=True,
            )
        if leads_pro:
            st.markdown(f"**{len(leads_pro)} ligne(s) dans `leads_professionnels` (B2B)**")
            st.dataframe(
                to_dataframe(leads_pro)[["nom_entreprise", "type_acteur", "client_final", "statut", "created_at"]],
                use_container_width=True, hide_index=True,
            )

    st.divider()
    st.markdown("### Confirmer le traitement de la demande")
    if total_trouve > 0:
        st.warning(
            f"⚠️ Cette action supprime **définitivement** {total_trouve} ligne(s) "
            "ci-dessus, sans possibilité d'annulation."
        )

    date_demande = st.date_input(
        "Date de la demande (si différente d'aujourd'hui — ex: demande reçue "
        "par e-mail quelques jours avant le traitement)",
        value=datetime.now(timezone.utc).date(),
        key="rgpd_date_demande",
    )
    traite_par = st.text_input(
        "Traité par",
        value="Expertise Digitale - traitement manuel dashboard",
        key="rgpd_traite_par",
    )
    confirme = st.checkbox(
        "Je confirme avoir vérifié qu'il s'agit bien de la bonne personne et je "
        "souhaite procéder à la suppression définitive.",
        key="rgpd_confirme",
    )

    if st.button("🗑️ Confirmer la suppression", type="primary", disabled=not confirme):
        date_demande_iso = datetime.combine(date_demande, time.min, tzinfo=timezone.utc).isoformat()
        leads_ids = [ligne["id"] for ligne in leads]
        leads_pro_ids = [ligne["id"] for ligne in leads_pro]
        resultat, erreur = safe_call(
            supprimer_donnees_rgpd, email, leads_ids, leads_pro_ids, date_demande_iso, traite_par
        )
        if erreur:
            st.error(erreur)
        else:
            st.success(
                f"✅ Demande traitée pour « {email} » — "
                f"{resultat['supprimes_leads']} ligne(s) supprimée(s) dans `leads`, "
                f"{resultat['supprimes_leads_professionnels']} dans "
                "`leads_professionnels`, email blacklisté, opération tracée dans "
                "le registre ci-dessous."
            )
            st.session_state.pop("rgpd_recherche", None)
            st.session_state.pop("rgpd_confirme", None)

# ---------------------------------------------------------------------
# Registre (lecture seule)
# ---------------------------------------------------------------------

st.divider()
st.subheader("📜 Registre des suppressions (20 dernières)")
st.caption(
    "email_hash = SHA256 de l'e-mail normalisé (pas l'email en clair — voir "
    "sql/init_registre_suppressions_rgpd.sql) : ce registre prouve qu'une "
    "suppression a eu lieu sans reconstituer une liste de contacts."
)
registre, erreur_registre = safe_call(get_registre_suppressions_rgpd, 20)
if erreur_registre:
    st.error(erreur_registre)
elif registre and registre["suppressions"]:
    st.dataframe(to_dataframe(registre["suppressions"]), use_container_width=True, hide_index=True)
else:
    st.caption("Aucune suppression enregistrée pour le moment.")
