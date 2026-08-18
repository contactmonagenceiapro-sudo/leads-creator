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

afficher_accueil / afficher_comment_ca_marche / afficher_tarifs /
afficher_a_propos (?vue=accueil|comment_ca_marche|tarifs|a_propos) sont le
site vitrine ajouté le 19/08 : présentation de l'offre avant même l'achat
d'un nom de domaine (l'URL Streamlit suffit en attendant), texte repris tel
quel du fichier Contenu_Site_Vitrine.md (racine du dépôt) — seule la mise
en forme (composants Streamlit natifs) a été adaptée, jamais le texte
lui-même. Partagent une navigation et un footer communs (_navigation_publique
/ _footer_publique ci-dessous) avec afficher_demande_devis, dont le
titre/texte d'intro a été aligné sur ce même fichier de contenu à cette
occasion. Volontairement PAS de lien vers ces 4 nouvelles vues ni vers
demande_devis depuis les pages transactionnelles plus anciennes
(presentation/intake/signature/devis-slug/confidentialite) : celles-ci
restent des flux dédiés à un lead/token/campagne précis, pas des pages de
navigation libre pour un visiteur quelconque.
"""

import os
from pathlib import Path

import streamlit as st

from contrats_signature import envoyer_contrat_signature, generer_pdf_devis, libelle_prestation, normaliser_type_offre
from alertes import alerter_discord
from generation_contrats import (
    FORMULES_ABONNEMENT,
    GRILLE_PRIX_PAR_TYPE_ACTEUR_EUR,
    LIBELLE_PALIER,
    LIBELLE_TYPE_ACTEUR_GRILLE_EUR,
    PALIER_BASIQUE,
    PALIER_PREMIUM,
    PALIER_STANDARD,
    PRIX_PAR_PALIER_EUR,
    TYPE_OFFRE_ABONNEMENT,
    TYPE_OFFRE_UNITE,
    formule_abonnement_par_id,
    fourchette_prix_unite_eur,
)
from scraper_batiment import SECTEURS_NAF, VILLES_CIBLES
from signature_interne import enregistrer_signature, envoyer_contrat_signature_interne, get_contrat_par_token
from supabase_client import supabase

AGENCY_NAME = os.getenv("AGENCY_NAME", "Expertise Digitale")
# Adresse de contact publique (footer vitrine, page de confidentialité) —
# distincte de generation_contrats.DEFAULTS_AGENCE["email"]/AGENCY_EMAIL
# (dashboard/signature_interne.py), qui sert aux emails SORTANTS envoyés par
# l'agence (contrats, archives) : celle-ci est l'adresse ENTRANTE affichée
# aux visiteurs du site vitrine, pas forcément la même selon la config Zoho.
AGENCY_CONTACT_EMAIL = os.getenv("AGENCY_CONTACT_EMAIL", "expertisedigitale@zohomail.eu")

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

# Libellés importés depuis scraper_batiment.py::SECTEURS_NAF (mêmes 6
# corps de métier, sans les codes NAF, inutiles ici) — SOURCE UNIQUE,
# jamais dupliquée : depuis que ce vocabulaire contraint deux tables par
# CHECK constraint (demandes_devis_particuliers.corps_metier ET
# intake_responses.corps_metier, voir livraison_devis.py qui les
# rapproche), une divergence entre deux copies romprait silencieusement le
# rapprochement. Même précédent déjà en place côté admin
# (dashboard/data_access.py importe VILLES_LYON/VILLES_GRAND_EST du même
# module) : scraper_batiment.py n'a aucun effet de bord réseau à l'import
# (seulement des constantes + load_dotenv()), l'importer ici n'a donc pas
# le coût qu'aurait un import de outbound_chantiers/ (B2B, segment
# volontairement indépendant, voir scorer_leads.py::SCORES_TRANCHE_EFFECTIF
# pour ce cas de duplication assumée, différent de celui-ci).
SECTEURS_METIER = [libelle for _, libelle in SECTEURS_NAF]

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


# ---------------------------------------------------------------------
# Site vitrine public — navigation et footer communs à afficher_accueil,
# afficher_comment_ca_marche, afficher_tarifs, afficher_a_propos et
# afficher_demande_devis (voir docstring de module). Volontairement de
# simples composants Streamlit natifs (st.link_button, st.columns) plutôt
# que du HTML/CSS injecté via unsafe_allow_html : aucune page de ce fichier
# n'en utilise, on ne casse pas cette convention pour la vitrine.
# ---------------------------------------------------------------------

_NAVIGATION_VITRINE = [
    ("accueil", "🏠 Accueil"),
    ("comment_ca_marche", "🛠️ Comment ça marche"),
    ("tarifs", "💶 Tarifs"),
    ("demande_devis", "📝 Demander un devis"),
    ("a_propos", "ℹ️ À propos"),
]


def _navigation_publique(vue_active: str) -> None:
    """Menu horizontal commun aux pages vitrine — un st.button désactivé pour
    la page courante (pas de lien mort cliquable), un st.link_button vers
    `?vue=...` pour les autres, même pattern que les liens `?vue=intake&...`
    déjà utilisés ailleurs dans ce fichier."""
    colonnes = st.columns(len(_NAVIGATION_VITRINE))
    for colonne, (vue, libelle) in zip(colonnes, _NAVIGATION_VITRINE):
        with colonne:
            if vue == vue_active:
                st.button(libelle, disabled=True, use_container_width=True, key=f"nav_actuelle_{vue_active}")
            else:
                st.link_button(libelle, f"?vue={vue}", use_container_width=True)
    st.divider()


def _footer_publique() -> None:
    """Pied de page commun — accroche courte, liens légaux, contact (voir
    Contenu_Site_Vitrine.md::Footer). Mentions légales/CGV pas encore
    rédigées : on ne fabrique pas de fausses pages vides pour ces deux
    liens, seule une adresse de contact est fournie en attendant (option
    explicitement laissée ouverte par le fichier de contenu)."""
    st.divider()
    col_accroche, col_legal, col_contact = st.columns(3)
    with col_accroche:
        st.markdown(f"**{AGENCY_NAME}**")
        st.caption("Mise en relation qualifiée entre professionnels du bâtiment et particuliers.")
    with col_legal:
        st.markdown("**Liens légaux**")
        st.link_button("Politique de confidentialité", LIEN_CONFIDENTIALITE, use_container_width=True)
        st.caption(
            "Mentions légales et CGV : à venir. Pour toute demande liée à vos données "
            f"(suppression, accès), écrivez-nous à {AGENCY_CONTACT_EMAIL}."
        )
    with col_contact:
        st.markdown("**Contact**")
        st.caption(AGENCY_CONTACT_EMAIL)


def afficher_accueil() -> None:
    """Page d'accueil du site vitrine (?vue=accueil) — texte repris tel quel
    de Contenu_Site_Vitrine.md::"Page d'accueil". Le bouton "professionnel"
    pointe vers l'ancre #pour-les-professionnels plus bas SUR CETTE MÊME
    page (voir anchor= sur le st.header ci-dessous) ; le bouton
    "particulier" pointe directement vers le formulaire ?vue=demande_devis,
    conformément au fichier de contenu."""
    _navigation_publique("accueil")

    st.title("Des clients qualifiés pour votre activité, sans effort de prospection.")
    st.subheader(
        "Expertise Digitale trouve et vous met en relation avec des particuliers et "
        "professionnels qui ont un vrai besoin, dans votre zone d'intervention."
    )

    col_pro, col_particulier = st.columns(2)
    with col_pro:
        st.link_button(
            "Je suis artisan / professionnel du bâtiment",
            "#pour-les-professionnels", use_container_width=True,
        )
    with col_particulier:
        st.link_button(
            "J'ai un projet de travaux", "?vue=demande_devis",
            type="primary", use_container_width=True,
        )

    st.divider()

    st.header("Vous recevez des demandes réelles, vous n'avez qu'à répondre.", anchor="pour-les-professionnels")
    st.write(
        "Nous identifions et qualifions des prospects dans votre secteur d'activité et "
        "votre zone géographique — grâce à des données publiques d'activité de chantiers, "
        "et à de vraies demandes de devis exprimées par des particuliers. Vous ne payez "
        "que pour des contacts vérifiés."
    )
    col_qualifie, col_flexible, col_transparent = st.columns(3)
    with col_qualifie:
        st.markdown("**Qualifié**")
        st.caption("chaque contact est vérifié (email valide, zone conforme) avant de vous être livré.")
    with col_flexible:
        st.markdown("**Flexible**")
        st.caption("testez à l'unité, passez à l'abonnement si ça vous convient.")
    with col_transparent:
        st.markdown("**Transparent**")
        st.caption("grille tarifaire claire, remplacement garanti si un contact s'avère invalide.")

    col_cta_tarifs, col_cta_contact = st.columns(2)
    with col_cta_tarifs:
        st.link_button("Voir nos tarifs", "?vue=tarifs", use_container_width=True)
    with col_cta_contact:
        st.link_button("Nous contacter", f"mailto:{AGENCY_CONTACT_EMAIL}", use_container_width=True)

    st.divider()

    st.header("Un projet de travaux ? Trouvez le bon professionnel près de chez vous.")
    st.write(
        "Décrivez votre besoin en 2 minutes, nous vous mettons en relation avec un "
        "professionnel qualifié de votre secteur. Gratuit, sans engagement."
    )
    st.link_button("Demander un devis", "?vue=demande_devis", type="primary", use_container_width=True)

    _footer_publique()


def _etape_parcours(numero: int, titre: str, texte: str) -> None:
    """Une étape d'un parcours (pro ou particulier) sur la page "Comment ça
    marche" — liste numérotée stylée en cartes plutôt qu'en texte brut, voir
    Contenu_Site_Vitrine.md::"Page Comment ça marche"."""
    with st.container(border=True):
        st.markdown(f"**{numero}. {titre}**")
        st.write(texte)


def afficher_comment_ca_marche() -> None:
    """Page "Comment ça marche" (?vue=comment_ca_marche) — les deux parcours
    (professionnels et particuliers), texte repris tel quel de
    Contenu_Site_Vitrine.md::"Page 'Comment ça marche'"."""
    _navigation_publique("comment_ca_marche")

    st.title("Comment ça fonctionne pour vous")
    st.caption("Pour les professionnels")
    _etape_parcours(
        1, "On identifie les bons contacts.",
        "À partir de données publiques d'activité de chantiers et de vraies demandes "
        "entrantes, on repère les prospects pertinents pour votre métier et votre zone.",
    )
    _etape_parcours(
        2, "On qualifie avant de vous transmettre.",
        "Email et coordonnées vérifiés, besoin confirmé pour les demandes de particuliers.",
    )
    _etape_parcours(
        3, "Vous choisissez votre formule.",
        "À l'unité pour tester, ou en abonnement mensuel pour un flux régulier.",
    )
    _etape_parcours(
        4, "Vous recevez le contact, vous le contactez.",
        "À vous de transformer l'échange en rendez-vous ou en devis.",
    )
    _etape_parcours(
        5, "Un contact invalide ? On le remplace.",
        "Sous 7 jours, remplacement ou avoir, sans discussion inutile.",
    )

    st.divider()

    st.title("Comment obtenir un devis")
    st.caption("Pour les particuliers")
    _etape_parcours(
        1, "Décrivez votre projet.",
        "Type de travaux, votre commune, quelques mots sur votre besoin.",
    )
    _etape_parcours(
        2, "On vous met en relation.",
        "Un professionnel qualifié de votre secteur reçoit votre demande.",
    )
    _etape_parcours(
        3, "Il vous recontacte directement.",
        "Par téléphone ou email, pour discuter de votre projet et vous proposer un devis.",
    )
    st.info(
        "Vos coordonnées ne sont transmises qu'aux professionnels correspondant à votre "
        "demande, jamais revendues à des fins publicitaires."
    )

    _footer_publique()


def afficher_tarifs() -> None:
    """Page "Tarifs" (?vue=tarifs) — les deux grilles (B2C à l'unité + 3
    formules d'abonnement, B2B par type d'acteur), texte repris tel quel de
    Contenu_Site_Vitrine.md::"Page Tarifs". Valeurs affichées TOUJOURS lues
    depuis generation_contrats.py (PRIX_PAR_PALIER_EUR, FORMULES_ABONNEMENT,
    GRILLE_PRIX_PAR_TYPE_ACTEUR_EUR), jamais recopiées en dur : cette page
    reste à jour automatiquement si ces constantes changent."""
    _navigation_publique("tarifs")

    st.title("Nos formules")
    st.caption("Pour les artisans")

    mini, maxi = fourchette_prix_unite_eur()
    st.subheader("À l'unité")
    st.write(
        f"Payez uniquement pour les contacts que vous recevez. Le prix varie selon la "
        f"qualité du lead (de {mini:.0f}€ à {maxi:.0f}€), avec un remplacement garanti "
        f"en cas de contact invalide."
    )
    st.dataframe(
        [
            {"Qualité du lead": LIBELLE_PALIER[palier], "Prix TTC / lead": f"{PRIX_PAR_PALIER_EUR[palier]:.0f} €"}
            for palier in (PALIER_BASIQUE, PALIER_STANDARD, PALIER_PREMIUM)
        ],
        hide_index=True, use_container_width=True,
    )

    st.subheader("Abonnement")
    st.write("Un flux régulier de leads qualifiés, à prix dégressif selon le volume.")
    st.dataframe(
        [
            {
                "Formule": formule["id"].capitalize(),
                "Volume": f"{formule['volume']} leads",
                "Prix/mois": f"{formule['prix_eur']:.0f} €",
            }
            for formule in FORMULES_ABONNEMENT
        ],
        hide_index=True, use_container_width=True,
    )

    st.divider()

    st.title("Grille tarifaire")
    st.caption("Pour les architectes, promoteurs, maîtres d'œuvre")
    st.dataframe(
        [
            {
                "Type d'acteur": LIBELLE_TYPE_ACTEUR_GRILLE_EUR.get(cle, cle),
                "Prix par lead": f"{prix:.0f} € HT",
            }
            for cle, prix in GRILLE_PRIX_PAR_TYPE_ACTEUR_EUR.items()
        ],
        hide_index=True, use_container_width=True,
    )
    st.caption(
        "Tarifs valables pour toute commande, avec la même garantie de remplacement sous "
        "7 jours en cas de contact invalide."
    )

    _footer_publique()


def afficher_a_propos() -> None:
    """Page "À propos" (?vue=a_propos) — texte repris tel quel de
    Contenu_Site_Vitrine.md::"Page 'À propos'". Statut légal en placeholder
    explicite "[SIRET à venir]" tant que l'immatriculation n'est pas
    obtenue (voir generation_contrats.py::DEFAULTS_AGENCE["siret_statut"],
    toujours "en_cours" à ce stade)."""
    _navigation_publique("a_propos")

    st.title("Qui sommes-nous")
    st.write(
        f"{AGENCY_NAME} est une agence spécialisée dans la mise en relation entre "
        "professionnels du bâtiment et prospects qualifiés. [À compléter une fois les "
        "premiers témoignages disponibles — citation courte d'un client satisfait]."
    )
    st.caption("Statut légal : [SIRET à venir]")

    _footer_publique()


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
    #
    # corps_metier (selectbox, voir _champ_corps_metier) est en dehors du
    # st.form ci-dessous pour la même raison que type_offre plus haut :
    # avoir besoin de sa VALEUR avant la soumission n'est pas le cas ici,
    # mais le placer dans le form fonctionnerait tout aussi bien — gardé à
    # cet endroit uniquement pour rester visuellement juste après le choix
    # de formule, avant les champs texte proprement dits.
    corps_metier = _champ_corps_metier(f"corps_metier_intake_{lead_id}")
    communes_couvertes = st.multiselect(
        "Communes couvertes (zone d'intervention exploitable pour le rapprochement)",
        options=VILLES_CIBLES,
        key=f"communes_couvertes_{lead_id}",
    )

    with st.form("form_intake_public"):
        description = st.text_area(
            "Décrivez votre activité en quelques phrases", height=120, max_chars=3000,
        )
        zone_activite = st.text_input(
            "Zone d'intervention, en clair (ex: rayon, communes non listées ci-dessus)",
            placeholder="Ex : Reims et 30km alentour", max_chars=300,
        )
        st.caption(f"En envoyant ce formulaire, vous acceptez notre [{LIBELLE_LIEN_CONFIDENTIALITE}]({LIEN_CONFIDENTIALITE}).")
        envoyer = st.form_submit_button("Envoyer", type="primary", use_container_width=True)

    if not envoyer:
        return
    if not description.strip():
        st.warning("Merci de décrire votre activité avant d'envoyer.")
        return
    if not corps_metier:
        st.warning("Merci de sélectionner votre corps de métier avant d'envoyer.")
        return

    payload = {
        "lead_id": lead_id,
        "description": description,
        "corps_metier": corps_metier,
        "zone_activite": zone_activite,
        "communes_couvertes": communes_couvertes,
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
    encore construite.

    Titre/texte d'intro alignés le 19/08 sur Contenu_Site_Vitrine.md::
    "Section formulaire /demande-devis" (repris tel quel) — cette page est
    la cible réelle du formulaire générique décrit dans ce fichier, la
    seule des deux (avec afficher_devis, scopée à une campagne B2B) à ne
    dépendre d'aucun slug."""
    cle_envoye = "demande_devis_envoyee"
    if st.session_state.get(cle_envoye):
        _navigation_publique("demande_devis")
        st.title("Merci pour votre demande !")
        with st.container(border=True):
            st.write(
                "Nous avons bien reçu votre demande de devis et revenons vers vous "
                "très rapidement avec un ou plusieurs artisans qualifiés."
            )
        _footer_publique()
        return

    _navigation_publique("demande_devis")

    st.title("Obtenez un devis gratuit")
    st.write("Décrivez votre projet, nous le transmettons à un professionnel qualifié près de chez vous. Réponse rapide, sans engagement de votre part.")

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
        st.caption(
            "Vos informations sont utilisées uniquement pour vous mettre en relation avec "
            "un professionnel adapté à votre demande. "
            f"Voir notre [{LIBELLE_LIEN_CONFIDENTIALITE}]({LIEN_CONFIDENTIALITE})."
        )
        envoyer = st.form_submit_button("Envoyer ma demande", type="primary", use_container_width=True)

    _footer_publique()

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
            f"contactez-nous directement à {AGENCY_CONTACT_EMAIL}."
        )
        return
    st.markdown(contenu)
