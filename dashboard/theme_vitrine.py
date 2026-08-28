"""
CSS + composants centralisés du thème "Chantier Ouvert" — habille le site
vitrine public et le formulaire de demande de devis (dashboard/pages_publiques.py)
d'une identité claire, distincte du thème sombre "Plan de Chantier" de
l'admin (voir dashboard/theme.py) — plan de design validé le 28/08/2026,
palette et élément signature retravaillés le 28/08/2026 (v2) après retour
utilisateur : la v1 (fond crème + accent orange/terracotta) reproduisait
telle quelle le cliché "site généré par IA" explicitement à éviter, et rien
ne distinguait la page comme spécifique au bâtiment.

Direction v2 — le "cordeau" : l'accent devient un bleu de cordeau à tracer
(--bleu-cordeau), la couleur de la ligne que les artisans tendent et
claquent pour marquer un repère droit avant de couper. Élément signature
unique : ligne_cordeau() (voir plus bas), un séparateur de section qui
reprend ce geste concret (ligne tendue, piquets aux extrémités, graduations)
au lieu d'un simple <hr>/st.divider() générique. Un seul signature élément
fort, tout le reste reste sobre — voir docstring de ligne_cordeau().

Contrainte technique importante : .streamlit/config.toml définit un thème
UNIQUE pour toute l'app Streamlit (pas de thème par page). La vitrine ne
peut donc pas avoir nativement un fond clair pendant que l'admin reste
sombre — ce module surcharge donc AGRESSIVEMENT les couleurs natives (fond,
texte, boutons, champs, alertes) via CSS avec !important, uniquement quand
une vue publique est active. Injecté UNE SEULE FOIS depuis dashboard/app.py,
dans la branche `if _vue_publique in _VUES_PUBLIQUES:`, jamais après
exiger_connexion() (là où dashboard/theme.py::injecter_theme() prend le
relais pour l'admin) — les deux thèmes ne se chevauchent jamais dans la
même page chargée.

Sélecteurs `[data-testid=...]` : contrat public de theming Streamlit
(engagement de stabilité), mais non vérifiés dans un vrai navigateur (aucun
outil Playwright/navigateur dans cet environnement, voir mémoire projet) —
un contrôle visuel réel reste nécessaire avant de considérer le rendu
acquis. Idem pour `st.container(key=...)` : documenté par Streamlit comme
générant une classe CSS stable `st-key-<key>` sur le conteneur englobant,
utilisé ici pour scoper le style de la navigation (_navigation_publique)
sans toucher aux autres boutons de la page.
"""

import streamlit as st

_CSS = """
<style>
:root {
    --blanc-chantier: #FAF9F6;
    --blanc-chantier-raise: #FFFFFF;
    --beton: #E7E3DC;
    --beton-line: #D6D0C5;
    --bleu-plan: #16283D;
    --gris-ardoise: #4B5768;
    --gris-faint: #7C889A;
    --bleu-cordeau: #1D4F8F;
    --bleu-cordeau-vif: #2E6FC7;
    --vert-verifie: #227A4E;
}

/* -----------------------------------------------------------------
   Fond général et texte — surcharge du thème natif sombre (admin), voir
   docstring du module pour pourquoi cette surcharge est nécessaire.
------------------------------------------------------------------ */
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stMain"] {
    background: var(--blanc-chantier) !important;
}
.stApp, .stApp p, .stApp span, .stApp label, .stApp li {
    color: var(--gris-ardoise) !important;
    font-family: 'Public Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
    font-family: 'Fraunces', Georgia, serif !important;
    color: var(--bleu-plan) !important;
    letter-spacing: -0.01em;
}
.stApp a { color: var(--bleu-cordeau) !important; }
.stApp hr { border-color: var(--beton-line) !important; }

/* -----------------------------------------------------------------
   Boutons — bleu cordeau réservé au primaire (texte blanc : contraste
   8,2:1, voir le plan de design v2), le reste en contour.
------------------------------------------------------------------ */
.stApp button[kind="primary"], .stApp button[data-testid="baseButton-primary"] {
    background: var(--bleu-cordeau) !important;
    color: var(--blanc-chantier-raise) !important;
    border: none !important;
    border-radius: 2px !important;
    font-weight: 600 !important;
}
.stApp button[kind="secondary"], .stApp button[data-testid="baseButton-secondary"],
.stApp a[data-testid="stBaseLinkButton-secondary"] {
    background: transparent !important;
    color: var(--bleu-plan) !important;
    border: 1px solid var(--gris-faint) !important;
    border-radius: 2px !important;
}
.stApp a[data-testid="stBaseLinkButton-primary"] {
    background: var(--bleu-cordeau) !important;
    color: var(--blanc-chantier-raise) !important;
    border: none !important;
    border-radius: 2px !important;
    font-weight: 600 !important;
}
.stApp button:disabled {
    background: var(--beton) !important;
    color: var(--gris-faint) !important;
    border: 1px solid var(--beton-line) !important;
}

/* -----------------------------------------------------------------
   Champs de formulaire — bordure visible, focus bleu cordeau (accessibilité :
   focus clavier explicite sur le point de conversion le plus important).
------------------------------------------------------------------ */
.stApp input, .stApp textarea, .stApp [data-baseweb="select"] > div {
    background: var(--blanc-chantier-raise) !important;
    color: var(--bleu-plan) !important;
    border: 1px solid var(--beton-line) !important;
    border-radius: 2px !important;
}
.stApp input:focus, .stApp textarea:focus,
.stApp *:focus-visible {
    outline: 2px solid var(--bleu-cordeau) !important;
    outline-offset: 2px !important;
}

/* -----------------------------------------------------------------
   Conteneurs à bordure, dataframes — cohérents avec le fond clair.
------------------------------------------------------------------ */
.stApp [data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--blanc-chantier-raise) !important;
    border: 1px solid var(--beton-line) !important;
}
.stApp [data-testid="stDataFrame"] {
    border: 1px solid var(--beton-line) !important;
}

/* -----------------------------------------------------------------
   Messages (st.success/st.info/st.warning/st.error) — recolorés pour rester
   lisibles sur fond clair (les teintes natives sont calibrées pour le
   thème sombre de l'admin).
------------------------------------------------------------------ */
.stApp [data-testid="stAlert"] {
    background: var(--beton) !important;
    border: 1px solid var(--beton-line) !important;
    color: var(--bleu-plan) !important;
    border-radius: 2px !important;
}
.stApp [data-testid="stAlert"] p { color: var(--bleu-plan) !important; }

/* -----------------------------------------------------------------
   Navigation publique (_navigation_publique) — scopée via
   st.container(key="nav-vitrine") pour ne pas toucher aux autres boutons
   de la page. Look "graduation de cordeau" plutôt que boutons Streamlit
   par défaut : pas de fond/bordure de bouton, juste un trait bas qui se
   comporte comme une marque de mesure (fin au repos, plein en survol,
   épais et bleu sur la page active).
------------------------------------------------------------------ */
.st-key-nav-vitrine div[data-testid="stHorizontalBlock"] {
    gap: 0 !important;
}
.st-key-nav-vitrine button {
    background: transparent !important;
    border: none !important;
    border-bottom: 2px dotted var(--beton-line) !important;
    border-radius: 0 !important;
    color: var(--gris-ardoise) !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.03em !important;
    text-transform: uppercase !important;
    padding: 0.6rem 0.2rem !important;
}
.st-key-nav-vitrine button:hover:not(:disabled) {
    border-bottom: 2px solid var(--bleu-cordeau-vif) !important;
    color: var(--bleu-cordeau) !important;
}
.st-key-nav-vitrine button:disabled {
    background: transparent !important;
    border-bottom: 4px solid var(--bleu-cordeau) !important;
    color: var(--bleu-plan) !important;
    font-weight: 600 !important;
    opacity: 1 !important;
}

/* -----------------------------------------------------------------
   Mobile — hero et bandeau de confiance restent lisibles, pas de texte
   trop serré sur petit écran.
------------------------------------------------------------------ */
@media (max-width: 640px) {
    .stApp h1 { font-size: 1.9rem !important; }
    .vitrine-trust-bar { flex-direction: column !important; gap: 0.8rem !important; }
    .st-key-nav-vitrine div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
}

@media (prefers-reduced-motion: reduce) {
    .stApp * { transition: none !important; animation: none !important; }
}
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
"""


def injecter_theme_vitrine() -> None:
    """Injecte le CSS du thème "Chantier Ouvert" — à appeler UNE SEULE FOIS
    par page vue, dans dashboard/app.py juste avant de router vers la vue
    publique demandée. Voir la docstring du module."""
    st.markdown(_CSS, unsafe_allow_html=True)


def tampon_confiance(libelle: str) -> str:
    """Élément signature du plan de design — pastille bordée, légèrement
    inclinée, façon tampon de conformité de chantier. Réservé STRICTEMENT
    aux vrais éléments de confiance (badge_verification_pro(), engagement
    48h, garantie de remplacement) — jamais une décoration ailleurs, voir
    le plan de design validé le 28/08/2026.

    Renvoie du HTML brut (pas un st.markdown direct) : cet élément apparaît
    presque toujours EN LIGNE avec du texte (voir bandeau_confiance
    ci-dessous), pas seul sur sa propre ligne.

    Couleur en !important : la règle globale `.stApp span { color: ... !important }`
    (voir _CSS ci-dessus) l'emporterait sinon sur le style inline de ce
    span, quelle que soit sa spécificité — un style inline sans !important
    perd face à une règle de feuille de style avec !important."""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:0.4rem;'
        f'font-family:\'IBM Plex Mono\',monospace;font-size:0.78rem;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:#227A4E !important;'
        f'border:1.5px solid #227A4E;border-radius:999px;'
        f'padding:0.25rem 0.7rem 0.25rem 0.5rem;transform:rotate(-2deg);">'
        f'<span style="width:6px;height:6px;border-radius:50%;background:#227A4E;"></span>'
        f'{libelle}</span>'
    )


def bandeau_confiance(items: list[str]) -> None:
    """Bandeau horizontal de tampons de confiance (voir tampon_confiance) —
    placé juste sous le héros de la page d'accueil, jamais noyé au milieu
    du texte comme c'était le cas avant ce plan de design (l'engagement 48h
    était mentionné en simple st.caption, peu visible)."""
    pastilles = "".join(f'<div>{tampon_confiance(item)}</div>' for item in items)
    st.markdown(
        f'<div class="vitrine-trust-bar" style="display:flex;justify-content:center;'
        f'gap:2rem;flex-wrap:wrap;padding:1.2rem 0;margin:1.5rem 0;'
        f'border-top:1px solid #D6D0C5;border-bottom:1px solid #D6D0C5;">{pastilles}</div>',
        unsafe_allow_html=True,
    )


def carte_tarif(nom: str, prix_texte: str, sous_texte: str, mise_en_avant: bool = False) -> None:
    """Carte tarifaire — remplace le st.dataframe plat utilisé jusqu'ici
    pour la grille de prix (voir le plan de design du 28/08/2026 : "grille
    tarifaire déjà vue un peu plate"). Purement informationnel, comme
    l'ancien tableau (pas de bouton "Choisir" : aucun parcours self-service
    n'existe depuis cette page à ce jour, le tunnel réel démarre par
    ?vue=intake&lead_id=... envoyé par e-mail — voir afficher_presentation).

    mise_en_avant : liseré bleu cordeau plus épais, pour une formule à
    mettre en avant si besoin (aucune ne l'est par défaut aujourd'hui, ni
    Standard ni Moyen ne sont désignées "recommandées" dans
    Contenu_Site_Vitrine.md — ce paramètre existe pour un futur choix
    éditorial, pas utilisé pour l'instant).

    Couleurs en !important : voir docstring de tampon_confiance (même
    contournement de la règle globale `.stApp p { color: ... !important }`)."""
    bordure = "2px solid #1D4F8F" if mise_en_avant else "1px solid #D6D0C5"
    st.markdown(
        f'<div style="border:{bordure};background:#FFFFFF;padding:1.4rem 1.2rem;'
        f'text-align:center;height:100%;">'
        f'<p style="font-family:\'Fraunces\',Georgia,serif;font-weight:600;'
        f'font-size:1.15rem;color:#16283D !important;margin:0 0 0.6rem;">{nom}</p>'
        f'<p style="font-family:\'IBM Plex Mono\',monospace;font-variant-numeric:tabular-nums;'
        f'font-size:1.9rem;font-weight:500;color:#16283D !important;margin:0 0 0.3rem;">{prix_texte}</p>'
        f'<p style="font-size:0.85rem;color:#7C889A !important;margin:0;">{sous_texte}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )


def ligne_cordeau(label: str | None = None) -> None:
    """Élément signature du plan de design v2 (28/08/2026) — remplace
    st.divider() sur les pages vitrine. Reprend un geste concret du métier :
    le cordeau à tracer que l'artisan tend entre deux piquets et claque pour
    marquer un repère droit avant de couper. Rendu comme une ligne tendue
    avec un piquet (rond plein) à chaque extrémité, de petites graduations
    régulières le long du trait (façon mètre ruban), et un repère central
    optionnel — au lieu d'un simple <hr> générique qu'on trouve sur
    n'importe quel site.

    Seul élément signature de ce thème (voir docstring de module) : ne pas
    en multiplier les variantes, l'utiliser systématiquement là où
    st.divider() aurait été utilisé sur une page vitrine."""
    repere = (
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.72rem;'
        f'letter-spacing:0.05em;text-transform:uppercase;color:var(--gris-faint) !important;'
        f'white-space:nowrap;padding:0 0.2rem;">{label}</span>'
        if label else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.5rem;margin:1.8rem 0;">'
        f'<span style="width:7px;height:7px;border-radius:50%;background:var(--bleu-cordeau);'
        f'flex-shrink:0;"></span>'
        f'<span style="flex:1;height:2px;background:var(--bleu-cordeau);flex-shrink:0;"></span>'
        f'{repere}'
        f'<span style="flex:1;height:2px;background:var(--bleu-cordeau);flex-shrink:0;"></span>'
        f'<span style="width:7px;height:7px;border-radius:50%;background:var(--bleu-cordeau);'
        f'flex-shrink:0;"></span>'
        f'</div>',
        unsafe_allow_html=True,
    )
