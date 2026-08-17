"""
Dashboard ai-company — Portail Admin & Client, 100% Streamlit (plus de
backend FastAPI séparé : accès direct à Supabase, scripts de fond lancés en
subprocess depuis ce process — voir data_access.py / process_runner.py).

Lancement :
    streamlit run app.py

Structure (multi-pages) :
    app.py                        -> point d'entrée : secrets, routing public, connexion, navigation
    secrets_loader.py             -> chargement tolérant de st.secrets (jamais de crash brut)
    auth.py                       -> écran de login + session_state (bcrypt direct, plus de JWT)
    pages_publiques.py            -> [Public, sans connexion] présentation / intake / devis
    app_pages/sourcing.py           -> [Admin] Sourcing / Scraping
    app_pages/gestion_clients.py    -> [Admin] Gestion & Réponse aux clients
    app_pages/administration_contrats.py -> [Admin] Administration & Contrats
    app_pages/deliverabilite.py     -> [Admin] Délivrabilité
    app_pages/demandes_devis.py     -> [Admin] Demandes de devis (rapprochement avec les artisans clients)
    app_pages/finances.py           -> [Admin] Finances (CA, MRR, activité commerciale B2C)
                                        (RETIRÉE TEMPORAIREMENT de la navigation, voir
                                        commentaire dans la section navigation ci-dessous)
    app_pages/suppression_rgpd.py   -> [Admin] Suppression RGPD (droit à l'effacement)
    app_pages/portail_client.py     -> [Client] Vue restreinte à ses propres campagnes
                                        (également accessible à l'admin, en aperçu/test)

Le dossier s'appelle "app_pages/" et NON "pages/" : Streamlit détecte
automatiquement tout dossier littéralement nommé "pages/" à côté du script
d'entrée et construit SA PROPRE navigation en plus de celle définie ici via
st.navigation()/st.Page() — les deux mécanismes entrent alors en conflit.

Pages PUBLIQUES (dashboard/pages_publiques.py) : accessibles via un
paramètre d'URL dédié (?vue=presentation|intake|devis|demande_devis|signature|confidentialite),
interceptées ici AVANT auth.exiger_connexion() — ce sont les liens envoyés
aux prospects par e-mail (voir mail_processor.py::envoyer_suivi_positif) ou
partagés publiquement (demande_devis), qui ne doivent évidemment pas
nécessiter de compte.
"""

import sys
from pathlib import Path

# La racine du dépôt doit être sur sys.path AVANT tout autre import de ce
# fichier (ou de tout module dashboard/ qu'il importe) : plusieurs modules
# du dashboard importent des scripts qui vivent à la racine du dépôt, pas
# dans dashboard/ (ceo_agent.py via contrats_signature.py, le package
# outbound_chantiers via data_access.py) — sans cet ajout explicite, Python
# ne les trouve pas (streamlit ne place que le dossier de app.py sur
# sys.path, pas son parent).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="ai-company · Dashboard",
    page_icon="📊",
    layout="wide",
)

# Doit s'exécuter EN TOUT PREMIER, avant tout import de auth/data_access/
# pages_publiques (qui importent tous supabase_client, lequel plante de
# façon bien moins lisible si SUPABASE_URL/SUPABASE_KEY manquent) — voir
# secrets_loader.py pour le détail (tolérant aux sections TOML imbriquées,
# à st.secrets inaccessible, affiche un message clair + st.stop() propre
# plutôt qu'un traceback si une clé critique manque).
from secrets_loader import initialiser_secrets  # noqa: E402

initialiser_secrets()

# ---------------------------------------------------------------------
# Routing des pages PUBLIQUES — avant tout gate d'authentification.
# ---------------------------------------------------------------------
_vue_publique = st.query_params.get("vue")
if _vue_publique in ("presentation", "intake", "devis", "demande_devis", "signature", "confidentialite"):
    from pages_publiques import (
        afficher_confidentialite,
        afficher_demande_devis,
        afficher_devis,
        afficher_intake,
        afficher_presentation,
        afficher_signature,
    )

    if _vue_publique == "presentation":
        afficher_presentation(st.query_params.get("lead_id"))
    elif _vue_publique == "intake":
        afficher_intake(st.query_params.get("lead_id"))
    elif _vue_publique == "signature":
        afficher_signature(st.query_params.get("token"))
    elif _vue_publique == "confidentialite":
        afficher_confidentialite()
    elif _vue_publique == "demande_devis":
        afficher_demande_devis()
    else:
        afficher_devis(st.query_params.get("slug"))
    st.stop()


from auth import campagnes_autorisees, deconnexion, est_admin, exiger_connexion  # noqa: E402
from common import safe_call  # noqa: E402
from data_access import get_health  # noqa: E402

exiger_connexion()


# ---------------------------------------------------------------------
# Sidebar — commun à toutes les pages
# ---------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ ai-company")

    role_libelle = "🛠️ Admin" if est_admin() else "👤 Client"
    st.markdown(f"**{st.session_state.get('auth_email', '')}**  \n{role_libelle}")
    if not est_admin():
        for campagne in campagnes_autorisees():
            st.caption(f"📌 {campagne}")

    if st.button("🚪 Se déconnecter", use_container_width=True):
        deconnexion()
    if st.button("🔄 Rafraîchir les données", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()

    if est_admin():
        st.subheader("État des services")
        health, health_error = safe_call(get_health)
        if health_error:
            st.error(health_error)
        elif health:
            for cle in ("supabase", "ollama"):
                valeur = health.get(cle, "?")
                if valeur == "ok":
                    emoji = "🟢"
                elif "degraded" in str(valeur):
                    emoji = "🟡"
                elif "non configuré" in str(valeur):
                    # Choix assumé (pas de budget LLM cloud actuellement,
                    # voir data_access.get_health), jamais une panne — même
                    # code couleur que "Zoho/Discord non configuré" plus bas.
                    emoji = "⚪"
                else:
                    emoji = "🔴"
                st.caption(f"{emoji} {cle.capitalize()} : {valeur}")
            st.caption("🟢 Zoho configuré" if health.get("zoho_configure") else "⚪ Zoho non configuré")
            st.caption("🟢 Discord configuré" if health.get("discord_configure") else "⚪ Discord non configuré")
            if health.get("dnspython") == "ok":
                st.caption("🟢 dnspython installé")
            elif "dnspython" in health:
                st.caption(f"🔴 dnspython : {health['dnspython']}")
            else:
                # Clé absente (pas juste "échec du contrôle") : la plupart
                # du temps signe un déploiement pas encore à jour avec le
                # code de get_health() qui vérifie dnspython — un
                # "Reboot app" depuis Streamlit Community Cloud force une
                # réinstallation complète et un redémarrage propre.
                st.caption("🟡 dnspython : app pas encore redéployée avec ce contrôle (reboot conseillé)")
        st.divider()

    st.caption("ai-company · dashboard")


# ---------------------------------------------------------------------
# Navigation — dépend uniquement du rôle du compte connecté, jamais d'un
# choix manuel : un compte client ne doit jamais pouvoir atteindre les
# pages admin.
# ---------------------------------------------------------------------

if est_admin():
    page_sourcing = st.Page("app_pages/sourcing.py", title="Sourcing / Scraping", icon="🔍")
    page_gestion = st.Page("app_pages/gestion_clients.py", title="Gestion & Réponse", icon="📇")
    page_administration = st.Page(
        "app_pages/administration_contrats.py", title="Administration & Contrats", icon="📑"
    )
    page_deliverabilite = st.Page("app_pages/deliverabilite.py", title="Délivrabilité", icon="📶")
    page_demandes_devis = st.Page("app_pages/demandes_devis.py", title="Demandes de devis", icon="📮")
    page_suivi = st.Page("app_pages/suivi_resultats.py", title="Suivi & Résultats", icon="📈")
    # RETIRÉE TEMPORAIREMENT de la navigation (2026-08-17, veille d'une démo
    # bloquante) : un ImportError sur get_contracts_finances a été signalé
    # en production (Streamlit Cloud) juste après le déploiement de cette
    # page. Non reproduit en local (la fonction existe bien dans
    # data_access.py, le commit est bien sur origin/main, un vrai serveur
    # Streamlit démarre sans erreur sur ce code) — cause racine probable :
    # décalage de build/cache côté Streamlit Cloud plutôt qu'un bug de
    # code, mais retrait de précaution le temps de confirmer calmement.
    # app_pages/finances.py et data_access.get_contracts_finances restent
    # en place tels quels (rien à corriger dedans à ce stade) : seule cette
    # ligne d'enregistrement + la suivante dans st.navigation() ci-dessous
    # sont commentées, pour pouvoir réactiver la page d'un simple retrait
    # de commentaire une fois la cause confirmée.
    # page_finances = st.Page("app_pages/finances.py", title="Finances", icon="💰")
    # Admin uniquement (jamais côté Portail Client) : supprime des données
    # réelles de façon irréversible — voir app_pages/suppression_rgpd.py.
    page_suppression_rgpd = st.Page("app_pages/suppression_rgpd.py", title="Suppression RGPD", icon="🗑️")
    # Portail Client accessible en aperçu à l'admin (choix de la campagne à
    # prévisualiser géré dans portail_client.py) — pour pouvoir tester
    # directement depuis l'interface ce que voit un compte client.
    page_portail_client = st.Page("app_pages/portail_client.py", title="Portail Client (aperçu)", icon="📊")
    pg = st.navigation(
        [
            page_sourcing, page_gestion, page_administration, page_deliverabilite,
            page_demandes_devis, page_suivi, page_suppression_rgpd, page_portail_client,
            # page_finances,  # voir commentaire ci-dessus — retrait temporaire
        ]
    )
else:
    page_client = st.Page("app_pages/portail_client.py", title="Mon espace", icon="📊")
    pg = st.navigation([page_client])

pg.run()
