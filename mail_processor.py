import email
import imaplib
import logging
import os
import re
import unicodedata

import requests
from dotenv import load_dotenv
from supabase import create_client

from email_blacklist import blacklister_email

load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ZOHO_USER = os.getenv("ZOHO_USER", "")
ZOHO_PASSWORD = os.getenv("ZOHO_PASSWORD", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# Dossier IMAP optionnel vers lequel déplacer les bounces déjà traités
# (hard bounce, delay, ou indéterminé) une fois marqués lus, pour ne pas
# les laisser traîner dans la boîte de réception. Si vide, ils sont
# simplement marqués comme lus et restent dans "inbox" (comportement par
# défaut, aucune config supplémentaire requise côté Zoho).
ZOHO_DOSSIER_BOUNCES_TRAITES = os.getenv("ZOHO_DOSSIER_BOUNCES_TRAITES", "")
# URL publique de l'app Streamlit (ex: https://mon-app.streamlit.app), sous
# laquelle les pages "présentation"/"intake" (dashboard/pages_publiques.py)
# sont joignables par un artisan externe, via des liens de la forme
# "{PUBLIC_DASHBOARD_URL}/?vue=presentation&lead_id=..." (voir
# envoyer_suivi_positif ci-dessous).
PUBLIC_DASHBOARD_URL = os.getenv("PUBLIC_DASHBOARD_URL", "http://localhost:8501")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CRM] %(message)s")
log = logging.getLogger(__name__)

# Mots-clés de réponse POSITIVE. Volontairement larges : un simple "ok" ou
# "oui" doit être détecté (c'est l'exemple même donné pour ce pipeline), pas
# seulement des phrases explicites comme "je suis intéressé".
MOTS_POSITIFS = [
    "interesse", "disponible", "rendez-vous", "rdv", "tarif", "devis",
    "echange", "suite", "ok", "oui", "d'accord", "daccord", "banco",
    "ca marche", "ça marche", "allez-y", "vas-y", "partant", "yes",
    "go pour", "je veux bien", "avec plaisir", "pourquoi pas",
]

# Mots-clés de réponse NÉGATIVE / désinscription : à vérifier EN PREMIER
# (priorité sur les positifs) pour ne jamais recontacter quelqu'un qui a
# explicitement décliné ou demandé à être retiré.
MOTS_NEGATIFS = [
    "pas interesse", "pas intéressé", "non merci", "sans interet",
    "desinscri", "désinscri", "stop", "ne plus recevoir", "ne plus me contacter",
    "ne pas me contacter", "retirer de votre liste", "supprimer mes coordonnees",
    "supprimer mes coordonnées",
]

# Détection d'un message automatique de non-remise (bounce) — jamais une
# vraie réponse humaine, à vérifier AVANT toute détection de mot-clé
# positif/négatif (voir check_for_replies). Couvre explicitement les deux
# formulations vues en pratique : "Undelivered Mail Returned to Sender"
# (échec) ET "Delivery Status Notification (Delay)" (juste un délai, PAS
# un échec — voir analyser_dsn/traiter_bounce pour la distinction).
EXPEDITEURS_BOUNCE = ("mailer-daemon", "mail delivery subsystem", "postmaster", "mail delivery system")
SUJETS_BOUNCE = (
    "undelivered mail", "mail delivery failed", "delivery status notification",
    "returned mail", "failure notice", "delivery has failed", "non remis",
)


def normaliser(texte: str) -> str:
    """Minuscules + suppression des accents, pour une détection robuste aux
    variantes orthographiques ('intéressé' vs 'interesse')."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def contient_un_mot(texte_normalise: str, mots: list[str]) -> str | None:
    """Recherche en début de mot (limite de mot au début seulement, pas à la
    fin) : évite qu'un mot court comme 'ok' matche à l'intérieur d'un mot plus
    long sans rapport (ex: 'albat' dans 'albatros'), tout en acceptant les
    variantes conjuguées/dérivées d'un même radical (ex: 'desinscri' doit
    matcher 'désinscrire', 'désinscription', 'désinscrivez'...)."""
    for mot in mots:
        motif = r"\b" + re.escape(normaliser(mot))
        if re.search(motif, texte_normalise):
            return mot
    return None


def send_discord_alert(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL non configuré dans .env, alerte ignorée.")
        return
    try:
        data = {"content": f"🚨 **Alerte Lead Chaud !**\n{message}"}
        requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
    except requests.exceptions.RequestException as e:
        log.error(f"Erreur lors de l'envoi de l'alerte Discord : {e}")


def extraire_email(champ_from: str) -> str:
    return champ_from.split("<")[1].split(">")[0].strip() if "<" in champ_from else champ_from.strip()


def recuperer_lead_par_email(email_address: str) -> dict | None:
    try:
        reponse = supabase.table("leads").select("*").eq("email", email_address).limit(1).execute()
        return reponse.data[0] if reponse.data else None
    except Exception as e:
        log.error(f"Erreur Supabase lors de la récupération du lead {email_address} : {e}")
        return None


def update_lead_status(email_address: str, new_status: str) -> None:
    try:
        response = supabase.table("leads").update({"status": new_status}).eq("email", email_address).execute()
        if response.data:
            log.info(f"🔥 Lead {email_address} mis à jour avec le statut : '{new_status}'")
        else:
            log.warning(f"Lead non trouvé dans la table pour l'email : {email_address}")
    except Exception as e:
        log.error(f"Erreur Supabase : {e}")


def update_lead_professionnel_status(email_address: str, new_status: str) -> None:
    """Même mécanisme que update_lead_status, mais pour la table
    leads_professionnels du département outbound_chantiers (acteurs pro :
    architectes, promoteurs, maîtres d'œuvre). Un désabonnement doit
    s'appliquer aux DEUX pipelines de prospection, pas seulement à celui des
    artisans — sans quoi le mot-clé 'stop' d'un architecte serait ignoré."""
    try:
        response = (
            supabase.table("leads_professionnels")
            .update({"statut": new_status})
            .eq("email", email_address)
            .execute()
        )
        if response.data:
            log.info(f"🔥 Acteur pro {email_address} mis à jour avec le statut : '{new_status}'")
    except Exception as e:
        log.error(f"Erreur Supabase (leads_professionnels) : {e}")


def envoyer_suivi_positif(lead: dict) -> None:
    """Envoie automatiquement (sans intervention manuelle) la présentation
    asynchrone + le lien du formulaire d'intake dès qu'une réponse positive
    est détectée — remplace l'étape jusqu'ici manuelle ("je lui balance...").

    Les liens pointent vers l'app Streamlit (PUBLIC_DASHBOARD_URL), qui gère
    ces deux vues publiques via un paramètre d'URL (voir
    dashboard/app.py + dashboard/pages_publiques.py) plutôt que des routes
    HTTP dédiées — il n'y a plus de backend API séparé."""
    from ceo_agent import send_email_prospect  # import différé : évite un cycle au chargement du module

    lead_id = lead.get("id")
    company = lead.get("company") or "votre entreprise"
    to_email = lead.get("email")

    if not lead_id or not to_email:
        log.warning(f"Impossible d'envoyer le suivi automatique pour {company} : lead_id ou email manquant")
        return

    lien_presentation = f"{PUBLIC_DASHBOARD_URL}/?vue=presentation&lead_id={lead_id}"
    lien_intake = f"{PUBLIC_DASHBOARD_URL}/?vue=intake&lead_id={lead_id}"

    corps = (
        f"Bonjour,\n\n"
        f"Merci pour votre retour ! Voici la présentation détaillée de notre offre "
        f"clé en main pour {company} :\n{lien_presentation}\n\n"
        f"Pour qu'on puisse démarrer, merci de remplir ce court formulaire "
        f"(2 minutes, aucun appel nécessaire) :\n{lien_intake}\n\n"
        f"Dès réception, on prépare tout et on revient vers vous par email.\n\n"
        f"Cordialement"
    )

    succes = send_email_prospect(
        to_email, f"Votre projet {company} — présentation et prochaine étape", corps, lead_id=lead_id
    )
    if succes:
        log.info(f"Suivi automatique (présentation + intake) envoyé à {company} <{to_email}>")
        update_lead_status(to_email, "presentation_envoyee")
    else:
        log.error(f"Échec de l'envoi du suivi automatique à {company} <{to_email}>")


def est_message_bounce(sender_brut: str, subject: str) -> bool:
    """Vrai si ce message est une notification automatique de non-remise
    (bounce) — expéditeur mailer-daemon/postmaster ou sujet typique d'un
    rapport DSN. Jamais une vraie réponse humaine : à vérifier EN PREMIER
    dans check_for_replies(), avant toute détection de mot-clé
    positif/négatif (qui n'a aucun sens sur ce type de message)."""
    s = normaliser(sender_brut or "")
    suj = normaliser(subject or "")
    return any(m in s for m in EXPEDITEURS_BOUNCE) or any(m in suj for m in SUJETS_BOUNCE)


def analyser_dsn(msg: email.message.Message) -> dict | None:
    """Parse un rapport de non-remise structuré (DSN, RFC 3464 — le format
    standard généré par la quasi-totalité des serveurs mail, dont Zoho).
    Renvoie {'action', 'code_statut', 'destinataire', 'diagnostic'}, ou
    None si la partie 'message/delivery-status' est absente ou illisible —
    jamais une supposition sur un format non structuré : un bounce qu'on ne
    sait pas parser avec confiance reste sans action automatique plutôt que
    de risquer d'invalider le mauvais lead (voir traiter_bounce)."""
    if not msg.is_multipart():
        return None

    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue

        brut = part.get_payload(decode=True)
        texte = brut.decode(errors="ignore") if brut else str(part.get_payload())

        action = re.search(r"^Action:\s*(\w+)", texte, re.MULTILINE | re.IGNORECASE)
        statut = re.search(r"^Status:\s*([\d.]+)", texte, re.MULTILINE | re.IGNORECASE)
        # Final-Recipient est obligatoire par la RFC, Original-Recipient
        # optionnel (peut différer en cas de redirection/alias) — on
        # accepte les deux, Final-Recipient étant listé en premier donc
        # prioritaire si les deux sont présents.
        destinataire = re.search(
            r"^(?:Final|Original)-Recipient:\s*(?:rfc822;)?\s*<?([^\s>]+@[^\s>]+)",
            texte, re.MULTILINE | re.IGNORECASE,
        )
        diagnostic = re.search(r"^Diagnostic-Code:\s*(.+)$", texte, re.MULTILINE | re.IGNORECASE)

        return {
            "action": action.group(1).lower() if action else None,
            "code_statut": statut.group(1) if statut else None,
            "destinataire": destinataire.group(1).strip().rstrip(".,;") if destinataire else None,
            "diagnostic": diagnostic.group(1).strip() if diagnostic else "",
        }

    return None


def _texte_complet_bounce(msg: email.message.Message) -> str:
    """Concatène tout le texte exploitable du message (sujet + chaque
    partie text/plain, message/delivery-status, message/rfc822) — sert de
    matière première aux replis ci-dessous quand la structure DSN stricte
    (RFC 3464) n'est pas respectée : observé en pratique, certains bounces
    Zoho n'exposent pas de partie message/delivery-status correctement
    formée, ou omettent purement et simplement Action:/Status:. Une partie
    message/rfc822 (copie de l'e-mail original en échec, souvent incluse
    dans un DSN) est précieuse ici : son en-tête 'To:' est justement
    l'adresse du destinataire visé."""
    morceaux = [msg.get("Subject") or ""]
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() not in ("text/plain", "message/delivery-status", "message/rfc822"):
                continue
            brut = part.get_payload(decode=True)
            morceaux.append(brut.decode(errors="ignore") if brut else str(part.get_payload()))
    else:
        brut = msg.get_payload(decode=True)
        if brut:
            morceaux.append(brut.decode(errors="ignore"))
    return "\n".join(morceaux)


# Adresses techniques jamais retenues comme "destinataire en échec" — elles
# apparaissent presque systématiquement quelque part dans un bounce (dans
# l'en-tête From du rapport lui-même, ou en pied de page), sans jamais être
# la vraie cible.
PREFIXES_ADRESSES_TECHNIQUES = ("mailer-daemon", "postmaster", "abuse@", "noreply@", "no-reply@")

MOTIF_EMAIL_GENERIQUE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def extraire_destinataire_repli(texte: str, adresse_envoi: str) -> str | None:
    """Repli quand analyser_dsn() n'a pas trouvé de destinataire dans une
    partie MIME strictement formée — cherche dans TOUT le texte disponible
    (sujet, corps, copie de l'original), du signal le plus fiable au moins
    fiable :
    1. En-têtes DSN présents en texte libre (hors partie MIME dédiée) ;
    2. En-tête 'To:' d'une copie de l'e-mail original incluse dans le
       rapport (message/rfc822) — l'adresse qu'on a nous-mêmes visée ;
    3. Formulations humaines fréquentes ('failed to deliver to X',
       'X: 550 ...') ;
    4. Dernier recours : première adresse du texte qui n'est ni notre
       propre boîte d'envoi, ni une adresse technique connue.
    Renvoie None si rien de plausible n'est trouvé — jamais une adresse
    inventée."""
    match = re.search(
        r"(?:Final|Original)-Recipient:\s*(?:rfc822;)?\s*<?([^\s>,]+@[^\s>,]+)",
        texte, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().rstrip(".,;")

    match = re.search(r"^To:\s*(?:\"[^\"]*\"\s*)?<?([^\s>,]+@[^\s>,]+)>?\s*$", texte, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(".,;")

    for motif in (
        r"(?:delivery (?:to|of your message to)|failed to deliver to|"
        r"could not be delivered to|undelivered to|n'a pas pu être livré à)\s*:?\s*<?([^\s>,]+@[^\s>,]+)",
        r"<?([^\s>,]+@[^\s>,]+)>?\s*:\s*(?:550|551|553|554|5\.\d\.\d)",
    ):
        match = re.search(motif, texte, re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(".,;")

    adresse_envoi_normalisee = (adresse_envoi or "").strip().lower()
    for candidate in MOTIF_EMAIL_GENERIQUE.findall(texte):
        c = candidate.strip().lower()
        if c == adresse_envoi_normalisee:
            continue
        if any(c.startswith(p) for p in PREFIXES_ADRESSES_TECHNIQUES):
            continue
        return candidate.strip().rstrip(".,;")

    return None


CODES_SMTP_DEFINITIFS = re.compile(r"\b(550|551|553|554)\b|\b5\.\d\.\d\b")
CODES_SMTP_TEMPORAIRES = re.compile(r"\b(421|450|451|452)\b|\b4\.\d\.\d\b")

MOTS_ECHEC_DEFINITIF = (
    "user unknown", "no such user", "does not exist", "mailbox not found",
    "mailbox unavailable", "recipient rejected", "address rejected",
    "invalid recipient", "unknown recipient", "undeliverable",
    "n'existe pas", "boite introuvable", "utilisateur inconnu",
    # Formulations plus vagues mais tout aussi définitives, très courantes
    # en pratique (y compris les deux exemples exacts rapportés en usage
    # réel : "Undelivered Mail Returned to Sender").
    "could not be delivered", "was not delivered", "delivery failed",
    "delivery has failed", "permanently failed", "returned to sender",
    "undelivered mail",
)
MOTS_ECHEC_TEMPORAIRE = (
    "will keep trying", "delayed", "temporarily deferred", "try again later",
    "greylist", "mailbox full", "quota exceeded", "temporary failure",
    "deferred", "sera retente", "nouvelle tentative",
    # "(Delay)" est le qualificatif exact qui distingue un simple délai
    # (encore en cours de tentative) d'un échec définitif dans les rapports
    # DSN Zoho observés en usage réel — à vérifier explicitement, sans quoi
    # "delivery status notification" seul ne permet pas de trancher (les
    # deux variantes, délai et échec, partagent ce même sujet générique).
    "(delay)", "notification (delay)",
)


def deviner_gravite_repli(texte: str) -> str | None:
    """Repli quand ni Action: ni Status: n'ont été trouvés dans une partie
    DSN structurée : déduit 'failed' (définitif) ou 'delayed' (temporaire)
    du contenu libre du message. Renvoie None si le texte contient des
    signaux contradictoires ou aucun signal net — mieux vaut ne rien faire
    que de deviner à tort et invalider un lead valide."""
    texte_normalise = normaliser(texte)
    a_code_definitif = bool(CODES_SMTP_DEFINITIFS.search(texte))
    a_code_temporaire = bool(CODES_SMTP_TEMPORAIRES.search(texte))
    a_mot_definitif = any(m in texte_normalise for m in MOTS_ECHEC_DEFINITIF)
    a_mot_temporaire = any(m in texte_normalise for m in MOTS_ECHEC_TEMPORAIRE)

    if (a_code_definitif or a_mot_definitif) and not (a_code_temporaire or a_mot_temporaire):
        return "failed"
    if (a_code_temporaire or a_mot_temporaire) and not (a_code_definitif or a_mot_definitif):
        return "delayed"
    return None


def invalider_lead(email_address: str) -> None:
    """Marque le lead correspondant 'invalide' — artisan OU acteur pro, les
    deux tentées (même logique que le traitement d'un 'decline' plus haut,
    on ne sait pas a priori de quel pipeline vient l'adresse). Un lead
    'invalide' n'est plus jamais sélectionné : get_leads_from_supabase()
    filtre sur contacted=False (déjà True à ce stade) et
    recuperer_prospects_a_relancer()/lancer_relances() filtrent sur
    status/statut='contacte_attente_reponse', jamais 'invalide'."""
    update_lead_status(email_address, "invalide")
    update_lead_professionnel_status(email_address, "invalide")


def traiter_bounce(sender_brut: str, subject: str, msg: email.message.Message) -> str:
    """Traite un message identifié comme bounce (voir est_message_bounce) :
    seul un HARD bounce confirmé (Action: failed ou code 5.x.x, y compris
    déduit par repli sur texte libre — voir extraire_destinataire_repli/
    deviner_gravite_repli) invalide le lead et alimente la blacklist
    durable. Un simple délai (Action: delayed ou code 4.x.x, ex: 'Delivery
    Status Notification (Delay)') ne déclenche AUCUNE action métier : le
    serveur distant retente encore, ce n'est pas un échec définitif.

    La structure DSN stricte (RFC 3464) sert de source PRIORITAIRE (la plus
    fiable), mais n'est pas toujours respectée en pratique par Zoho — d'où
    les replis en texte libre à chaque étape plutôt qu'un simple abandon
    quand la partie message/delivery-status est absente ou incomplète.

    Renvoie un statut ('hard_bounce', 'delayed', 'indetermine' ou
    'sans_destinataire') utilisé par check_for_replies() pour décider du
    sort IMAP du message (marquage lu / déplacement) : dans tous les cas un
    bounce est 100% automatique, jamais une réponse humaine à laisser traîner
    non lue dans la boîte de réception."""
    dsn = analyser_dsn(msg)
    texte_complet = _texte_complet_bounce(msg)

    destinataire = dsn["destinataire"] if dsn else None
    action = dsn["action"] if dsn else None
    code_statut = (dsn["code_statut"] if dsn else None) or ""
    diagnostic = (dsn["diagnostic"] if dsn else None) or ""

    if not destinataire:
        destinataire = extraire_destinataire_repli(texte_complet, ZOHO_USER)
        if destinataire:
            log.info(f"Destinataire du bounce retrouvé par repli (texte libre, pas de DSN structuré exploitable) : {destinataire}")

    if not destinataire:
        log.warning(
            f"Bounce sans destinataire identifiable, même après repli texte libre "
            f"(expéditeur={sender_brut!r}, sujet={subject!r}) — aucune action métier, à vérifier manuellement."
        )
        return "sans_destinataire"

    if not action and not code_statut:
        action = deviner_gravite_repli(texte_complet)
        if action:
            log.info(f"Gravité du bounce déduite du texte libre pour {destinataire} : {action}")

    if action == "failed" or code_statut.startswith("5"):
        log.warning(f"❌ Hard bounce confirmé pour {destinataire} (statut {code_statut or '?'}) — {diagnostic or texte_complet[:200]!r}")
        invalider_lead(destinataire)
        blacklister_email(
            destinataire, raison="hard_bounce",
            code_statut=code_statut or None, diagnostic=diagnostic or texte_complet[:500],
        )
        return "hard_bounce"
    elif action == "delayed" or code_statut.startswith("4"):
        log.info(f"⏳ Délai de livraison pour {destinataire} (statut {code_statut or '?'}) — le serveur retente encore, aucune action métier.")
        return "delayed"
    else:
        log.warning(f"Bounce de nature indéterminée pour {destinataire} (action={action}, statut={code_statut!r}) — aucune action métier automatique.")
        return "indetermine"


def marquer_message_traite(mail: imaplib.IMAP4_SSL, num: bytes, dossier_archive: str = "") -> None:
    """Marque un message IMAP comme lu (\\Seen) une fois son traitement
    métier terminé, pour ne pas laisser les notifications 100% automatiques
    (bounces) s'accumuler comme non lues dans la boîte Zoho. Si
    `dossier_archive` est fourni, tente en plus de déplacer le message hors
    de la boîte de réception (COPY + marquage \\Deleted ; l'EXPUNGE final
    est fait une seule fois par check_for_replies() après la boucle, pour ne
    pas décaler les numéros de séquence des messages restant à traiter)."""
    try:
        mail.store(num, "+FLAGS", "\\Seen")
    except Exception as e:
        log.error(f"Impossible de marquer le message {num!r} comme lu : {e}")
        return

    if not dossier_archive:
        return

    try:
        typ, _ = mail.copy(num, dossier_archive)
        if typ == "OK":
            mail.store(num, "+FLAGS", "\\Deleted")
        else:
            log.warning(f"Échec de la copie du message {num!r} vers '{dossier_archive}' ({typ}) — message laissé dans inbox (mais marqué lu).")
    except Exception as e:
        log.error(f"Erreur lors du déplacement du message {num!r} vers '{dossier_archive}' : {e}")


def check_for_replies() -> None:
    if not ZOHO_USER or not ZOHO_PASSWORD:
        log.error("Identifiants Zoho manquants dans .env")
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.zoho.eu", 993)
        mail.login(ZOHO_USER, ZOHO_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        email_ids = messages[0].split()

        if not email_ids:
            log.info("📬 Aucun nouveau message non lu.")
            mail.logout()
            return

        for num in email_ids:
            _, msg_data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender_brut = msg.get("From") or ""
            sender = extraire_email(sender_brut)
            subject = msg.get("Subject") or ""

            # Vérifié EN PREMIER, avant toute détection de mot-clé : un
            # bounce n'est jamais une vraie réponse humaine, et son
            # expéditeur (mailer-daemon) n'est de toute façon jamais
            # l'adresse du lead concerné (voir traiter_bounce, qui extrait
            # la vraie adresse depuis le contenu du rapport DSN).
            if est_message_bounce(sender_brut, subject):
                statut_bounce = traiter_bounce(sender_brut, subject, msg)
                # Un bounce est toujours 100% automatique (jamais une
                # réponse humaine à laisser en attente) : on le marque lu
                # dans tous les cas, et on le déplace en plus hors de
                # l'inbox si ZOHO_DOSSIER_BOUNCES_TRAITES est configuré —
                # objectif : ne plus le voir s'accumuler par centaines.
                marquer_message_traite(mail, num, ZOHO_DOSSIER_BOUNCES_TRAITES)
                log.info(f"📭 Bounce ({statut_bounce}) marqué comme lu.")
                continue

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            texte_normalise = normaliser(body)

            mot_negatif = contient_un_mot(texte_normalise, MOTS_NEGATIFS)
            if mot_negatif:
                log.info(f"🛑 Refus/désinscription détecté de {sender} (mot-clé : {mot_negatif!r})")
                update_lead_status(sender, "decline")
                update_lead_professionnel_status(sender, "decline")
                continue

            mot_positif = contient_un_mot(texte_normalise, MOTS_POSITIFS)
            if mot_positif:
                log.info(f"🎯 Réponse positive détectée de {sender} (mot-clé : {mot_positif!r})")
                update_lead_status(sender, "interested")
                update_lead_professionnel_status(sender, "interested")
                send_discord_alert(f"Le prospect {sender} est intéressé par vos services !")

                lead = recuperer_lead_par_email(sender)
                if lead:
                    envoyer_suivi_positif(lead)
                else:
                    log.warning(f"Lead introuvable en base pour {sender}, suivi automatique non envoyé")
            else:
                log.info(f"Message de {sender} reçu (aucun mot-clé détecté).")

        # EXPUNGE unique après la boucle (et non message par message) :
        # supprimer un message en cours d'itération décalerait les numéros
        # de séquence IMAP des messages suivants encore à traiter.
        if ZOHO_DOSSIER_BOUNCES_TRAITES:
            mail.expunge()

        mail.logout()
    except Exception as e:
        log.error(f"Erreur lors du scan : {e}")


if __name__ == "__main__":
    check_for_replies()
