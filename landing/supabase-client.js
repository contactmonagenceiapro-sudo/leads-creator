/**
 * Client Supabase partagé par les pages de l'espace Artisans
 * (artisan-inscription.html, artisan-connexion.html,
 * artisan-tableau-de-bord.html).
 *
 * Clé "anon" publique par design (rôle "anon" dans le JWT) : sans risque à
 * exposer côté navigateur. La vraie barrière de sécurité est le Row Level
 * Security appliqué côté Supabase sur la table `artisans` (voir
 * sql/init_artisans.sql), pas ce secret ni les redirections JS ci-dessous.
 */
const SUPABASE_URL = "https://iijvonnzanbvhwvexvzm.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlpanZvbm56YW5idmh3dmV4dnptIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAwNzYyMTUsImV4cCI6MjA5NTY1MjIxNX0._xuT1HRHBipUB-TGtdi7AT3tTzZywZibhlpNjimeE88";

const supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

/**
 * Traduit les messages d'erreur Supabase Auth les plus courants en
 * français. Retombe sur le message original (anglais) si non reconnu,
 * plutôt que d'afficher un message générique inutile.
 */
function traduireErreurAuth(error) {
    const message = error && error.message ? error.message : "Une erreur inconnue est survenue.";
    const correspondances = {
        "User already registered": "Un compte existe déjà avec cet e-mail.",
        "Invalid login credentials": "E-mail ou mot de passe incorrect.",
        "Email not confirmed": "Confirme d'abord ton adresse e-mail (vérifie ta boîte mail).",
        "Password should be at least 6 characters": "Le mot de passe doit contenir au moins 6 caractères.",
    };
    return correspondances[message] || message;
}

/**
 * À appeler en haut de toute page protégée (ex: artisan-tableau-de-bord.html).
 * Redirige vers la connexion si personne n'est authentifié ; renvoie la
 * session sinon. Confort UX uniquement (évite d'afficher une coquille vide
 * le temps de la vérification) — ne remplace pas le RLS Supabase, qui reste
 * la seule barrière de sécurité réelle sur les données.
 */
async function exigerConnexionArtisan() {
    const { data: { session } } = await supabaseClient.auth.getSession();
    if (!session) {
        window.location.href = "artisan-connexion.html";
        return null;
    }
    return session;
}

/**
 * Redirige immédiatement vers la connexion si la session se termine
 * pendant la consultation d'une page protégée (jeton expiré, déconnexion
 * déclenchée depuis un autre onglet...).
 */
function surveillerSessionArtisan() {
    supabaseClient.auth.onAuthStateChange((_event, session) => {
        if (!session) {
            window.location.href = "artisan-connexion.html";
        }
    });
}
