"""
Interface admin "Performance artisans" — module 9 de pilotage. Par artisan
(formule à l'unité uniquement — la formule abonnement livre directement,
aucune notion de refus/expiration) : propositions expirées sans action
(table propositions_expirees, voir sql/init_propositions_expirees.sql et
livraison_devis.py::expirer_propositions_perimees), livraisons payées, taux
de réactivité déduit, délai moyen de paiement.

Aucune alerte automatique : ce module sert à repérer manuellement un
artisan structurellement peu réactif (candidat à retirer ou recontacter),
pas un incident nécessitant une réaction immédiate.

Espace admin uniquement.
"""

import pandas as pd
import streamlit as st

from common import safe_call
from data_access import get_performance_artisans

st.title("🛠️ Performance des artisans")
st.caption(
    "Formule à l'unité uniquement — la formule abonnement livre directement (voir "
    "livraison_devis.py::_livrer_directement), aucune notion de refus/expiration possible."
)

resultat, erreur = safe_call(get_performance_artisans)
if erreur:
    st.error(f"Impossible de charger la performance des artisans : {erreur}")
    st.stop()

artisans = resultat["artisans"]
if not artisans:
    st.info("Aucune donnée pour l'instant (aucune proposition à l'unité expirée ou livrée).")
    st.stop()

df = pd.DataFrame([
    {
        "Artisan": a["nom"],
        "Propositions expirées": a["propositions_expirees"],
        "Livraisons payées": a["livraisons_payees"],
        "Taux de réactivité": f"{a['taux_reactivite'] * 100:.0f} %",
        "Délai moyen de paiement": f"{a['delai_paiement_moyen_heures']:.1f} h" if a["delai_paiement_moyen_heures"] is not None else "—",
    }
    for a in artisans
])
st.dataframe(df, use_container_width=True, hide_index=True)

peu_reactifs = [a for a in artisans if a["propositions_expirees"] >= 3 and a["taux_reactivite"] < 0.5]
if peu_reactifs:
    st.warning(
        f"⚠️ {len(peu_reactifs)} artisan(s) avec 3 propositions expirées ou plus et un taux de "
        "réactivité sous 50 % — candidats à recontacter ou retirer du round-robin."
    )
