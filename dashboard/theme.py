"""
CSS centralisé du thème "Plan de Chantier" — complète .streamlit/config.toml
(couleurs, polices, rayons de bordure : tout ce que le thème natif Streamlit
sait déjà faire, voir ce fichier pour la palette documentée) avec les
quelques détails que le thème natif ne couvre pas : repères d'angle sur les
cartes (élément signature du plan de design validé le 27/08/2026), liseré de
l'item de navigation actif, focus clavier renforcé.

Point d'injection UNIQUE : appelé une fois depuis dashboard/app.py, juste
après auth.exiger_connexion() (donc jamais sur le site vitrine public,
dashboard/pages_publiques.py, qui fait st.stop() avant ce point — voir
app.py). Toute page qui rappellerait injecter_theme() dupliquerait juste le
même <style>, sans casser quoi que ce soit (CSS idempotent), mais c'est
inutile : ne PAS l'appeler depuis dashboard/app_pages/*.py.

Sélecteurs `[data-testid=...]` : Streamlit s'engage à garder ces attributs
stables spécifiquement pour permettre du CSS personnalisé (contrat public de
theming, documenté par Streamlit) — contrairement aux classes générées
(hashées, changent à chaque version). Certains sélecteurs ci-dessous
(navigation active, notamment) n'ont pas pu être vérifiés dans un vrai
navigateur (aucun outil Playwright/navigateur dans cet environnement, voir
mémoire projet) : plusieurs sélecteurs de repli sont empilés pour chaque
usage incertain, sans risque puisqu'un sélecteur CSS qui ne matche rien est
simplement ignoré — mais un contrôle visuel réel (capture d'écran ou test
manuel) reste nécessaire avant de considérer le rendu final acquis.
"""

import streamlit as st

_CSS = """
<style>
:root {
    /* Miroir de .streamlit/config.toml — mêmes valeurs, mêmes noms. Deux
       fichiers séparés (le thème natif Streamlit ne s'auto-expose pas en
       variables CSS), donc rappelées ici pour que ce fichier reste
       lisible/maintenable sans redonder les hex bruts partout ci-dessous. */
    --orange-chantier: #E8631C;
    --st-border-color: #2B415A;
}

/* -----------------------------------------------------------------
   Titres de page/section (st.title, st.header, st.subheader) : allure
   "signalétique de chantier" — capitales + espacement des lettres. Restreint
   à h1-h3 volontairement (st.title/header/subheader) : les sous-titres plus
   fins (st.markdown("#### ...") -> h4, très fréquents pour des libellés de
   bloc dans ce dashboard) restent en casse normale pour ne pas "crier" sur
   chaque petite section — voir le plan de design, principe de retenue.
------------------------------------------------------------------ */
h1, h2, h3 {
    text-transform: uppercase;
    letter-spacing: 0.02em;
}

/* -----------------------------------------------------------------
   Repères d'angle — élément signature du plan de design. Deux traits en L,
   façon amorces de recadrage d'un plan technique, sur les conteneurs à
   bordure (st.container(border=True)) et les cartes de métrique
   (st.metric) : désigne les blocs d'information qui comptent (un chiffre à
   retenir, un bloc d'action), jamais un conteneur purement décoratif.
   Discret (2px, couleur d'accent unique), jamais animé.
------------------------------------------------------------------ */
div[data-testid="stVerticalBlockBorderWrapper"],
div[data-testid="stMetric"] {
    position: relative;
}
div[data-testid="stVerticalBlockBorderWrapper"]::before,
div[data-testid="stVerticalBlockBorderWrapper"]::after,
div[data-testid="stMetric"]::before,
div[data-testid="stMetric"]::after {
    content: "";
    position: absolute;
    width: 10px;
    height: 10px;
    border-color: var(--orange-chantier, #E8631C);
    border-style: solid;
    border-width: 0;
    pointer-events: none;
}
div[data-testid="stVerticalBlockBorderWrapper"]::before,
div[data-testid="stMetric"]::before {
    top: -1px;
    left: -1px;
    border-top-width: 2px;
    border-left-width: 2px;
}
div[data-testid="stVerticalBlockBorderWrapper"]::after,
div[data-testid="stMetric"]::after {
    bottom: -1px;
    right: -1px;
    border-bottom-width: 2px;
    border-right-width: 2px;
}
/* st.metric n'a pas de bordure par défaut (contrairement à
   st.container(border=True)) : sans un léger cadre, les repères d'angle
   flotteraient dans le vide. Ajouté seulement ici, pas sur les conteneurs
   à bordure qui ont déjà la leur via showWidgetBorder (config.toml). */
div[data-testid="stMetric"] {
    padding: 0.9rem 1.1rem;
    border: 1px solid var(--st-border-color, #2B415A);
}

/* -----------------------------------------------------------------
   Navigation — item actif marqué par un liseré (pas un aplat), cohérent
   avec le principe "l'orange reste réservé, jamais dispersé" du plan de
   design. Sélecteurs empilés (non vérifiés en navigateur réel, voir
   docstring du module) : aria-current="page" est la piste la plus fiable
   (attribut d'accessibilité standard), les data-testid sont un repli.
------------------------------------------------------------------ */
section[data-testid="stSidebar"] a[aria-current="page"],
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"],
section[data-testid="stSidebar"] li[aria-current="page"] a {
    border-left: 2px solid var(--orange-chantier, #E8631C);
    background: rgba(232, 99, 28, 0.08);
}
section[data-testid="stSidebar"] a,
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
    border-left: 2px solid transparent;
}

/* -----------------------------------------------------------------
   Focus clavier — renforcé au-delà du défaut Streamlit, sur TOUT élément
   interactif (accessibilité, exigée explicitement par le chantier design).
------------------------------------------------------------------ */
button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
select:focus-visible,
[tabindex]:focus-visible {
    outline: 2px solid var(--orange-chantier, #E8631C) !important;
    outline-offset: 2px !important;
}

/* -----------------------------------------------------------------
   Chiffres tabulaires dans les tableaux/métriques — alignement propre des
   colonnes numériques, cohérent avec codeFont (IBM Plex Mono) déjà réglé
   en config.toml pour ces zones.
------------------------------------------------------------------ */
div[data-testid="stMetricValue"] {
    font-variant-numeric: tabular-nums;
}

/* -----------------------------------------------------------------
   Mobile — aucune perte de lisibilité sur petit écran : capitales moins
   espacées (les majuscules serrées deviennent difficiles à lire en dessous
   d'une certaine taille), repères d'angle légèrement réduits pour ne pas
   dominer des cartes déjà compactes en une colonne.
------------------------------------------------------------------ */
@media (max-width: 640px) {
    h1, h2, h3 {
        letter-spacing: 0.01em;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]::before,
    div[data-testid="stVerticalBlockBorderWrapper"]::after,
    div[data-testid="stMetric"]::before,
    div[data-testid="stMetric"]::after {
        width: 7px;
        height: 7px;
    }
}

/* -----------------------------------------------------------------
   Respect du système : aucune animation ajoutée par ce thème, mais toute
   transition native de Streamlit (hover, etc.) est neutralisée si
   l'utilisateur a demandé de réduire les animations.
------------------------------------------------------------------ */
@media (prefers-reduced-motion: reduce) {
    * {
        transition: none !important;
        animation: none !important;
    }
}
</style>
"""


def injecter_theme() -> None:
    """Injecte le CSS complémentaire du thème "Plan de Chantier" — à appeler
    UNE SEULE FOIS, depuis dashboard/app.py juste après exiger_connexion().
    Voir la docstring du module pour la justification complète."""
    st.markdown(_CSS, unsafe_allow_html=True)
