"""
Génération du bon de commande / contrat de prestation (texte + PDF), CGV
incluses — logique de mise en page/juridique pure, sans aucun code Streamlit,
extraite de dashboard/app_pages/administration_contrats.py pour que cette
page reste une couche de présentation (formulaire) plutôt qu'un mélange des
deux : plus facile à relire/tester indépendamment de l'UI.
"""

import os
import re

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from scorer_leads import SEUIL_LEAD_PRIORITAIRE_B2C

DEFAULTS_AGENCE = {
    "nom": os.getenv("AGENCY_NAME", "Expertise Digitale"),
    "adresse": os.getenv("AGENCY_ADDRESS", ""),
    "email": os.getenv("AGENCY_EMAIL", ""),
    "siret_statut": "en_cours",  # "en_cours" | "definitif"
    "siret_numero": "",
}

# Couleurs aux teintes de l'agence — bleu nuit + accent or, utilisées pour
# le bandeau d'en-tête et les titres de section du PDF.
COULEUR_PRIMAIRE = (17, 42, 76)
COULEUR_ACCENT = (176, 141, 61)
COULEUR_TEXTE_CLAIR = (255, 255, 255)
COULEUR_GRIS = (110, 110, 110)


def _siret_agence_texte(agence: dict) -> str:
    if agence.get("siret_statut") == "definitif" and agence.get("siret_numero", "").strip():
        return agence["siret_numero"].strip()
    return "En cours d'immatriculation"


# Numéros SIRET déjà rencontrés dans ce projet comme placeholder d'exemple
# (jamais un vrai SIRET) — normalisés sans espaces. Incident réel corrigé le
# 10/08 : "123 456 789 00012" (repris tel quel du placeholder du champ de
# saisie, voir administration_contrats.py) était resté enregistré comme SIRET
# réel de l'agence dans agence_config. Liste extensible si un autre
# placeholder de ce type est un jour découvert.
SIRET_PLACEHOLDERS_CONNUS = {"12345678900012"}


def siret_valide_pour_statut_definitif(siret_numero: str) -> tuple[bool, str]:
    """Empêche de faire passer siret_statut à "definitif" (donc d'exposer le
    numéro sur un vrai contrat, voir _siret_agence_texte) tant que
    siret_numero est vide ou correspond à un placeholder d'exemple connu.
    Renvoie (valide, message_erreur — vide si valide)."""
    numero_nettoye = re.sub(r"\s+", "", siret_numero or "")
    if not numero_nettoye:
        return False, (
            "Le numéro SIRET est vide — impossible de passer au statut « Immatriculé » "
            "sans un vrai numéro."
        )
    if numero_nettoye in SIRET_PLACEHOLDERS_CONNUS:
        return False, (
            "Ce numéro SIRET correspond à un exemple/placeholder connu, pas à un vrai "
            "SIRET — impossible de passer au statut « Immatriculé »."
        )
    return True, ""


# Grille de prix par lead livré selon le type d'acteur B2B (architecte,
# promoteur, maître d'œuvre) — décision business validée, indépendante des
# poids de scoring outbound_chantiers/config.py::POIDS_TYPE_ACTEUR (ceux-ci
# priorisent l'ordre d'envoi, ils ne fixent aucun prix). Ces clés reprennent
# telles quelles celles utilisées dans campagnes.types_acteur_cibles (voir
# sql/init_campagnes.sql). Sert UNIQUEMENT d'aide-mémoire affiché à côté du
# champ "Prix" du bon de commande (voir administration_contrats.py) : le
# prix reste saisi à la main, jamais calculé automatiquement — un calcul
# automatique risquerait de sur-simplifier un cas particulier (remise
# négociée, palier de volume...).
GRILLE_PRIX_PAR_TYPE_ACTEUR_EUR = {
    "architecte": 70,
    "promoteur": 60,
    "maitre_oeuvre": 50,
}

# Tarification B2C (vente de leads aux artisans, table `leads`) — deux
# formules au choix du Client à l'intake (voir dashboard/pages_publiques.py
# ::afficher_intake) : paiement à l'unité (variable selon la qualité du lead
# livré) ou abonnement mensuel (formules à volume croissant). VALEURS
# PLACEHOLDER — montants définitifs non encore fixés par l'agence : à
# ajuster ici (ou via les variables d'environnement du même nom) avant toute
# mise en production réelle.

# Valeurs acceptées pour intake_responses.type_offre / contracts.type_offre
# (voir sql/init_intake_responses_leads_b2c.sql) — un seul point de vérité
# pour ces deux littéraux, réutilisé par construire_articles_cgv_b2c()
# ci-dessous et par dashboard/contrats_signature.py.
TYPE_OFFRE_UNITE = "unite"
TYPE_OFFRE_ABONNEMENT = "abonnement"


# ---------------------------------------------------------------------
# Axe 1 — à l'unité : prix variable selon la qualité (score) du lead livré.
# ---------------------------------------------------------------------
# Le score utilisé est celui déjà calculé par scorer_leads.py (table `leads`,
# 0-100) — même seuil que la mention "contact prioritaire" côté
# lead_worker.py::generer_pitch (SEUIL_LEAD_PRIORITAIRE_B2C, importé plutôt
# que redéfini : les deux DOIVENT rester synchronisés, contrairement à la
# duplication délibérée pratiquée ailleurs entre B2B et B2C — ici c'est le
# MÊME segment B2C, le même signal de qualité, pas deux logiques
# indépendantes).
#
# Le prix n'est donc connu qu'au moment de la LIVRAISON du lead (quand son
# score réel est disponible), jamais au moment du devis initial — voir
# dashboard/contrats_signature.py::creer_et_envoyer_lien_paiement, seul
# endroit où ce montant est réellement calculé et facturé.
PRIX_LEAD_PREMIUM_EUR = float(os.getenv("PRIX_LEAD_PREMIUM_EUR", "60"))
PRIX_LEAD_STANDARD_EUR = float(os.getenv("PRIX_LEAD_STANDARD_EUR", "45"))
PRIX_LEAD_BASIQUE_EUR = float(os.getenv("PRIX_LEAD_BASIQUE_EUR", "30"))

PALIER_PREMIUM = "premium"
PALIER_STANDARD = "standard"
PALIER_BASIQUE = "basique"

PRIX_PAR_PALIER_EUR = {
    PALIER_PREMIUM: PRIX_LEAD_PREMIUM_EUR,
    PALIER_STANDARD: PRIX_LEAD_STANDARD_EUR,
    PALIER_BASIQUE: PRIX_LEAD_BASIQUE_EUR,
}
LIBELLE_PALIER = {PALIER_PREMIUM: "Premium", PALIER_STANDARD: "Standard", PALIER_BASIQUE: "Basique"}

# 50 = score neutre par défaut appliqué par scorer_leads.py quand aucun
# signal d'activité chantiers n'est exploitable (commune absente/non
# résolvable — voir outbound_chantiers/signal_activite_chantiers.py::
# SCORE_NEUTRE_PAR_DEFAUT = 0.5, x100, sans bonus taille puisque
# tranche_effectif_salarie est elle aussi presque toujours absente pour ces
# leads — cf. diagnostic du 2026-08-13 sur les 20 leads sans commune).
# Valeur dupliquée ici (pas importée) pour ne jamais faire dépendre ce
# module du package B2B outbound_chantiers, même indirectement.
SCORE_NEUTRE_LEAD_B2C = 50


def palier_pour_score(score: int | None) -> str:
    """Palier de qualité (Premium/Standard/Basique) pour un score `leads.score`
    (0-100) donné. Un score neutre (50, y compris tous les leads sans
    commune identifiée) ou en-dessous tombe en Basique ; jamais d'exception
    sur une valeur manquante (score=None traité comme 0, donc Basique)."""
    score = score or 0
    if score >= SEUIL_LEAD_PRIORITAIRE_B2C:
        return PALIER_PREMIUM
    if score > SCORE_NEUTRE_LEAD_B2C:
        return PALIER_STANDARD
    return PALIER_BASIQUE


def prix_lead_unite_eur(score: int | None) -> float:
    return PRIX_PAR_PALIER_EUR[palier_pour_score(score)]


def fourchette_prix_unite_eur() -> tuple[float, float]:
    """(prix mini, prix maxi) toutes qualités confondues — affiché au devis
    tant que le lead réellement livré (et donc son palier) n'est pas
    encore connu."""
    valeurs = PRIX_PAR_PALIER_EUR.values()
    return min(valeurs), max(valeurs)


# ---------------------------------------------------------------------
# Axe 2 — abonnement : formules à volume croissant, tarif/lead dégressif.
# ---------------------------------------------------------------------
# Structure en liste de dictionnaires (plutôt que des constantes séparées
# par formule) : un seul point de vérité par formule (id, volume, prix,
# libellé), facile à faire évoluer (ajouter/retirer une formule, changer un
# montant) sans toucher au reste du code qui itère dessus.
FORMULES_ABONNEMENT = [
    {
        "id": "petit",
        "volume": 10,
        "prix_eur": float(os.getenv("PRIX_ABONNEMENT_PETIT_EUR", "350")),
        "label": "10 leads/mois",
    },
    {
        "id": "moyen",
        "volume": 20,
        "prix_eur": float(os.getenv("PRIX_ABONNEMENT_MOYEN_EUR", "640")),
        "label": "20 leads/mois",
    },
    {
        "id": "grand",
        "volume": 30,
        "prix_eur": float(os.getenv("PRIX_ABONNEMENT_GRAND_EUR", "900")),
        "label": "30 leads/mois",
    },
]
FORMULE_ABONNEMENT_PAR_DEFAUT = FORMULES_ABONNEMENT[0]["id"]


def formule_abonnement_par_id(formule_id: str | None) -> dict:
    """Retombe sur la plus petite formule si `formule_id` est absent/inconnu
    (donnée manquante/corrompue) plutôt que de lever une exception — même
    principe que palier_pour_score ci-dessus."""
    return next((f for f in FORMULES_ABONNEMENT if f["id"] == formule_id), FORMULES_ABONNEMENT[0])


def aide_memoire_prix(types_acteur_cibles: list[dict] | None) -> str:
    """Formate un rappel de la grille de prix par lead pour les types
    d'acteur d'une campagne donnée (ex: [{"cle":"architecte","libelle":
    "Architectes",...}, ...], voir campagnes.types_acteur_cibles). Renvoie
    une chaîne vide si la campagne ne cible aucun type d'acteur connu de la
    grille — à l'appelant de ne rien afficher dans ce cas plutôt que
    d'afficher un aide-mémoire trompeur pour un client hors grille B2B."""
    lignes = []
    for type_acteur in types_acteur_cibles or []:
        cle = type_acteur.get("cle", "")
        prix = GRILLE_PRIX_PAR_TYPE_ACTEUR_EUR.get(cle)
        if prix is None:
            continue
        libelle = type_acteur.get("libelle") or cle
        lignes.append(f"{libelle} : {prix} €/lead")
    return " · ".join(lignes)


def _article_renvoi_cgv_generales() -> tuple[str, str]:
    """Article de renvoi croisé, identique pour le B2B (construire_articles_cgv)
    et le B2C (construire_articles_cgv_b2c) : les conditions particulières du
    document signé (bon de commande B2B ou devis B2C) priment toujours sur les
    CGV générales publiques en cas de contradiction — un seul point de vérité
    pour ce texte, partagé par les deux fonctions plutôt que dupliqué, pour
    qu'il ne puisse jamais diverger silencieusement entre les deux tunnels."""
    return (
        "Article 9 - Articulation avec les Conditions Générales de Vente générales",
        "Les présentes conditions particulières, intégrées au présent contrat signé "
        "électroniquement, complètent et prévalent, en cas de contradiction, sur les "
        "Conditions Générales de Vente générales de l'Agence, disponibles sur son site "
        "internet.",
    )


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
        _article_renvoi_cgv_generales(),
    ]


def construire_articles_cgv_b2c(agence: dict, type_offre: str) -> list[tuple[str, str]]:
    """Conditions Générales de Prestation (CGV) — cadre B2C, applicable au
    devis signé électroniquement dans le tunnel artisan (voir
    dashboard/contrats_signature.py::generer_pdf_devis). Même structure et
    même ton que construire_articles_cgv (B2B) ci-dessus : la prestation
    réelle est la VENTE DE LEADS (demandes de devis qualifiées de
    particuliers/professionnels, dans la zone et le corps de métier du
    Client), exactement comme côté B2B (leads_professionnels) — pas la
    création de site vitrine de l'ancienne version de ce module (voir
    diagnostic du 2026-08-14).

    `type_offre` (TYPE_OFFRE_UNITE ou TYPE_OFFRE_ABONNEMENT, voir
    intake_responses.type_offre) détermine le mode de livraison et de prix
    décrits aux articles 2 et 5 ; toute autre valeur (donnée manquante/
    corrompue) retombe silencieusement sur TYPE_OFFRE_UNITE plutôt que de
    faire échouer la génération du devis.

    Volontairement AUCUN montant en euros n'est écrit en dur dans les
    articles ci-dessous (contrairement à la version précédente) : le prix à
    l'unité dépend du palier de qualité du lead réellement livré
    (PRIX_PAR_PALIER_EUR) et l'abonnement dépend de la formule de volume
    choisie (FORMULES_ABONNEMENT) — les CGV renvoient au présent devis pour
    les montants exacts, pour ne jamais avoir à réécrire ce texte juridique
    à chaque ajustement tarifaire (voir Article 5).

    La logique de statut SIRET (agence["siret_statut"]) reste volontairement
    DUPLIQUÉE depuis construire_articles_cgv plutôt que factorisée : même
    principe que SCORES_TRANCHE_EFFECTIF dans scorer_leads.py — B2B et B2C
    restent deux segments commerciaux indépendants, qui ne doivent jamais
    dépendre l'un de l'autre au niveau du code, même pour une clause au
    contenu proche."""
    nom = agence.get("nom") or DEFAULTS_AGENCE["nom"]
    abonnement = type_offre == TYPE_OFFRE_ABONNEMENT

    if agence.get("siret_statut") == "definitif" and agence.get("siret_numero", "").strip():
        texte_statut = (
            f"L'Agence est immatriculée sous le numéro SIRET {agence['siret_numero'].strip()}. "
            f"Ses coordonnées complètes figurent en en-tête du présent document."
        )
    else:
        texte_statut = (
            "À la date d'émission du présent document, l'Agence est en cours de formalisation "
            "de sa structure juridique (immatriculation SIRET en cours). Le Client en est "
            "informé et l'accepte expressément pour toute prestation exécutée au cours de "
            "cette phase transitoire."
        )

    if abonnement:
        texte_contenu = (
            "La prestation consiste en la mise à disposition du Client de demandes de "
            "devis qualifiées (« leads »), correspondant au corps de métier et à la zone "
            "d'intervention déclarés au présent devis, livrées au fil de l'eau à mesure de "
            "leur qualification. Dans la formule « abonnement », le Client bénéficie d'un "
            "volume de leads inclus chaque mois civil, correspondant à la formule choisie "
            "au présent devis parmi celles proposées par l'Agence, pour le prix forfaitaire "
            "fixé à l'article 5 ; les leads non consommés sur un mois donné ne sont ni "
            "reportés ni remboursés le mois suivant."
        )
        texte_prix = (
            "Le prix de la prestation, dans la formule « abonnement », est celui de la "
            "formule de volume choisie par le Client parmi celles proposées par l'Agence, "
            "tel qu'indiqué au présent devis, payable mensuellement d'avance à compter de "
            "la signature du présent devis. L'abonnement est reconduit tacitement chaque "
            "mois ; le Client peut y mettre fin à tout moment, sans pénalité, l'arrêt "
            "prenant effet à l'échéance mensuelle en cours."
        )
    else:
        texte_contenu = (
            "La prestation consiste en la mise à disposition du Client de demandes de "
            "devis qualifiées (« leads »), correspondant au corps de métier et à la zone "
            "d'intervention déclarés au présent devis, livrées au fil de l'eau à mesure de "
            "leur qualification. Dans la formule « à l'unité », chaque lead livré fait "
            "l'objet d'une facturation individuelle au tarif fixé à l'article 5 ; aucun "
            "volume ni fréquence de livraison n'est garanti sur une période donnée."
        )
        texte_prix = (
            "Le prix de la prestation, dans la formule « à l'unité », varie selon le "
            "palier de qualité du lead réellement livré (déterminé par le score attribué "
            "au lead au moment de sa livraison, selon la méthode de scoring en vigueur "
            "chez l'Agence), facturé et payable à chaque livraison, dans la fourchette "
            "indiquée au présent devis. Le présent devis est conclu pour une durée "
            "indéterminée et peut être résilié à tout moment par le Client, sans "
            "engagement de volume minimum."
        )

    return [
        (
            "Article 1 - Objet et champ d'application",
            f"Les présentes Conditions Générales de Prestation régissent la prestation de "
            f"mise en relation commerciale par la fourniture de demandes de devis "
            f"qualifiées (« leads ») de particuliers et professionnels intéressés par les "
            f"prestations du Client, dans sa zone d'activité, fournie par {nom} (« l'Agence ») "
            f"au client artisan ou professionnel du bâtiment signataire du présent devis "
            f"(« le Client »). Les informations particulières figurant au présent devis "
            f"(corps de métier, zone d'intervention, type d'offre choisi) complètent les "
            f"présentes CGV et prévalent en cas de contradiction entre les deux documents.",
        ),
        ("Article 2 - Contenu de la prestation", texte_contenu),
        (
            "Article 3 - Obligation de moyens",
            "L'Agence met en œuvre tous les moyens raisonnables (sourcing, qualification) "
            "pour identifier et transmettre au Client des demandes de devis correspondant "
            "au corps de métier et à la zone d'intervention déclarés au présent devis. Les "
            "parties reconnaissent expressément que l'Agence est tenue à une obligation de "
            "moyens et non de résultat : elle ne garantit ni un volume précis de leads sur "
            "une période donnée, ni la transformation commerciale effective (signature, "
            "vente) des leads livrés au Client.",
        ),
        (
            "Article 4 - Délai de réclamation et garantie de remplacement",
            "Toute réclamation relative à un lead livré (contact invalide, hors zone "
            "d'intervention ou corps de métier convenu) doit être formulée par écrit dans "
            "un délai de 7 (sept) jours calendaires à compter de sa livraison ; passé ce "
            "délai, le lead est réputé conforme et définitivement accepté. Pour toute "
            "réclamation recevable et formulée dans les délais, l'Agence propose, à son "
            "choix, le remplacement du lead ou l'émission d'un avoir commercial d'un "
            "montant correspondant, à l'exclusion de toute autre indemnisation.",
        ),
        ("Article 5 - Prix, paiement et durée", texte_prix),
        (
            "Article 6 - Confidentialité et données personnelles",
            "Chaque partie s'engage à garder confidentielles les informations commerciales "
            "et techniques échangées dans le cadre du contrat. Les données personnelles du "
            "Client sont traitées par l'Agence dans le respect du Règlement Général sur la "
            "Protection des Données (RGPD), exclusivement aux fins de l'exécution de la "
            "présente prestation et de la relation commerciale qui en découle.",
        ),
        (
            "Article 7 - Responsabilité et droit applicable",
            "La responsabilité de l'Agence est limitée au montant effectivement payé au "
            "titre de la prestation, hors faute lourde ou dolosive ; elle ne saurait être "
            "tenue pour responsable des conséquences indirectes résultant de l'utilisation "
            "des leads par le Client, notamment de leur exploitation commerciale ou du taux "
            "de transformation obtenu. Les présentes CGV sont soumises au droit français ; "
            "tout litige non résolu à l'amiable relève de la juridiction compétente du "
            "siège social de l'Agence.",
        ),
        ("Article 8 - Statut juridique de l'Agence", texte_statut),
        _article_renvoi_cgv_generales(),
    ]


def slugifier_reference(texte: str, repli: str = "CLIENT") -> str:
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
