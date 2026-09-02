// Récepteur webhook Stripe — SEUL rôle : vérifier la signature Stripe puis
// insérer l'événement brut dans stripe_webhook_events (voir
// sql/init_stripe_webhook_events.sql pour le pourquoi de cette architecture
// en deux temps : Streamlit Community Cloud, où tourne le dashboard, ne
// peut pas recevoir de requête HTTP entrante — c'est justement pour ça que
// l'ancien webhook avait été retiré avec le backend FastAPI, voir
// dashboard/contrats_signature.py). AUCUNE logique métier ici — pas de
// duplication en TypeScript de marquer_contrat_paye /
// marquer_demande_devis_payee_et_livree (dashboard/data_access.py) : le
// traitement réel est fait par scripts/traiter_paiements_stripe.py (cron
// GitHub Actions), qui lit cette table.
//
// Répond 200 dès l'insertion réussie (Stripe attend une réponse rapide,
// <10s) — le traitement métier est différé et découplé de ce délai.
//
// Abonner ce endpoint UNIQUEMENT à checkout.session.completed côté Stripe
// Dashboard (Developers > Webhooks) : c'est le seul événement dont
// scripts/traiter_paiements_stripe.py connaît le format (session.metadata
// contient contract_id OU demande_id selon le flux, voir
// dashboard/contrats_signature.py::creer_et_envoyer_lien_paiement et
// livraison_devis.py pour la création des Payment Links correspondants).
// Un autre type reçu ici est inséré avec statut='ignore', jamais traité.
//
// Déploiement : `supabase functions deploy stripe-webhook --no-verify-jwt`
// (Stripe n'envoie pas de JWT Supabase — voir supabase/config.toml).
// Secret requis : `supabase secrets set STRIPE_WEBHOOK_SECRET=whsec_...`
// (signing secret affiché par Stripe à la création du endpoint webhook,
// une fois l'URL de cette fonction connue). SUPABASE_URL et
// SUPABASE_SERVICE_ROLE_KEY sont fournis automatiquement par Supabase à
// toute Edge Function, aucune configuration supplémentaire nécessaire pour
// ceux-là.

import Stripe from "npm:stripe@17";
import { createClient } from "npm:@supabase/supabase-js@2";

const STRIPE_WEBHOOK_SECRET = Deno.env.get("STRIPE_WEBHOOK_SECRET") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

// apiVersion fixée explicitement : évite qu'une mise à jour silencieuse de
// la version par défaut du compte Stripe ne change la forme du payload reçu
// sans qu'on s'en aperçoive (même prudence que le reste du projet vis-à-vis
// des API externes, voir requirements.txt).
const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY") ?? "", {
  apiVersion: "2024-06-20",
  httpClient: Stripe.createFetchHttpClient(),
});

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

const TYPE_EVENEMENT_TRAITE = "checkout.session.completed";

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const signature = req.headers.get("stripe-signature");
  if (!signature) {
    return new Response("Signature manquante", { status: 400 });
  }

  const corpsBrut = await req.text();

  let event: Stripe.Event;
  try {
    // constructEventAsync (pas constructEvent) : la vérification HMAC
    // repose sur SubtleCrypto en environnement Deno/edge, pas le module
    // crypto Node — voir la doc Stripe pour les runtimes serverless.
    event = await stripe.webhooks.constructEventAsync(corpsBrut, signature, STRIPE_WEBHOOK_SECRET);
  } catch (err) {
    console.error(`Signature Stripe invalide : ${err instanceof Error ? err.message : err}`);
    return new Response("Signature invalide", { status: 400 });
  }

  const { error } = await supabase
    .from("stripe_webhook_events")
    .upsert(
      {
        stripe_event_id: event.id,
        type: event.type,
        payload: event,
        statut: event.type === TYPE_EVENEMENT_TRAITE ? "recu" : "ignore",
      },
      { onConflict: "stripe_event_id", ignoreDuplicates: true },
    );

  if (error) {
    // Erreur d'écriture Supabase : on répond 500 pour que Stripe RETENTE
    // plus tard (comportement standard Stripe sur un webhook en échec) —
    // ne jamais avaler silencieusement un événement de paiement.
    console.error(`Échec insertion stripe_webhook_events : ${error.message}`);
    return new Response("Erreur interne", { status: 500 });
  }

  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
