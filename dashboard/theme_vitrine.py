"""
CSS + composants centralisés du thème "Chantier Ouvert" — habille le site
vitrine public et le formulaire de demande de devis (dashboard/pages_publiques.py)
d'une identité claire, distincte du thème sombre "Plan de Chantier" de
l'admin (voir dashboard/theme.py) — plan de design validé le 28/08/2026.

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
acquis.
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
    --orange-chantier: #E56A28;
    --orange-chantier-dim: #C9581E;
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
.stApp a { color: var(--orange-chantier-dim) !important; }
.stApp hr { border-color: var(--beton-line) !important; }

/* -----------------------------------------------------------------
   Boutons — orange réservé au primaire (texte foncé, jamais blanc :
   contraste vérifié à 4,6:1, voir le plan de design), le reste en contour.
------------------------------------------------------------------ */
.stApp button[kind="primary"], .stApp button[data-testid="baseButton-primary"] {
    background: var(--orange-chantier) !important;
    color: var(--bleu-plan) !important;
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
    background: var(--orange-chantier) !important;
    color: var(--bleu-plan) !important;
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
   Champs de formulaire — bordure visible, focus orange (accessibilité :
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
    outline: 2px solid var(--orange-chantier-dim) !important;
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
   Mobile — hero et bandeau de confiance restent lisibles, pas de texte
   trop serré sur petit écran.
------------------------------------------------------------------ */
@media (max-width: 640px) {
    .stApp h1 { font-size: 1.9rem !important; }
    .vitrine-trust-bar { flex-direction: column !important; gap: 0.8rem !important; }
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
    ci-dessous), pas seul sur sa propre ligne."""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:0.4rem;'
        f'font-family:\'IBM Plex Mono\',monospace;font-size:0.78rem;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:#227A4E;'
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

    mise_en_avant : liseré orange plus épais, pour une formule à mettre en
    avant si besoin (aucune ne l'est par défaut aujourd'hui, ni Standard ni
    Moyen ne sont désignées "recommandées" dans Contenu_Site_Vitrine.md —
    ce paramètre existe pour un futur choix éditorial, pas utilisé pour
    l'instant)."""
    bordure = "2px solid #E56A28" if mise_en_avant else "1px solid #D6D0C5"
    st.markdown(
        f'<div style="border:{bordure};background:#FFFFFF;padding:1.4rem 1.2rem;'
        f'text-align:center;height:100%;">'
        f'<p style="font-family:\'Fraunces\',Georgia,serif;font-weight:600;'
        f'font-size:1.15rem;color:#16283D;margin:0 0 0.6rem;">{nom}</p>'
        f'<p style="font-family:\'IBM Plex Mono\',monospace;font-variant-numeric:tabular-nums;'
        f'font-size:1.9rem;font-weight:500;color:#16283D;margin:0 0 0.3rem;">{prix_texte}</p>'
        f'<p style="font-size:0.85rem;color:#7C889A;margin:0;">{sous_texte}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )
