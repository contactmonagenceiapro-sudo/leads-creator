"""
Pages PUBLIQUES du dashboard — accessibles SANS connexion, contrairement à
toutes les autres pages (voir app.py, qui court-circuite exiger_connexion()
pour ces vues via un paramètre d'URL ?vue=...). Remplacent les anciennes
routes HTML de api/main.py :
- GET/POST /presentation/{lead_id}  -> ?vue=presentation&lead_id=...
- GET/POST /intake/{lead_id}        -> ?vue=intake&lead_id=...
- GET/POST /devis/{client_slug}     -> ?vue=devis&slug=...

Contenu et logique métier repris tels quels (mêmes champs de formulaire,
même comportement), seulement traduits en composants Streamlit natifs au
lieu de HTML/CSS brut.

afficher_signature (?vue=signature&token=...) est différente : nouvelle
page (pas une reprise d'une ancienne route FastAPI), ajoutée le 10/08 pour
la signature électronique interne (voir signature_interne.py) — résout par
token, jamais par lead_id, contrairement aux autres vues ci-dessus.

afficher_demande_devis (?vue=demande_devis, PAS de slug requis) est
également nouvelle — ajoutée le 18/08 (voir sql/init_demandes_devis_particuliers_generique.sql) :
formulaire public GÉNÉRIQUE de demande de devis, indépendant de toute
campagne B2B, pensé comme future source de signal de besoin exprimé pour
les leads B2C vendus aux artisans (aucun mécanisme de mise en relation
n'est branché à ce stade — cette page ne fait qu'enregistrer la demande).
Coexiste avec afficher_devis (scopée à une campagne B2B nommée via son
slug) sans le remplacer : les deux écrivent dans la même table, avec ou
sans client_final connu à la soumission.
"""

import os
from pathlib import Path

import streamlit as st

from contrats_signature import envoyer_contrat_signature, generer_pdf_devis, libelle_prestation, normaliser_type_offre
from alertes import alerter_discord
from generation_contrats import (
    FORMULES_ABONNEMENT,
    TYPE_OFFRE_ABONNEMENT,
    TYPE_OFFRE_UNITE,
    formule_abonnement_par_id,
    fourchette_prix_unite_eur,
)
from signature_interne import enregistrer_signature, envoyer_contrat_signature_interne, get_contrat_par_token
from supabase_client import supabase

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")

# Lien relatif (même app) vers la page de politique de confidentialité
# ci-dessous — réutilisé partout où une mention RGPD est requise sur cette
# page (intake, signature) : lead_worker.py::MENTION_DESINSCRIPTION (email
# en texte brut, pas de lien cliquable côté serveur) et
# landing/artisan-inscription.html (page statique hors de cette app,
# nécessite l'URL absolue de PUBLIC_DASHBOARD_URL en dur) ont chacun leur
# propre construction du même lien.
LIEN_CONFIDENTIALITE = "?vue=confidentialite"
LIBELLE_LIEN_CONFIDENTIALITE = "politique de confidentialité"

# Racine du dépôt : pages_publiques.py vit dans dashboard/, le fichier
# source de la politique à la racine (politique-confidentialite.md) — même
# convention que RACINE_REPO dans data_access.py/process_runner.py.
CHEMIN_POLITIQUE_CONFIDENTIALITE = Path(__file__).resolve().parent.parent / "politique-confidentialite.md"
# "interne" (défaut) = signature électronique simple maison (signature_interne.py) ;
# "yousign" = repasse sur le prestataire de confiance Yousign (contrats_signature.py),
# gardé disponible pour plus tard (budget/volume suffisant, ou contrat à enjeu plus
# important) — voir diagnostic + plan validés le 10/08.
SIGNATURE_PROVIDER = os.getenv("SIGNATURE_PROVIDER_PAR_DEFAUT", "interne")

# Tout statut atteint APRÈS une soumission d'intake réussie (voir
# envoyer_contrat_signature / marquer_contrat_signe / marquer_contrat_paye
# dans contrats_signature.py / data_access.py) — sert de garde-fou CÔTÉ
# SERVEUR contre une resoumission (st.session_state seul ne suffit pas : il
# est propre à une session navigateur, un lien réouvert ailleurs le
# contournerait entièrement).
STATUTS_INTAKE_DEJA_ENVOYE = {
    "intake_recu", "contrat_envoye", "contrat_signe", "lien_paiement_envoye", "paye",
}

# Libellés dupliqués depuis scraper_batiment.py::SECTEURS_NAF (mêmes 6
# corps de métier, sans les codes NAF, inutiles ici) plutôt qu'importés :
# ce module est public/léger (formulaire visité par des particuliers sans
# connexion), scraper_batiment.py est un script de scraping lourd (bs4,
# retry HTTP, load_dotenv() à l'import...) qu'il serait malvenu de charger
# juste pour 6 chaînes de caractères — même principe de duplication
# assumée que scorer_leads.py::SCORES_TRANCHE_EFFECTIF (les segments ne
# doivent jamais dépendre l'un de l'autre au niveau code). Toute évolution
# de la liste côté scraping doit être répercutée ici à la main, et
# inversement — les deux forment ensemble le vocabulaire contrôlé
# "corps de métier" du projet (contrainte CHECK identique côté SQL, voir
# sql/init_demandes_devis_particuliers_generique.sql).
SECTEURS_METIER = [
    "Bâtiment - Plâtrerie",
    "Bâtiment - Électricité",
    "Bâtiment - Isolation / Rénovation Énergétique",
    "Bâtiment - Gros Œuvre",
    "Bâtiment - Second Œuvre / Rénovation",
    "Bâtiment - Plomberie / Chauffage",
]

# Première option de chaque selectbox corps de métier ci-dessous — jamais
# une valeur acceptée (voir _corps_metier_saisi), seulement un texte
# d'invite : st.selectbox impose un index par défaut (pas d'équivalent
# universel à un <select> HTML sans option pré-sélectionnée sur toutes les
# versions de Streamlit), donc on ajoute explicitement un placeholder en
# première position plutôt que de risquer une pré-sélection silencieuse du
# premier VRAI corps de métier de la liste.
_PLACEHOLDER_CORPS_METIER = "— Sélectionnez un corps de métier —"


def _champ_corps_metier(cle: str) -> str | None:
    """Selectbox corps de métier partagée par afficher_devis() et
    afficher_demande_devis() — un seul widget à faire évoluer si la liste
    SECTEURS_METIER change. Retourne None tant que le placeholder est
    sélectionné."""
    choix = st.selectbox(
        "Corps de métier recherché",
        options=[_PLACEHOLDER_CORPS_METIER] + SECTEURS_METIER,
        key=cle,
    )
    return None if choix == _PLACEHOLDER_CORPS_METIER else choix


def _get_lead(lead_id: str) -> dict | None:
    """None aussi bien si le lead n'existe pas que si la lecture échoue
    (ex: lead_id malformé — pas un UUID valide — ou Supabase injoignable) :
    ces pages sont publiques et sans authentification, une erreur ne doit
    jamais remonter un traceback brut à un visiteur externe."""
    try:
        leads = supabase.table("leads").select("*").eq("id", lead_id).execute().data
    except Exception:
        return None
    return leads[0] if leads else None


def _get_campagne_active_par_slug(slug: str) -> str | None:
    """Même principe que _get_lead() : toute erreur devient un simple
    'page introuvable', jamais un crash visible publiquement."""
    try:
        resultats = (
            supabase.table("campagnes").select("nom_client")
            .eq("slug", slug).eq("statut", "active").limit(1).execute().data
        )
    except Exception:
        return None
    return resultats[0]["nom_client"] if resultats else None


def afficher_presentation(lead_id: str | None) -> None:
    """Page publique consultée directement par l'artisan depuis son email —
    présente l'offre réelle : apport de leads qualifiés (demandes de devis
    de particuliers/professionnels dans sa zone d'activité), pas une
    refonte de site vitrine — voir lead_worker.py::generer_pitch(), déjà
    aligné sur ce modèle, et le diagnostic du 2026-08-14 qui a identifié
    que cette page (et tout le tunnel en aval) ne l'était pas."""
    if not lead_id:
        st.error("Lien invalide : identifiant manquant.")
        return

    lead = _get_lead(lead_id)
    if not lead:
        st.error("Présentation introuvable.")
        return

    company = lead.get("company") or "votre entreprise"
    pitch = lead.get("pitch_commercial") or lead.get("weakness") or ""

    st.title(f"{AGENCY_NAME} — Des clients qualifiés, sans prospection")
    with st.container(border=True):
        st.markdown(f"Bonjour **{company}**,")
        st.write(pitch)

    mini, maxi = fourchette_prix_unite_eur()
    formules_texte = " · ".join(f"{f['label']} : {f['prix_eur']:.0f} € TTC/mois" for f in FORMULES_ABONNEMENT)

    st.subheader("Ce qui est inclus, sans aucune action technique de votre part :")
    with st.container(border=True):
        st.markdown(
            "✅ Des demandes de devis réelles, de particuliers/professionnels intéressés "
            "par vos prestations  \n"
            "✅ Ciblées sur votre corps de métier et votre zone d'intervention  \n"
            f"✅ Au choix : à l'unité ({mini:.0f} à {maxi:.0f} € TTC/lead, selon sa "
            f"qualité) ou en abonnement mensuel ({formules_texte})  \n"
            "✅ Aucun appel, aucune compétence technique requise de votre côté"
        )

    st.link_button("Démarrer (2 minutes) →", f"?vue=intake&lead_id={lead_id}", type="primary")
    st.caption(f"{AGENCY_NAME} — réponse par email uniquement.")


def afficher_intake(lead_id: str | None) -> None:
    """Formulaire asynchrone de qualification du Client (aucun appel : tout
    est déclaratif, par écrit) — corps de métier, zone d'intervention et
    choix de la formule (à l'unité ou abonnement), pour lancer l'envoi de
    leads qualifiés. Déclenche la génération du devis PDF + l'envoi en
    signature électronique dès la soumission (interne par défaut, Yousign
    en option — voir SIGNATURE_PROVIDER)."""
    if not lead_id:
        st.error("Lien invalide : identifiant manquant.")
        return

    lead = _get_lead(lead_id)
    if not lead:
        st.error("Formulaire introuvable.")
        return

    cle_envoye = f"intake_envoye_{lead_id}"
    deja_recu = lead.get("status") in STATUTS_INTAKE_DEJA_ENVOYE
    if st.session_state.get(cle_envoye) or deja_recu:
        st.title("Merci, c'est enregistré !")
        with st.container(border=True):
            st.write(
                "Nous avons bien reçu vos informations. On prépare votre devis et "
                "on revient vers vous par email dès qu'il y a du nouveau — toujours sans appel."
            )
        return

    company = lead.get("company") or "votre entreprise"
    st.title(f"Démarrons, {company}")
    st.write("Ce formulaire remplace tout appel téléphonique : remplissez-le à votre rythme.")

    # Le choix de formule est VOLONTAIREMENT en dehors du st.form ci-dessous
    # (contrairement aux autres champs) : un widget dans un formulaire ne
    # déclenche aucun rerun avant la soumission, donc il serait impossible
    # d'afficher conditionnellement les 3 formules d'abonnement (ou la
    # fourchette à l'unité) tant que le Client n'a pas encore choisi entre
    # les deux — ces deux widgets, eux, doivent réagir immédiatement.
    mini, maxi = fourchette_prix_unite_eur()
    type_offre = st.radio(
        "Formule souhaitée",
        options=[TYPE_OFFRE_UNITE, TYPE_OFFRE_ABONNEMENT],
        format_func=lambda v: (
            f"À l'unité — de {mini:.0f} à {maxi:.0f} € TTC par lead livré, selon sa qualité"
            if v == TYPE_OFFRE_UNITE
            else "Abonnement mensuel — volume au choix"
        ),
        key=f"type_offre_{lead_id}",
    )

    formule_abonnement = None
    if type_offre == TYPE_OFFRE_UNITE:
        st.caption(
            f"Le prix dépend de la qualité de chaque lead livré (entre {mini:.0f} € et "
            f"{maxi:.0f} € TTC) ; vous ne payez que ce que vous recevez, facturé à chaque livraison."
        )
    else:
        formule_abonnement = st.radio(
            "Choisissez votre volume mensuel",
            options=[f["id"] for f in FORMULES_ABONNEMENT],
            format_func=lambda fid: (
                lambda f: f"{f['label']} — {f['prix_eur']:.0f} € TTC/mois"
            )(formule_abonnement_par_id(fid)),
            key=f"formule_abonnement_{lead_id}",
        )

    # max_chars sur chaque champ : ce formulaire est public et non authentifié,
    # rien n'empêche un abus (texte massif envoyé en boucle) sans une limite
    # basique — appliquée au niveau du widget, la plus simple à maintenir.
    with st.form("form_intake_public"):
        description = st.text_area(
            "Décrivez votre activité en quelques phrases", height=120, max_chars=3000,
        )
        corps_metier = st.text_input(
            "Votre corps de métier", placeholder="Ex : Plombier, Électricien, Menuisier...", max_chars=200,
        )
        zone_activite = st.text_input(
            "Zone d'intervention (villes/rayon)", placeholder="Ex : Reims et 30km alentour", max_chars=300,
        )
        st.caption(f"En envoyant ce formulaire, vous acceptez notre [{LIBELLE_LIEN_CONFIDENTIALITE}]({LIEN_CONFIDENTIALITE}).")
        envoyer = st.form_submit_button("Envoyer", type="primary", use_container_width=True)

    if not envoyer:
        return
    if not description.strip():
        st.warning("Merci de décrire votre activité avant d'envoyer.")
        return
    if not corps_metier.strip():
        st.warning("Merci de renseigner votre corps de métier avant d'envoyer.")
        return

    payload = {
        "lead_id": lead_id,
        "description": description,
        "corps_metier": corps_metier,
        "zone_activite": zone_activite,
        "type_offre": type_offre,
        "formule_abonnement": formule_abonnement,
    }
    try:
        supabase.table("intake_responses").insert(payload).execute()
        supabase.table("leads").update({"status": "intake_recu"}).eq("id", lead_id).execute()
    except Exception:
        st.error("Erreur lors de l'enregistrement, réessayez plus tard.")
        return

    with st.spinner("Génération de votre devis et envoi en signature électronique..."):
        if SIGNATURE_PROVIDER == "yousign":
            envoyer_contrat_signature(lead, payload)
        else:
            envoyer_contrat_signature_interne(lead, payload)

    st.session_state[cle_envoye] = True
    st.rerun()


def _get_intake(lead_id: str) -> dict:
    """Même principe que _get_lead() : jamais d'exception visible sur une
    page publique."""
    try:
        rows = (
            supabase.table("intake_responses").select("*")
            .eq("lead_id", lead_id).order("created_at", desc=True).limit(1).execute().data
        )
    except Exception:
        return {}
    return rows[0] if rows else {}


def afficher_signature(token: str | None) -> None:
    """Page publique de signature électronique interne (lien envoyé par
    email à la place d'une demande Yousign, voir
    signature_interne.envoyer_contrat_signature_interne). Résout STRICTEMENT
    par token — jamais par lead_id/contract_id — le token est le seul
    contrôle d'accès à cette page."""
    if not token:
        st.error("Lien invalide : jeton manquant.")
        return

    contrat = get_contrat_par_token(token)
    if not contrat:
        st.error("Page introuvable.")
        return

    cle_signe = f"signature_ok_{token}"
    deja_signe = contrat.get("yousign_status") == "signe"
    if st.session_state.get(cle_signe) or deja_signe:
        st.title("Signé ✅")
        with st.container(border=True):
            st.write(
                "Merci, ce contrat est signé ! Vous avez reçu (ou allez recevoir) la "
                "confirmation par email avec le récapitulatif complet."
            )
        return

    lead = _get_lead(contrat["lead_id"])
    if not lead:
        st.error("Contrat introuvable.")
        return

    intake = _get_intake(contrat["lead_id"])
    pdf_bytes = generer_pdf_devis(lead, intake)
    type_offre = normaliser_type_offre(contrat.get("type_offre") or intake.get("type_offre"))

    if type_offre == TYPE_OFFRE_ABONNEMENT:
        formule = formule_abonnement_par_id(contrat.get("formule_abonnement") or intake.get("formule_abonnement"))
        libelle = libelle_prestation(type_offre, formule_abonnement=formule["id"])
        ligne_montant = f"**Montant : {formule['prix_eur']:.2f} € TTC / mois ({formule['label']})**"
    else:
        libelle = libelle_prestation(type_offre)
        mini, maxi = fourchette_prix_unite_eur()
        ligne_montant = f"**Montant : entre {mini:.2f} € et {maxi:.2f} € TTC / lead, selon la qualité du lead livré**"

    st.title(f"Votre devis — {lead.get('company') or ''}")
    with st.container(border=True):
        st.write(f"Corps de métier : {intake.get('corps_metier', '') or '—'}")
        st.write(f"Zone d'intervention : {intake.get('zone_activite', '') or '—'}")
        st.write(f"Description de l'activité : {intake.get('description', '') or '—'}")
        st.write(f"Prestation : {libelle}")
        st.markdown(ligne_montant)
    st.download_button(
        "⬇️ Télécharger le devis en PDF", data=pdf_bytes,
        file_name=f"devis_{contrat['id']}.pdf", mime="application/pdf",
    )

    st.divider()
    st.subheader("Validation du devis")
    st.caption(
        "En tapant votre nom et en cliquant ci-dessous, vous acceptez les conditions du "
        "devis ci-dessus. Ceci constitue une signature électronique simple au sens de "
        "l'article 1367 du Code civil (nom, date, heure et adresse IP sont enregistrés — "
        f"voir notre [{LIBELLE_LIEN_CONFIDENTIALITE}]({LIEN_CONFIDENTIALITE}))."
    )
    nom_saisi = st.text_input("Votre nom complet, pour valider")
    if st.button("✅ J'accepte les conditions", type="primary", use_container_width=True):
        succes, message_erreur = enregistrer_signature(contrat, nom_saisi)
        if not succes:
            st.warning(message_erreur)
        else:
            st.session_state[cle_signe] = True
            st.rerun()


def afficher_devis(slug: str | None) -> None:
    """Formulaire public de demande de devis pour un particulier — seul
    canal par lequel un maître d'ouvrage privé entre dans le système. Le
    slug doit correspondre à une campagne ACTIVE, jamais une URL arbitraire."""
    if not slug:
        st.error("Page introuvable.")
        return

    client_final = _get_campagne_active_par_slug(slug)
    if not client_final:
        st.error("Page introuvable.")
        return

    cle_envoye = f"devis_envoye_{slug}"
    if st.session_state.get(cle_envoye):
        st.title("Merci pour votre demande !")
        with st.container(border=True):
            st.write("Nous avons bien reçu votre demande et revenons vers vous très rapidement.")
        return

    st.title(f"Demande de devis — {client_final}")
    st.write("Décrivez votre projet, nous revenons vers vous rapidement.")

    corps_metier = _champ_corps_metier(f"corps_metier_devis_{slug}")

    with st.form("form_devis_public"):
        nom = st.text_input("Votre nom", max_chars=200)
        email = st.text_input("E-mail", max_chars=200)
        telephone = st.text_input("Téléphone", placeholder="0X XX XX XX XX", max_chars=50)
        commune = st.text_input("Commune du projet", max_chars=200)
        budget_estime = st.text_input("Budget estimé (optionnel)", max_chars=100)
        message = st.text_area("Votre message", height=120, max_chars=3000)
        consentement = st.checkbox(f"J'accepte d'être recontacté(e) par {client_final} au sujet de ma demande.")
        envoyer = st.form_submit_button("Envoyer ma demande", type="primary", use_container_width=True)

    if envoyer:
        if not nom.strip():
            st.warning("Merci de renseigner votre nom.")
        elif not corps_metier:
            st.warning("Merci de sélectionner le corps de métier recherché.")
        elif not consentement:
            st.warning("Merci de cocher la case de consentement pour continuer.")
        else:
            payload = {
                "client_final": client_final,
                "nom": nom,
                "email": email or None,
                "telephone": telephone or None,
                "corps_metier": corps_metier,
                "commune": commune or None,
                "budget_estime": budget_estime or None,
                "message": message or None,
                "consentement": True,
            }
            try:
                supabase.table("demandes_devis_particuliers").insert(payload).execute()
            except Exception:
                st.error("Erreur lors de l'enregistrement, réessayez plus tard.")
            else:
                # Lead entrant chaud (le particulier vient de faire la démarche
                # lui-même) : alerte immédiate, contrairement aux prospects
                # sortants qui suivent la veille des réponses e-mail habituelle.
                alerter_discord(f"🔥 Nouvelle demande de devis entrante pour {client_final} : {nom}")
                st.session_state[cle_envoye] = True
                st.rerun()

    st.caption("Vos informations ne sont utilisées que pour traiter cette demande de devis.")


def afficher_demande_devis() -> None:
    """Formulaire public GÉNÉRIQUE de demande de devis (?vue=demande_devis,
    sans slug de campagne) — contrairement à afficher_devis() ci-dessus, le
    destinataire n'est PAS connu à la soumission : client_final reste NULL
    (voir sql/init_demandes_devis_particuliers_generique.sql). Diagnostic
    exploratoire du 18/08/2026 : source à venir de signal de besoin exprimé
    pour les leads B2C vendus aux artisans.

    AUCUN mécanisme de mise en relation n'est branché ici — cette page ne
    fait qu'enregistrer la demande avec le consentement adapté (voir
    consentement ci-dessous, qui autorise explicitement UNE MISE EN RELATION
    AVEC PLUSIEURS artisans, contrairement au consentement scopé à un seul
    client_final nommé de afficher_devis()). Le rapprochement réel avec des
    artisans clients est une étape ultérieure distincte, volontairement pas
    encore construite."""
    cle_envoye = "demande_devis_envoyee"
    if st.session_state.get(cle_envoye):
        st.title("Merci pour votre demande !")
        with st.container(border=True):
            st.write(
                "Nous avons bien reçu votre demande de devis et revenons vers vous "
                "très rapidement avec un ou plusieurs artisans qualifiés."
            )
        return

    st.title(f"Demande de devis — {AGENCY_NAME}")
    st.write("Décrivez votre projet, nous vous mettons en relation avec des artisans qualifiés près de chez vous.")

    corps_metier = _champ_corps_metier("corps_metier_demande_devis")

    with st.form("form_demande_devis_public"):
        commune = st.text_input("Commune ou code postal du projet", max_chars=200)
        description = st.text_area(
            "Décrivez votre besoin", placeholder="Ex : rénovation de salle de bain, extension de 20m²...",
            height=120, max_chars=1000,
        )
        nom = st.text_input("Votre nom", max_chars=200)
        email = st.text_input("E-mail", max_chars=200)
        telephone = st.text_input("Téléphone", placeholder="0X XX XX XX XX", max_chars=50)
        consentement = st.checkbox(
            "J'accepte que ma demande soit transmise à un ou plusieurs artisans qualifiés "
            "correspondant à mon besoin, dans le cadre de la mise en relation proposée par "
            f"{AGENCY_NAME}."
        )
        st.caption(f"Voir notre [{LIBELLE_LIEN_CONFIDENTIALITE}]({LIEN_CONFIDENTIALITE}).")
        envoyer = st.form_submit_button("Envoyer ma demande", type="primary", use_container_width=True)

    if not envoyer:
        return
    if not nom.strip():
        st.warning("Merci de renseigner votre nom.")
        return
    if not corps_metier:
        st.warning("Merci de sélectionner le corps de métier recherché.")
        return
    if not email.strip() and not telephone.strip():
        st.warning("Merci de renseigner au moins un moyen de vous recontacter (e-mail ou téléphone).")
        return
    if not consentement:
        st.warning("Merci de cocher la case de consentement pour continuer.")
        return

    payload = {
        "client_final": None,
        "nom": nom,
        "email": email or None,
        "telephone": telephone or None,
        "corps_metier": corps_metier,
        "commune": commune or None,
        "message": description or None,
        "consentement": True,
    }
    try:
        supabase.table("demandes_devis_particuliers").insert(payload).execute()
    except Exception:
        st.error("Erreur lors de l'enregistrement, réessayez plus tard.")
    else:
        alerter_discord(f"🔥 Nouvelle demande de devis générique ({corps_metier}, {commune or '?'}) : {nom}")
        st.session_state[cle_envoye] = True
        st.rerun()


def afficher_confidentialite() -> None:
    """Politique de confidentialité — page publique statique, affichée
    telle quelle depuis politique-confidentialite.md (racine du dépôt,
    source unique : toute mise à jour du contenu se fait dans ce seul
    fichier, jamais dans le code). Liée depuis l'intake, la signature, le
    pitch email (lead_worker.py) et le portail artisan
    (landing/artisan-inscription.html)."""
    st.title("Politique de confidentialité")
    try:
        contenu = CHEMIN_POLITIQUE_CONFIDENTIALITE.read_text(encoding="utf-8")
    except OSError:
        st.error(
            "Politique de confidentialité momentanément indisponible — "
            "contactez-nous directement à expertisedigitale@zohomail.eu."
        )
        return
    st.markdown(contenu)
