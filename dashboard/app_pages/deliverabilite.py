"""
Interface "Délivrabilité" — suivi de la montée en charge progressive
(warmup) du domaine d'envoi B2B et vérification live des enregistrements
DNS (SPF/DKIM/DMARC) indispensables à la délivrabilité.

Espace admin uniquement : c'est une donnée d'infrastructure d'envoi
(boîte Zoho partagée par toutes les campagnes B2B), pas une donnée propre
à une campagne client — jamais accessible depuis le portail client (voir
dashboard/app.py).

La vérification DNS interroge l'API publique DNS-over-HTTPS de Google
(https://dns.google/resolve) plutôt que d'ajouter une dépendance dédiée
(type dnspython) : une simple requête HTTP JSON suffit, `requests` est déjà
une dépendance du dashboard.
"""

import requests
import streamlit as st

from common import safe_call
from data_access import get_taux_reponse, get_warmup_status

st.title("📶 Délivrabilité")
st.caption("Montée en charge du domaine d'envoi (warmup) & enregistrements DNS SPF/DKIM/DMARC")

# ---------------------------------------------------------------------
# Montée en charge progressive (warmup)
# ---------------------------------------------------------------------

st.subheader("Montée en charge de l'envoi B2B")
st.caption(
    "Plafond quotidien partagé par toutes les campagnes B2B (même boîte d'envoi) — "
    "voir PALIERS_RAMP_ENVOI_QUOTIDIEN dans outbound_chantiers/config.py."
)

ramp, erreur = safe_call(get_warmup_status)
if erreur:
    st.error(erreur)
elif ramp:
    col1, col2, col3 = st.columns(3)
    col1.metric("Jour de warmup", f"J{ramp['jour_ramp']}")
    col2.metric("Envoyés aujourd'hui", f"{ramp['envoyes_aujourdhui']} / {ramp['plafond_jour']}")
    col3.metric("Budget restant aujourd'hui", ramp["budget_restant"])

    if ramp["plafond_jour"] > 0:
        st.progress(min(ramp["envoyes_aujourdhui"] / ramp["plafond_jour"], 1.0))

    if ramp["budget_restant"] <= 0:
        st.warning("Plafond du jour atteint — plus aucun envoi B2B (premier contact ou relance) avant demain.")

    with st.expander("Paliers de montée en charge"):
        for seuil_jours, plafond in ramp["paliers"]:
            st.caption(f"À partir du jour {seuil_jours + 1} : {plafond} e-mails B2B / jour")
        st.caption(
            "Ce plafond protège la réputation du domaine pendant la construction de sa "
            "réputation — il complète un warmup tiers (Instantly, Lemwarm, Warmup Inbox...) "
            "s'il y en a un, il ne le remplace pas."
        )

st.divider()

# ---------------------------------------------------------------------
# Taux de réponse (artisans + par campagne B2B)
# ---------------------------------------------------------------------

st.subheader("Taux de réponse")
st.caption(
    "Calculé à partir des événements 'envoye'/'repondu' journalisés dans email_events "
    "(voir mail_processor.py::journaliser_reponse) — une réponse compte pour un lead qui "
    "a répondu au moins une fois, jamais un OOF ni un bounce. Pas de suivi d'ouverture : "
    "le pixel de tracking a été retiré (voir email_tracking.py)."
)

reponses, erreur_reponses = safe_call(get_taux_reponse)
if erreur_reponses:
    st.error(erreur_reponses)
elif reponses:
    st.markdown("**Artisans (B2C)**")
    art = reponses["artisans"]
    col1, col2, col3 = st.columns(3)
    col1.metric("E-mails envoyés", art["envoyes"])
    col2.metric("Réponses reçues", art["repondus"])
    col3.metric("Taux de réponse", f"{art['taux'] * 100:.1f} %")

    if reponses["b2b_par_client"]:
        st.markdown("**Campagnes B2B**")
        st.dataframe(
            [
                {
                    "Campagne": ligne["client_final"],
                    "E-mails envoyés": ligne["envoyes"],
                    "Réponses reçues": ligne["repondus"],
                    "Taux de réponse": f"{ligne['taux'] * 100:.1f} %",
                }
                for ligne in reponses["b2b_par_client"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Aucun envoi B2B journalisé pour l'instant.")

st.divider()

# ---------------------------------------------------------------------
# Vérification DNS (SPF / DKIM / DMARC)
# ---------------------------------------------------------------------

st.subheader("Vérification des enregistrements DNS")
st.caption(
    "Contrôle en direct des enregistrements SPF, DKIM et DMARC du domaine d'envoi — "
    "indispensables pour que les e-mails de prospection n'atterrissent pas en spam."
)

DNS_OVER_HTTPS_URL = "https://dns.google/resolve"
TIMEOUT_DNS_SECONDES = 8


def _requete_dns(nom: str, type_enregistrement: str = "TXT") -> list[str]:
    """Renvoie la liste des valeurs TXT trouvées pour `nom` (liste vide si
    aucun enregistrement ou domaine inexistant — jamais d'exception : une
    vérification indisponible reste juste 'non trouvé', pas une erreur qui
    ferait planter la page)."""
    try:
        reponse = requests.get(
            DNS_OVER_HTTPS_URL,
            params={"name": nom, "type": type_enregistrement},
            timeout=TIMEOUT_DNS_SECONDES,
        )
        reponse.raise_for_status()
        return [a["data"].strip('"') for a in reponse.json().get("Answer", [])]
    except requests.exceptions.RequestException:
        return []


with st.form("form_verif_dns"):
    domaine = st.text_input(
        "Domaine d'envoi à vérifier",
        placeholder="ex: outreach.expertise-digitale.fr",
        help="Le domaine réellement utilisé comme adresse d'envoi (voir ZOHO_USER côté API) — "
        "PAS zohomail.eu si vous envoyez encore depuis une boîte Zoho non personnalisée : "
        "ce domaine partagé ne peut de toute façon pas porter vos propres enregistrements DNS.",
    )
    selecteur_dkim = st.text_input(
        "Sélecteur DKIM",
        value="zmail",
        help="Visible dans l'admin Zoho Mail (Domaines > DKIM) au moment de l'activation — "
        "'zmail' est la valeur la plus courante par défaut chez Zoho, mais peut différer.",
    )
    verifier = st.form_submit_button("🔍 Vérifier", type="primary")

if verifier:
    if not domaine.strip():
        st.warning("Merci de renseigner un domaine.")
    else:
        domaine = domaine.strip().lower()

        spf = [v for v in _requete_dns(domaine) if v.startswith("v=spf1")]
        if spf:
            st.success(f"✅ SPF trouvé : `{spf[0]}`")
            if "zoho" not in spf[0].lower():
                st.caption("⚠️ Ne référence pas Zoho (`include:zoho.eu`) — vérifie que c'est le bon enregistrement.")
        else:
            st.error(
                "❌ Aucun enregistrement SPF trouvé — ajoute un TXT à la racine du domaine : "
                "`v=spf1 include:zoho.eu ~all`"
            )

        dkim_nom = f"{selecteur_dkim.strip()}._domainkey.{domaine}"
        dkim = [v for v in _requete_dns(dkim_nom) if "DKIM1" in v]
        if dkim:
            st.success(f"✅ DKIM trouvé sur le sélecteur « {selecteur_dkim} »")
        else:
            st.error(
                f"❌ Aucun DKIM trouvé sur `{dkim_nom}` — active DKIM dans l'admin Zoho Mail "
                "(Domaines > DKIM) et vérifie le sélecteur exact fourni."
            )

        dmarc = [v for v in _requete_dns(f"_dmarc.{domaine}") if v.startswith("v=DMARC1")]
        if dmarc:
            st.success(f"✅ DMARC trouvé : `{dmarc[0]}`")
            if "p=none" in dmarc[0]:
                st.caption(
                    "ℹ️ Politique en observation (`p=none`) — normal en warmup. À durcir vers "
                    "`p=quarantine` puis `p=reject` une fois les rapports DMARC propres."
                )
        else:
            st.error(
                f"❌ Aucun DMARC trouvé sur `_dmarc.{domaine}` — ajoute un TXT : "
                f"`v=DMARC1; p=none; rua=mailto:dmarc-reports@{domaine}; pct=100`"
            )
