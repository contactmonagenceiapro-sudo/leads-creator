"""
email_validator.py
===================

Filtre de qualité des emails, à appliquer AVANT tout enregistrement
Supabase ou toute tentative d'envoi — vient compléter email_blacklist.py
(qui protège contre un email déjà VU en hard bounce) par une protection
contre un email JAMAIS vu mais structurellement à risque : format
invalide, domaine de messagerie jetable/temporaire connu, ou domaine sans
aucun enregistrement MX (donc structurellement incapable de recevoir un
email, qu'il "ressemble" à une adresse valide ou non).

Trois niveaux de vérification, du moins au plus coûteux (chacun peut
écarter un email sans passer au suivant) :
    1. Format (regex) — instantané, aucun réseau.
    2. Domaine jetable connu — instantané, liste statique.
    3. Enregistrement MX du domaine — une requête DNS (quelques dizaines
       de ms à ~5s en cas de lenteur/timeout).

Point d'entrée principal à utiliser dans les pipelines d'insertion/envoi :

    from email_validator import email_blackliste_ou_a_risque

    a_risque, raison = email_blackliste_ou_a_risque(email)
    if a_risque:
        log.warning(f"Email {email} écarté : {raison}")
        continue
"""

from __future__ import annotations

import logging
import re

from email_blacklist import emails_blacklistes

try:
    import dns.resolver

    _DNS_DISPONIBLE = True
except ImportError:  # dnspython absent de l'environnement : voir possede_enregistrement_mx
    _DNS_DISPONIBLE = False

log = logging.getLogger(__name__)

# Version simplifiée de la grammaire RFC 5322 — suffisante pour rejeter les
# formats manifestement invalides (espace, @ manquant/multiple, domaine
# sans point, label vide "a..b@x.com"...) sans la complexité d'une
# implémentation RFC 5322 complète (inutile ici : un email syntaxiquement
# exotique mais rarissime ne vaut pas le risque de faux négatifs sur des
# adresses professionnelles tout à fait ordinaires).
MOTIF_FORMAT_EMAIL = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Domaines de messagerie jetable/temporaire les plus répandus — jamais une
# vraie boîte de prospection B2B derrière une de ces adresses : un contact
# qui en utilise une n'a de toute façon fourni aucune adresse professionnelle
# exploitable. Liste non exhaustive par nature (de nouveaux domaines
# jetables apparaissent en continu) : une défense complémentaire au format
# et au MX, pas la protection principale.
DOMAINES_JETABLES = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "guerrillamail.biz",
    "10minutemail.com", "10minutemail.net", "yopmail.com", "yopmail.fr", "yopmail.net",
    "tempmail.com", "temp-mail.org", "throwawaymail.com", "trashmail.com", "trashmail.net",
    "dispostable.com", "sharklasers.com", "getnada.com", "maildrop.cc", "fakeinbox.com",
    "mohmal.com", "moakt.com", "mintemail.com", "emailondeck.com", "mailnesia.com",
    "spamgourmet.com", "getairmail.com", "mailcatch.com", "discard.email", "spam4.me",
    "mailsac.com", "tempinbox.com", "burnermail.io", "nowmymail.com", "inboxkitten.com",
}


def format_valide(email: str) -> bool:
    """Vrai si la syntaxe de l'email est plausible (voir MOTIF_FORMAT_EMAIL).
    N'atteste en rien que la boîte existe réellement — voir
    possede_enregistrement_mx pour ça."""
    return bool(email) and bool(MOTIF_FORMAT_EMAIL.match(email.strip()))


def _domaine(email: str) -> str:
    return email.strip().lower().rsplit("@", 1)[-1]


def domaine_jetable(email: str) -> bool:
    return _domaine(email) in DOMAINES_JETABLES


def possede_enregistrement_mx(domaine: str, timeout: float = 5.0) -> bool | None:
    """True si le domaine a au moins un enregistrement MX (donc capable de
    recevoir des emails), False s'il n'en a EXPLICITEMENT aucun (domaine
    confirmé incapable de recevoir un email — hard bounce quasi certain à
    l'envoi), None si la vérification elle-même a échoué (dnspython absent,
    DNS injoignable, timeout, domaine mal formé...).

    Distinction volontaire entre False et None : un email n'est jamais
    écarté sur un problème d'infra qui NOUS est propre (résolveur DNS
    indisponible), seulement sur une absence de MX confirmée — cf. le même
    principe déjà appliqué ailleurs dans ce projet (ex:
    email_blacklist.py::emails_blacklistes, qui renvoie un ensemble vide
    plutôt que de bloquer tout un run sur une panne de lecture)."""
    if not _DNS_DISPONIBLE:
        log.debug("dnspython non installé — vérification MX ignorée (voir requirements.txt).")
        return None
    if not domaine:
        return None
    try:
        reponses = dns.resolver.resolve(domaine, "MX", lifetime=timeout)
        return len(reponses) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        # Domaine inexistant, ou existant mais sans aucun MX déclaré : dans
        # les deux cas, aucun serveur mail n'acceptera de message pour ce
        # domaine — un hard bounce n'est pas un risque ici, c'est une
        # certitude technique.
        return False
    except Exception as e:
        # dns.exception.Timeout, NoNameservers, domaine malformé...
        # : on ne sait pas trancher, donc on ne bloque pas.
        log.debug(f"Vérification MX impossible pour {domaine} : {e}")
        return None


def email_exploitable(email: str, verifier_mx: bool = True) -> tuple[bool, str | None]:
    """Vérification structurelle complète d'un email JAMAIS vu (pas de
    notion de blacklist ici — voir email_blackliste_ou_a_risque pour la
    vérification combinée). Renvoie (True, None) si rien ne s'y oppose, ou
    (False, raison lisible) sinon. Ne lève jamais d'exception.

    verifier_mx=False permet de sauter la requête DNS (tests, ou contexte
    où on sait déjà que le domaine est valide, ex: juste après avoir
    confirmé que le site web du domaine répond en HTTP — voir
    email_enricher.py)."""
    if not email:
        return False, "email vide"
    email = email.strip()

    if not format_valide(email):
        return False, "format invalide"

    if domaine_jetable(email):
        return False, "domaine de messagerie jetable/temporaire"

    if verifier_mx:
        domaine = _domaine(email)
        a_mx = possede_enregistrement_mx(domaine)
        if a_mx is False:
            return False, f"domaine « {domaine} » sans enregistrement MX (ne peut pas recevoir d'email)"

    return True, None


def email_blackliste_ou_a_risque(
    email: str, blacklist: set[str] | None = None, verifier_mx: bool = True
) -> tuple[bool, str | None]:
    """LE point d'entrée à utiliser avant d'ajouter un email à une
    campagne ou à Supabase (voir docstring du module). Combine :
        1. La blacklist durable (hard bounces déjà vus, voir
           email_blacklist.py) — un email qui a DÉJÀ fait bondir un
           envoi précédent, quel que soit son format.
        2. La vérification structurelle ci-dessus (format, domaine
           jetable, MX) — un email jamais vu mais à risque connu.

    Renvoie (True, raison) si l'email doit être écarté, (False, None)
    sinon. `blacklist` peut être fournie déjà chargée (voir
    email_blacklist.emails_blacklistes — à charger UNE fois par run, pas
    par lead, même principe que partout ailleurs dans ce projet) pour
    éviter une requête Supabase par email ; sinon rechargée ici (moins
    efficace mais toujours correct)."""
    email_normalise = (email or "").strip().lower()
    if not email_normalise:
        return True, "email vide"

    if blacklist is None:
        blacklist = emails_blacklistes()
    if email_normalise in blacklist:
        return True, "déjà blacklisté (hard bounce précédent)"

    exploitable, raison = email_exploitable(email_normalise, verifier_mx=verifier_mx)
    if not exploitable:
        return True, raison

    return False, None
