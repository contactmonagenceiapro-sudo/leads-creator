"""
Interface "Administration & Contrats" — génération d'un document contractuel
(bon de commande / contrat de prestation) pré-rempli avec les informations
d'un client de l'agence, quel qu'il soit, prêt à copier ou télécharger
en PDF. Le PDF intègre directement le corps des Conditions Générales de
Prestation (CGV) de l'agence : le document généré est prêt à être envoyé
tel quel à un client (ex. S.B.G Travaux) avec le devis ou le bon de commande.

Les informations de l'agence elle-même (nom, adresse, e-mail, statut SIRET)
se configurent une seule fois via la section "Configuration de l'agence"
ci-dessous, et sont ensuite injectées automatiquement dans l'en-tête et le
bloc "Pour l'agence" de chaque document généré (voir charger_config_agence).

Espace admin uniquement : ces documents engagent l'agence auprès de tiers,
jamais accessible depuis le portail client (voir dashboard/app.py).

Ce générateur ne fait QUE produire un document à partir de la saisie du
formulaire — rien n'est enregistré côté API/Supabase (aucune table dédiée
n'existe à ce jour) : seule la configuration de l'agence est persistée
localement (voir CONFIG_PATH), chaque bon de commande repart d'une saisie
vierge.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import streamlit as st
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from data_access import get_campagnes
from common import safe_call

# ---------------------------------------------------------------------
# Configuration de l'agence — persistée dans un fichier JSON local plutôt
# qu'en session Streamlit, pour survivre aux rechargements de page ET aux
# redémarrages du conteneur (voir le volume "dashboard_data" monté sur
# /app/data dans docker-compose.yml). Les variables d'environnement
# AGENCY_NAME/AGENCY_EMAIL restent les valeurs par défaut tant qu'aucune
# configuration n'a encore été enregistrée depuis l'interface.
# ---------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = CONFIG_DIR / "agence_config.json"

DEFAULTS_AGENCE = {
    "nom": os.getenv("AGENCY_NAME", "Expertise Digitale"),
    "adresse": os.getenv("AGENCY_ADDRESS", ""),
    "email": os.getenv("AGENCY_EMAIL", ""),
    "siret_statut": "en_cours",  # "en_cours" | "definitif"
    "siret_numero": "",
}


def charger_config_agence() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS_AGENCE, **{k: v for k, v in data.items() if k in DEFAULTS_AGENCE}}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS_AGENCE)


def sauvegarder_config_agence(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _siret_agence_texte(agence: dict) -> str:
    if agence.get("siret_statut") == "definitif" and agence.get("siret_numero", "").strip():
        return agence["siret_numero"].strip()
    return "En cours d'immatriculation"


# Couleurs aux teintes de l'agence — bleu nuit + accent or, utilisées pour
# le bandeau d'en-tête et les titres de section du PDF.
COULEUR_PRIMAIRE = (17, 42, 76)
COULEUR_ACCENT = (176, 141, 61)
COULEUR_TEXTE_CLAIR = (255, 255, 255)
COULEUR_GRIS = (110, 110, 110)


def construire_articles_cgv(agence: dict) -> list[tuple[str, str]]:
    """Conditions Générales de Prestation (CGV) — cadre B2B générique,
    applicable à tout client de l'agence quel que soit le bon de commande.
    Volontairement condensées (le corps essentiel de chaque clause, sans
    développement juridique exhaustif) pour que le document final tienne sur
    1-2 pages tout en couvrant les points contractuels clés : définition du
    lead qualifié, obligation de moyens, délai de réclamation de 7 jours. Les
    conditions particulières du bon de commande complètent ces CGV et
    prévalent en cas de contradiction (voir Article 1). Les Articles 1 et 8
    dépendent de la configuration de l'agence (nom, statut SIRET)."""
    nom = agence.get("nom") or DEFAULTS_AGENCE["nom"]

    if agence.get("siret_statut") == "definitif" and agence.get("siret_numero", "").strip():
        texte_statut = (
            f"L'Agence est immatriculee sous le numero SIRET {agence['siret_numero'].strip()}. "
            f"Ses coordonnees completes figurent en en-tete du present document."
        )
    else:
        texte_statut = (
            "A la date d'emission du present document, l'Agence est en cours de formalisation "
            "de sa structure juridique (immatriculation SIRET en cours). Le Client en est "
            "informe et l'accepte expressement pour toute prestation executee au cours de "
            "cette phase transitoire."
        )

    return [
        (
            "Article 1 - Objet et champ d'application",
            f"Les presentes Conditions Generales de Prestation regissent l'ensemble des "
            f"prestations de prospection commerciale, de generation et de qualification de "
            f"leads B2B fournies par {nom} (\"l'Agence\") a ses clients professionnels "
            f"(\"le Client\"). Les conditions particulieres figurant au bon de commande "
            f"completent les presentes CGV et prevalent en cas de contradiction entre les "
            f"deux documents.",
        ),
        (
            "Article 2 - Definition du lead qualifie",
            "Sauf definition differente prevue aux conditions particulieres du bon de "
            "commande, un lead est considere comme qualifie s'il remplit cumulativement deux "
            "conditions : il correspond aux criteres de ciblage convenus avec le Client "
            "(secteur d'activite, zone geographique, besoin exprime), et il dispose d'un "
            "contact valide et joignable (adresse email et/ou numero de telephone verifie).",
        ),
        (
            "Article 3 - Obligation de moyens",
            "L'Agence met en oeuvre tous les moyens raisonnables (techniques, humains et "
            "methodologiques) pour identifier, qualifier et livrer au Client les leads "
            "convenus. Les parties reconnaissent expressement que l'Agence est tenue a une "
            "obligation de moyens et non de resultat : au-dela du volume explicitement "
            "chiffre au bon de commande, elle ne garantit ni volume supplementaire de leads "
            "ni transformation commerciale effective (signature, vente) des leads livres.",
        ),
        (
            "Article 4 - Delai de reclamation et garantie de remplacement",
            "Toute reclamation relative a un lead livre (contact invalide, hors perimetre "
            "convenu) doit etre formulee par ecrit dans un delai de 7 (sept) jours "
            "calendaires a compter de sa livraison ; passe ce delai, le lead est repute "
            "conforme et definitivement accepte. Pour toute reclamation recevable et "
            "formulee dans les delais, l'Agence propose, a son choix, le remplacement du "
            "lead ou l'emission d'un avoir commercial d'un montant correspondant, a "
            "l'exclusion de toute autre indemnisation.",
        ),
        (
            "Article 5 - Prix, paiement et duree",
            "Le prix de la prestation est celui indique au bon de commande, exprime en "
            "euros et payable a reception de facture, sauf stipulation contraire. Le "
            "contrat prend fin de plein droit a l'achevement du volume de prestation "
            "convenu, sauf reconduction expresse convenue entre les parties.",
        ),
        (
            "Article 6 - Confidentialite et donnees personnelles",
            "Chaque partie s'engage a garder confidentielles les informations "
            "commerciales, techniques ou financieres echangees dans le cadre du contrat. "
            "Les donnees personnelles des leads sont traitees par l'Agence dans le "
            "respect du Reglement General sur la Protection des Donnees (RGPD), "
            "exclusivement a des fins de prospection commerciale B2B conforme a "
            "l'interet legitime des parties.",
        ),
        (
            "Article 7 - Responsabilite et droit applicable",
            "La responsabilite de l'Agence est limitee au montant effectivement facture "
            "au titre de la prestation concernee, hors faute lourde ou dolosive ; elle ne "
            "saurait etre tenue pour responsable des consequences indirectes resultant de "
            "l'utilisation des leads par le Client. Les presentes CGV sont soumises au "
            "droit francais ; tout litige non resolu a l'amiable releve de la juridiction "
            "competente du siege social de l'Agence.",
        ),
        ("Article 8 - Statut juridique de l'Agence", texte_statut),
    ]


def _slugifier(texte: str, repli: str = "CLIENT") -> str:
    nettoye = re.sub(r"[^A-Za-z0-9]+", "-", (texte or "").strip()).strip("-").upper()
    return nettoye[:24] or repli


def _lignes_liste(texte: str) -> list[str]:
    """Sépare un bloc de texte en lignes, en retirant une éventuelle puce
    ('- ') déjà saisie par l'utilisateur pour éviter de la doubler."""
    lignes = []
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        lignes.append(re.sub(r"^[-•]\s*", "", ligne))
    return lignes


class _DocumentPDF(FPDF):
    """Pied de page imprimé automatiquement par fpdf2 sur chaque page, via le
    mécanisme interne dédié (footer()) plutôt qu'un set_y(-15) manuel en fin
    de contenu — ce dernier déclenche à tort un saut de page automatique (la
    position bascule sous le seuil de marge de bas de page), ce qui produisait
    une page blanche superflue. `reference_doc`/`nom_agence` sont posés par
    construire_pdf() juste après l'instanciation, avant le premier add_page()."""

    reference_doc: str = ""
    nom_agence: str = ""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # cp1252 (cf. Windows-1252) plutôt que le strict latin-1 par défaut,
        # pour accepter le symbole "€" — très probable dans le champ Prix —
        # sans faire planter la génération (Helvetica reste une police "core"
        # PDF standard, aucune police à embarquer).
        self.core_fonts_encoding = "cp1252"

    def footer(self) -> None:
        self.set_y(-15)
        self.set_draw_color(*COULEUR_GRIS)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_y(-13)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*COULEUR_GRIS)
        self.cell(0, 6, f"{self.nom_agence} - {self.reference_doc} - page {self.page_no()}")


def construire_pdf(donnees: dict, agence: dict) -> bytes:
    """Construit le bon de commande / contrat de prestation complet : en-tête,
    identité des parties (bloc "Pour l'agence" auto-rempli depuis `agence`),
    objet de la commande, corps des CGV, bloc de signatures « Bon pour
    accord ». Un seul flux continu (pas de saut de page forcé) : fpdf2 ne
    crée une page 2 que si le contenu déborde réellement, pour tenir sur 1 à
    2 pages selon la longueur des champs saisis."""
    nom_agence = agence.get("nom") or DEFAULTS_AGENCE["nom"]
    email_agence = agence.get("email", "")

    pdf = _DocumentPDF(format="A4")
    pdf.reference_doc = donnees["reference"]
    pdf.nom_agence = nom_agence
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # --- Bandeau d'en-tête : nom de l'agence + coordonnées ---
    pdf.set_fill_color(*COULEUR_PRIMAIRE)
    pdf.rect(0, 0, 210, 26, style="F")
    pdf.set_xy(10, 6)
    pdf.set_text_color(*COULEUR_TEXTE_CLAIR)
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 9, nom_agence, ln=True)
    pdf.set_x(10)
    pdf.set_font("Helvetica", "", 9)
    sous_titre = "Prospection & generation de leads qualifies"
    if email_agence:
        sous_titre += f" - {email_agence}"
    pdf.cell(0, 6, sous_titre)

    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 31)

    # --- Titre du document + numéro de document / date ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(
        0, 6.5, "BON DE COMMANDE / CONTRAT DE PRESTATION",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*COULEUR_GRIS)
    pdf.set_x(10)
    pdf.cell(0, 5.5, f"Document n. {donnees['reference']}    -    Date : {donnees['date_document']}", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2.5)

    def titre_section(texte: str) -> None:
        pdf.set_fill_color(*COULEUR_ACCENT)
        pdf.rect(10, pdf.get_y() + 1, 3, 4.5, style="F")
        pdf.set_xy(16, pdf.get_y())
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*COULEUR_PRIMAIRE)
        pdf.cell(0, 6.5, texte, ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.ln(0.5)

    def champ(label: str, valeur: str, largeur_label: int = 40) -> None:
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.cell(largeur_label, 5.5, label)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.multi_cell(0, 5.5, valeur or "A completer", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # --- Parties ---
    titre_section("Entre les soussignés")

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5.5, "L'agence :", ln=True)
    champ("Raison sociale", nom_agence)
    champ("Adresse", agence.get("adresse", ""))
    champ("SIRET", _siret_agence_texte(agence))
    if email_agence:
        champ("Contact", email_agence)
    pdf.ln(1.5)

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5.5, "Le client :", ln=True)
    champ("Raison sociale", donnees["nom_entreprise"])
    champ("SIRET", donnees["siret_client"])
    champ("Siege social", donnees["adresse_siege"])
    champ("Represente par", donnees["nom_representant"])
    pdf.ln(2)

    # --- Objet de la commande : volume + prix ---
    titre_section("Objet de la commande")
    champ("Volume", donnees["volume_prestation"], largeur_label=25)
    champ("Prix", donnees["prix_prestation"], largeur_label=25)
    pdf.ln(2)

    # --- Conditions particulières (facultatif, propre à cette commande) ---
    lignes_particulieres = _lignes_liste(donnees["conditions_particulieres"])
    if lignes_particulieres:
        titre_section("Conditions particulières de cette commande")
        for ligne in lignes_particulieres:
            pdf.set_x(10)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.cell(4, 5.5, "-")
            pdf.multi_cell(0, 5.5, ligne, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    # --- Corps des CGV ---
    titre_section("Conditions générales de prestation")
    for titre, corps in construire_articles_cgv(agence):
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.multi_cell(0, 5, titre, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(10)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.multi_cell(0, 4.3, corps, align="J", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.5)
    pdf.ln(2)

    # --- Bon pour accord / signatures ---
    titre_section("Bon pour accord")
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_x(10)
    pdf.multi_cell(
        0, 5,
        "Chaque partie reconnait avoir pris connaissance et accepter sans reserve les "
        "conditions du present document, en ce compris les conditions generales de "
        "prestation ci-dessus.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.ln(3)

    y_signature = pdf.get_y()
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_xy(10, y_signature)
    pdf.cell(90, 5.5, "Pour l'agence")
    pdf.set_xy(110, y_signature)
    pdf.cell(90, 5.5, "Pour le client", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(10, y_signature + 5.5)
    pdf.multi_cell(90, 5.5, f"{nom_agence}\nDate :\nSignature :")
    pdf.set_xy(110, y_signature + 5.5)
    pdf.multi_cell(90, 5.5, f"{donnees['nom_entreprise']}\nDate :\nSignature :")

    return bytes(pdf.output(dest="S"))


def construire_texte(donnees: dict, agence: dict) -> str:
    nom_agence = agence.get("nom") or DEFAULTS_AGENCE["nom"]

    lignes_particulieres = _lignes_liste(donnees["conditions_particulieres"])
    bloc_particulieres = ""
    if lignes_particulieres:
        bloc_particulieres = (
            "\nCONDITIONS PARTICULIÈRES DE CETTE COMMANDE\n"
            + "\n".join(f"- {l}" for l in lignes_particulieres)
            + "\n"
        )

    bloc_cgv = "\n\n".join(f"{titre}\n{corps}" for titre, corps in construire_articles_cgv(agence))

    return f"""BON DE COMMANDE / CONTRAT DE PRESTATION
Document n. {donnees['reference']}
Date : {donnees['date_document']}

ENTRE LES SOUSSIGNÉS

L'agence :
  Raison sociale : {nom_agence}
  Adresse : {agence.get('adresse') or '—'}
  SIRET : {_siret_agence_texte(agence)}
  Contact : {agence.get('email') or '—'}

Le client :
  Raison sociale : {donnees['nom_entreprise'] or '—'}
  SIRET : {donnees['siret_client'] or '—'}
  Siège social : {donnees['adresse_siege'] or '—'}
  Représenté par : {donnees['nom_representant'] or '—'}

OBJET DE LA COMMANDE
  Volume : {donnees['volume_prestation'] or '—'}
  Prix : {donnees['prix_prestation'] or '—'}
{bloc_particulieres}
CONDITIONS GÉNÉRALES DE PRESTATION
{bloc_cgv}

BON POUR ACCORD
Chaque partie reconnaît avoir pris connaissance et accepter sans réserve les conditions du
présent document, en ce compris les conditions générales de prestation ci-dessus.

  Pour l'agence ({nom_agence})          Pour le client ({donnees['nom_entreprise'] or '—'})
  Date :                                  Date :
  Signature :                             Signature :
"""


st.title("📑 Administration & Contrats")
st.caption(
    "Génère un bon de commande / contrat de prestation pré-rempli pour n'importe quel client "
    "de l'agence, CGV incluses, prêt à copier ou télécharger en PDF."
)
st.info(
    "Le PDF généré inclut le bon de commande ET le corps des Conditions Générales de "
    "Prestation de l'agence — il est prêt à être envoyé tel quel à un client. Rien n'est "
    "enregistré côté serveur pour les bons de commande : chaque génération repart d'une "
    "saisie vierge (seule la configuration de l'agence ci-dessous est mémorisée).",
    icon="ℹ️",
)

agence_config = charger_config_agence()

with st.expander(
    "⚙️ Configuration de l'agence — à renseigner une seule fois (auto-remplit le bloc « Pour l'agence »)",
    expanded=not CONFIG_PATH.exists(),
):
    with st.form("form_config_agence"):
        nom_agence_saisi = st.text_input("Nom de l'agence", value=agence_config["nom"])
        adresse_agence_saisie = st.text_area(
            "Adresse du siège", value=agence_config["adresse"], height=80,
            placeholder="ex. 1 rue de l'Exemple, 75000 Paris",
        )
        email_agence_saisi = st.text_input(
            "E-mail de contact", value=agence_config["email"], placeholder="ex. contact@expertise-digitale.fr",
        )
        statut_siret_libelle = st.radio(
            "Statut SIRET",
            options=["En cours d'immatriculation", "Immatriculé (SIRET définitif)"],
            index=1 if agence_config["siret_statut"] == "definitif" else 0,
            horizontal=True,
        )
        siret_agence_saisi = st.text_input(
            "Numéro SIRET (si immatriculé)", value=agence_config["siret_numero"],
            placeholder="ex. 123 456 789 00012",
        )
        enregistrer_agence = st.form_submit_button(
            "💾 Enregistrer la configuration de l'agence", type="primary", use_container_width=True,
        )

    if enregistrer_agence:
        try:
            sauvegarder_config_agence({
                "nom": nom_agence_saisi.strip() or DEFAULTS_AGENCE["nom"],
                "adresse": adresse_agence_saisie.strip(),
                "email": email_agence_saisi.strip(),
                "siret_statut": "definitif" if statut_siret_libelle.startswith("Immatriculé") else "en_cours",
                "siret_numero": siret_agence_saisi.strip(),
            })
        except OSError as e:
            st.error(f"Impossible d'enregistrer la configuration : {e}")
        else:
            st.success("Configuration de l'agence enregistrée — elle sera utilisée pour tous les prochains documents.")
            st.rerun()

campagnes_data, campagnes_error = safe_call(get_campagnes)
if campagnes_error:
    st.error(campagnes_error)
liste_campagnes = (campagnes_data or {}).get("campagnes", []) if campagnes_data else []
noms_campagnes = [c["nom_client"] for c in liste_campagnes]

st.subheader("1. Client")

mode_client = st.radio(
    "Sélection du client",
    options=["Nouveau client (saisie libre)", "Client existant (campagne configurée)"],
    horizontal=True,
    index=0,
    disabled=not noms_campagnes,
    label_visibility="collapsed",
)

if mode_client == "Client existant (campagne configurée)" and noms_campagnes:
    nom_entreprise = st.selectbox("Entreprise cliente", options=noms_campagnes)
else:
    nom_entreprise = st.text_input("Nom de l'entreprise cliente", placeholder="ex. ACME Bâtiment SARL")

col1, col2 = st.columns(2)
with col1:
    siret_client = st.text_input("Numéro SIRET du client", placeholder="ex. 123 456 789 00012")
    nom_representant = st.text_input("Nom du représentant", placeholder="ex. Jean Dupont")
with col2:
    adresse_siege = st.text_area(
        "Adresse du siège social",
        placeholder="ex. 1 rue de l'Exemple, 75000 Paris",
        height=100,
    )

st.subheader("2. Objet de la commande")

col_obj1, col_obj2 = st.columns(2)
with col_obj1:
    volume_prestation = st.text_area(
        "Volume de la prestation / du test",
        placeholder="ex. Phase de test limitée à 2 leads qualifiés, livrés sous 15 jours.",
        height=80,
    )
with col_obj2:
    prix_prestation = st.text_area(
        "Prix de la prestation",
        placeholder="ex. 990 € HT, ou « Phase de test gratuite »",
        height=80,
    )

conditions_particulieres = st.text_area(
    "Conditions particulières (facultatif, une par ligne — en complément des CGV ci-dessous)",
    placeholder="ex. Livraison des leads sous 15 jours ouvrés.",
    height=90,
)

with st.expander("📜 Conditions générales de prestation incluses dans le document"):
    for titre, corps in construire_articles_cgv(agence_config):
        st.markdown(f"**{titre}**")
        st.caption(corps)

date_document = st.date_input("Date du document", value=date.today())

st.divider()
st.subheader("3. Génération")

if st.button("📄 Générer le document", type="primary", use_container_width=True):
    if not nom_entreprise.strip():
        st.warning("Le nom de l'entreprise cliente est requis.")
    else:
        donnees = {
            "nom_entreprise": nom_entreprise.strip(),
            "siret_client": siret_client.strip(),
            "adresse_siege": adresse_siege.strip(),
            "nom_representant": nom_representant.strip(),
            "volume_prestation": volume_prestation.strip(),
            "prix_prestation": prix_prestation.strip(),
            "conditions_particulieres": conditions_particulieres,
            "date_document": date_document.strftime("%d/%m/%Y"),
            "reference": f"{_slugifier(agence_config['nom'], 'ED')}-{date_document:%Y%m%d}-{_slugifier(nom_entreprise)}",
        }
        st.session_state["contrat_donnees"] = donnees

donnees = st.session_state.get("contrat_donnees")
if donnees:
    try:
        with st.spinner("Génération du document..."):
            texte = construire_texte(donnees, agence_config)
            pdf_bytes = construire_pdf(donnees, agence_config)
    except Exception as e:
        # Filet de sécurité total : génération purement locale (pas d'appel
        # réseau) mais un caractère non pris en charge dans un champ saisi
        # (ou tout autre bug) ne doit jamais faire planter la page — un
        # message d'erreur clair, jamais une page figée ou un traceback brut.
        texte, pdf_bytes = None, None
        st.error(
            f"Impossible de générer le document (probablement un caractère non pris en charge "
            f"dans un des champs) : {e}"
        )

    if texte and pdf_bytes:
        st.text_area("Aperçu", value=texte, height=380)

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "⬇️ Télécharger en PDF",
                data=pdf_bytes,
                file_name=f"{donnees['reference']}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with col_dl2:
            st.download_button(
                "⬇️ Télécharger en texte (.txt)",
                data=texte,
                file_name=f"{donnees['reference']}.txt",
                mime="text/plain",
                use_container_width=True,
            )
