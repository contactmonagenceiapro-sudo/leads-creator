# Workflow n8n — Département "Outbound & Génération de Chantiers"

## Pourquoi orchestrer depuis n8n plutôt que d'enchaîner des appels HTTP

L'endpoint `/agent/trigger` de l'API (comme les actions existantes
`scraping`/`leads`) lance les scripts via `subprocess.Popen` : l'appel HTTP
répond immédiatement, **avant** que le script ait fini de tourner. Chaîner
plusieurs appels HTTP successifs dans n8n (sourcing puis enrichissement puis
scoring) sans savoir combien de temps prend chaque étape créerait une
course : l'étape 2 démarrerait avant que l'étape 1 ait écrit son fichier de
sortie.

C'est pour ça que `pipeline_outbound_chantiers.py` exécute les modules 1 à 3
**en séquence, dans un seul processus bloquant** — n8n n'a besoin que d'un
seul appel HTTP par cycle, sans deviner de délai.

## Structure du workflow (2 workflows n8n séparés)

### Workflow A — "Sourcing hebdomadaire"

```
[Schedule Trigger]              [HTTP Request]                [Discord/Slack]
  Cron : chaque lundi    ──▶     POST {API_URL}/agent/trigger   ──▶  notifie fin de run
  06h00                          body: {"action": "pipeline_pro"}
                                 header: X-API-Key
```

- **Schedule Trigger** : expression cron `0 6 * * 1` (hebdomadaire — le
  volume de nouveaux permis/acteurs ne justifie pas une fréquence plus
  élevée ; à ajuster après un premier mois d'observation).
- **HTTP Request** : `POST`, URL `{{ $env.API_URL }}/agent/trigger`, body
  JSON `{"action": "pipeline_pro"}`, header `X-API-Key`.
- **Nœud de notification (optionnel)** : un simple appel webhook Discord
  pour confirmer que le run a été déclenché (le suivi détaillé reste dans
  les logs `docker logs ai_api`, cf. guide_commandes_complet.md).

### Workflow B — "Campagne + relances quotidiennes"

```
[Schedule Trigger]              [HTTP Request]
  Cron : tous les jours  ──▶     POST {API_URL}/agent/trigger
  09h00                          body: {"action": "envoi_pro"}
```

- Tourne tous les jours : `lancer_campagne_initiale()` contacte les
  nouveaux acteurs jamais sollicités, `lancer_relances()` vérifie qui doit
  recevoir sa relance à J+3/J+7 — les deux fonctions gèrent déjà en interne
  le fait qu'il n'y ait rien à faire ce jour-là (aucune erreur si aucun
  acteur n'est éligible).

## Nœud de scoring/dispatch alternatif (mode webhook direct)

Si, plus tard, une source de leads professionnels arrive directement dans
n8n via un **Webhook** (par exemple un formulaire partenaire) plutôt que
par le pipeline Python, voici l'équivalent en **Code node** JavaScript,
qui reproduit exactement la formule de `scorer_et_publier.py` :

```javascript
// n8n Code node — scoring + dispatch des acteurs professionnels
// (équivalent JS de outbound_chantiers/scorer_et_publier.py)

const POIDS_TYPE_ACTEUR = { architecte: 1.0, promoteur: 0.9, maitre_oeuvre: 0.7 };
const POIDS_ACTIVITE_CHANTIERS = 0.5;
const SEUIL_PRIORITAIRE = 0.75;

return $input.all().map((item) => {
  const acteur = item.json;

  const poidsType = POIDS_TYPE_ACTEUR[acteur.type_acteur] ?? 0.5;
  const scoreActivite = acteur.score_activite_chantiers ?? 0.5; // signal agrégé, jamais nominatif

  const scoreFinal = Math.min(
    poidsType * (1 - POIDS_ACTIVITE_CHANTIERS) + scoreActivite * POIDS_ACTIVITE_CHANTIERS,
    1.0
  );

  return {
    json: {
      ...acteur,
      score_final: Math.round(scoreFinal * 1000) / 1000,
      file_prioritaire: scoreFinal >= SEUIL_PRIORITAIRE,
    },
  };
});
```

Brancher un nœud **IF** derrière (`{{ $json.file_prioritaire }} === true`) :
branche "vrai" → template d'e-mail prioritaire (envoi immédiat) ; branche
"faux" → file d'attente standard (envoi groupé la nuit suivante).

## Variables d'environnement à déclarer dans n8n

`API_URL` et `API_SECRET_KEY` (Settings > Variables, ou credentials n8n
dédiées) — jamais en dur dans un nœud, pour rester cohérent avec le reste
du projet (`.env` unique source de vérité).
