#!/usr/bin/env python3
"""Crée ou met à jour un compte du Portail Client (table utilisateurs_dashboard),
et le lie éventuellement à une ou plusieurs campagnes B2B (utilisateur_campagnes)
et/ou à un ou plusieurs leads B2C (utilisateur_leads, voir
sql/init_utilisateur_leads.sql — artisans acheteurs de devis, table `leads`).

Le mot de passe n'est jamais stocké en clair : hashé avec bcrypt avant
l'insertion en base (voir sql/init_portail_client.sql, vérifié par
api/main.py::login).

Usage :
    # Compte admin (accès total, aucune campagne/lead à lier)
    python3 creer_utilisateur_dashboard.py --email moi@expertise-digitale.fr --role admin

    # Compte client B2B, lié à une campagne (mot de passe demandé de façon masquée)
    python3 creer_utilisateur_dashboard.py --email contact@sbg-travaux.fr --role client --campagne "S.B.G Travaux"

    # Client lié à plusieurs campagnes
    python3 creer_utilisateur_dashboard.py --email x@y.fr --role client --campagne "Client A" --campagne "Client B"

    # Compte client B2C (artisan acheteur de devis), lié par son e-mail dans `leads`
    python3 creer_utilisateur_dashboard.py --email artisan@exemple.fr --role client --lead-email artisan@exemple.fr

Ré-exécutable sans risque : un même e-mail met à jour le mot de passe/rôle
existant plutôt que de dupliquer la ligne (upsert sur la colonne UNIQUE email).
"""

import argparse
import getpass
import os
import sys

import bcrypt
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password",
        default=None,
        help="Mot de passe en clair (déconseillé : reste dans l'historique du shell). "
        "Si omis, demandé de façon masquée.",
    )
    parser.add_argument("--role", choices=["admin", "client"], default="client")
    parser.add_argument(
        "--campagne",
        action="append",
        default=[],
        dest="campagnes",
        metavar="NOM_CLIENT",
        help="nom_client (table campagnes, B2B) à lier à ce compte — répétable.",
    )
    parser.add_argument(
        "--lead-email",
        action="append",
        default=[],
        dest="leads_emails",
        metavar="EMAIL_ARTISAN",
        help="e-mail exact (colonne leads.email, B2C) de l'artisan à lier à ce compte — répétable.",
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("❌ SUPABASE_URL / SUPABASE_KEY manquants (vérifie ton .env).")

    email = args.email.strip().lower()
    if args.role == "client" and not args.campagnes and not args.leads_emails:
        sys.exit(
            "❌ Un compte client doit être lié à au moins une campagne (--campagne \"Nom exact\") "
            "ou à au moins un artisan (--lead-email \"email@exemple.fr\")."
        )

    mot_de_passe = args.password or getpass.getpass("Mot de passe : ")
    if not mot_de_passe:
        sys.exit("❌ Mot de passe vide.")
    if len(mot_de_passe.encode("utf-8")) > 72:
        # bcrypt (>=4.0) lève ValueError au-delà de 72 octets, au lieu de
        # tronquer silencieusement comme avant — mieux vaut le signaler ici
        # qu'avoir un hashpw() qui plante plus bas.
        sys.exit("❌ Le mot de passe ne doit pas dépasser 72 caractères.")
    mot_de_passe_hash = bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/utilisateurs_dashboard",
        headers=_headers(),
        params={"on_conflict": "email"},
        json={"email": email, "mot_de_passe_hash": mot_de_passe_hash, "role": args.role, "actif": True},
        timeout=15,
    )
    if res.status_code not in (200, 201):
        sys.exit(f"❌ Échec création utilisateur : {res.status_code} {res.text}")
    utilisateur_id = res.json()[0]["id"]
    print(f"✅ Utilisateur « {email} » ({args.role}) créé/mis à jour (id={utilisateur_id})")

    for campagne in args.campagnes:
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/utilisateur_campagnes",
            headers=_headers(),
            params={"on_conflict": "utilisateur_id,client_final"},
            json={"utilisateur_id": utilisateur_id, "client_final": campagne},
            timeout=15,
        )
        if res.status_code not in (200, 201):
            print(f"⚠️  Échec liaison campagne « {campagne} » : {res.status_code} {res.text}")
        else:
            print(f"🔗 Lié à la campagne « {campagne} »")

    for lead_email in args.leads_emails:
        res_lookup = requests.get(
            f"{SUPABASE_URL}/rest/v1/leads",
            headers=_headers(),
            params={"email": f"eq.{lead_email.strip().lower()}", "select": "id,company,email"},
            timeout=15,
        )
        if res_lookup.status_code != 200 or not res_lookup.json():
            print(f"⚠️  Aucun artisan trouvé dans `leads` avec l'e-mail « {lead_email} » — liaison ignorée.")
            continue
        lead = res_lookup.json()[0]
        res_lien = requests.post(
            f"{SUPABASE_URL}/rest/v1/utilisateur_leads",
            headers=_headers(),
            params={"on_conflict": "utilisateur_id,lead_id"},
            json={"utilisateur_id": utilisateur_id, "lead_id": lead["id"]},
            timeout=15,
        )
        if res_lien.status_code not in (200, 201):
            print(f"⚠️  Échec liaison artisan « {lead_email} » : {res_lien.status_code} {res_lien.text}")
        else:
            print(f"🔗 Lié à l'artisan « {lead.get('company') or lead_email} » (lead_id={lead['id']})")


if __name__ == "__main__":
    main()
