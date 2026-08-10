# Registre des traitements — Article 30 du RGPD

**Responsable de traitement** : Expertise Digitale
**Contact** : expertisedigitale@zohomail.eu
**Statut juridique** : [À compléter une fois le statut d'auto-entrepreneur / société obtenu]
**Délégué à la protection des données (DPO)** : Non applicable (structure ne dépassant pas les seuils rendant la désignation obligatoire)
**Dernière mise à jour** : [À compléter à la date de publication]

---

## Traitement n°1 — Prospection commerciale B2C (artisans du bâtiment)

| Champ | Détail |
|---|---|
| **Finalité** | Identifier et contacter des artisans du bâtiment susceptibles d'être intéressés par des demandes de devis de clients finaux |
| **Base légale** | Intérêt légitime (art. 6.1.f RGPD) ; régime opt-out pour la prospection par email vers une personne agissant à titre professionnel (art. L34-5 CPCE) |
| **Catégories de données** | Nom de l'entreprise, secteur d'activité, commune, adresse email professionnelle, téléphone (quand disponible), taille d'entreprise, présence web |
| **Catégories de personnes concernées** | Artisans, auto-entrepreneurs du secteur du bâtiment |
| **Origine des données** | Base SIRENE (donnée publique), annuaires professionnels en ligne (scraping) |
| **Destinataires internes** | Équipe commerciale (accès base de données) |
| **Destinataires externes** | Aucun, sauf mise en relation ponctuelle avec un client final ayant exprimé un besoin correspondant |
| **Transferts hors UE** | Aucun identifié à ce jour (hébergement Supabase et Zoho Mail à vérifier — zone de données à confirmer) |
| **Durée de conservation** | 3 ans à compter du dernier contact sans suite ; conservation permanente en liste d'exclusion en cas de demande d'opposition (« STOP ») |
| **Mesures de sécurité** | Accès restreint à la base de données, mots de passe chiffrés pour les comptes utilisateurs, mécanisme de blacklist permanent |

## Traitement n°2 — Prospection commerciale B2B (architectes, promoteurs, maîtres d'œuvre)

| Champ | Détail |
|---|---|
| **Finalité** | Identifier et contacter des professionnels du bâtiment pour leur proposer un service d'apport de clients qualifiés |
| **Base légale** | Intérêt légitime (art. 6.1.f RGPD) ; régime opt-out (art. L34-5 CPCE) |
| **Catégories de données** | Nom de l'entreprise, type d'acteur, commune, SIRET, adresse email, téléphone (quand disponible), taille d'entreprise, présence web (LinkedIn, réseaux sociaux) |
| **Catégories de personnes concernées** | Architectes, promoteurs, maîtres d'œuvre (personnes physiques ou représentants d'entreprises constituées) |
| **Origine des données** | Base SIRENE, scraping d'annuaires professionnels, LinkedIn (identification manuelle) |
| **Destinataires internes** | Équipe commerciale |
| **Destinataires externes** | Aucun |
| **Transferts hors UE** | Aucun identifié à ce jour (à confirmer) |
| **Durée de conservation** | 3 ans à compter du dernier contact sans suite ; conservation permanente en liste d'exclusion en cas de demande d'opposition |
| **Mesures de sécurité** | Accès restreint à la base de données, mécanisme de blacklist permanent |

## Traitement n°3 — Gestion des comptes du portail artisan (self-service)

| Champ | Détail |
|---|---|
| **Finalité** | Permettre à un artisan de créer un compte et gérer sa relation avec l'Agence |
| **Base légale** | Exécution du contrat / mesures précontractuelles (art. 6.1.b RGPD) |
| **Catégories de données** | Nom complet, adresse email, mot de passe (chiffré), numéro SIRET |
| **Catégories de personnes concernées** | Artisans inscrits volontairement sur le portail |
| **Origine des données** | Saisie directe par la personne concernée (formulaire d'inscription) |
| **Destinataires internes** | Équipe commerciale, administrateur technique |
| **Destinataires externes** | Aucun |
| **Transferts hors UE** | À vérifier (hébergement Supabase) |
| **Durée de conservation** | Durée de la relation commerciale + durée légale d'archivage des documents commerciaux (10 ans pour les pièces comptables associées) |
| **Mesures de sécurité** | Authentification (Supabase Auth), Row Level Security, mots de passe chiffrés |

## Traitement n°4 — Génération et signature électronique de contrats

| Champ | Détail |
|---|---|
| **Finalité** | Formaliser un contrat de prestation avec un client et en assurer la valeur probante |
| **Base légale** | Exécution du contrat (art. 6.1.b RGPD) ; obligation légale / intérêt légitime pour la preuve de signature |
| **Catégories de données** | Raison sociale, SIRET, adresse, nom du représentant, volume et prix de la prestation ; données de preuve de signature (nom saisi, date, heure, adresse IP, user-agent, empreinte du document) |
| **Catégories de personnes concernées** | Clients signataires |
| **Origine des données** | Saisie manuelle par l'Agence (bon de commande) et par le client (page de signature) |
| **Destinataires internes** | Équipe commerciale, administrateur |
| **Destinataires externes** | Aucun (signature interne) ; Yousign en option future si réactivé |
| **Transferts hors UE** | Aucun (signature interne, hébergement Supabase à confirmer) |
| **Durée de conservation** | 10 ans (obligation légale de conservation des documents commerciaux/comptables) |
| **Mesures de sécurité** | Token de signature imprévisible (256 bits), hash SHA-256 du document, accès par token uniquement |

## Traitement n°5 — Gestion des demandes de désinscription et liste d'exclusion

| Champ | Détail |
|---|---|
| **Finalité** | Respecter le droit d'opposition des personnes contactées et empêcher tout recontact futur |
| **Base légale** | Obligation légale (respect du droit d'opposition, art. 21 RGPD) |
| **Catégories de données** | Adresse email blacklistée |
| **Catégories de personnes concernées** | Toute personne ayant répondu « STOP » à un email de prospection, ou ayant demandé son retrait |
| **Origine des données** | Réponse email automatiquement détectée, ou demande directe |
| **Destinataires internes** | Système automatisé (aucun accès humain nécessaire au fonctionnement) |
| **Destinataires externes** | Aucun |
| **Transferts hors UE** | Aucun |
| **Durée de conservation** | Permanente (nécessaire à la finalité même du traitement — empêcher un recontact) |
| **Mesures de sécurité** | Vérification systématique avant tout envoi, y compris lors d'un re-scraping ultérieur |

## Traitement n°6 — Suivi des réponses et scoring des leads

| Champ | Détail |
|---|---|
| **Finalité** | Mesurer l'efficacité des campagnes de prospection et prioriser les contacts les plus pertinents |
| **Base légale** | Intérêt légitime (art. 6.1.f RGPD) |
| **Catégories de données** | Statut de contact, date d'envoi, réponse reçue (oui/non), score de compatibilité calculé (données publiques : activité économique de la commune, taille d'entreprise) |
| **Catégories de personnes concernées** | Ensemble des leads B2B et B2C contactés |
| **Origine des données** | Données déjà collectées (traitements 1 et 2) et données publiques (permis de construire, INSEE/SIRENE) |
| **Destinataires internes** | Équipe commerciale |
| **Destinataires externes** | Aucun |
| **Transferts hors UE** | Aucun |
| **Durée de conservation** | Alignée sur la durée de conservation du lead concerné (traitements 1 et 2) |
| **Mesures de sécurité** | Accès restreint à la base de données |

---

## Points à clarifier / actions restantes

- [ ] Confirmer la zone d'hébergement des données chez Supabase et Zoho Mail (UE ou hors UE — si hors UE, des garanties supplémentaires seraient nécessaires)
- [ ] Mettre à jour le statut juridique une fois le SIRET obtenu
- [ ] Mettre en place un outil ou une procédure pour honorer une demande d'accès ou de suppression de données (actuellement manuel, non formalisé)
- [ ] Réviser ce registre à chaque évolution significative du pipeline (nouveau traitement, nouvelle source de données, nouveau sous-traitant)
