# Architecture technique — Base de données Supabase

Complète [docs/organigramme.md](organigramme.md) et [architecture_agents.md](architecture_agents.md).
Toutes les tables sont interrogées en **service_role** (clé `SUPABASE_KEY`, qui contourne
le RLS) depuis les scripts et le dashboard — seule la table `artisans` est aussi exposée
côté navigateur avec la clé **anon** publique (`landing/supabase-client.js`), ce qui rend
son RLS réellement critique. Voir [architecture_integrations.md](architecture_integrations.md)
pour le détail des deux clés.

**Statut RLS** établi à partir des migrations versionnées dans `sql/` (audit du 14/08/2026,
`sql/fix_rls_leads_kpis.sql` et `sql/fix_rls_5_tables_restantes.sql`) :

- 🔒 **RLS actif** — confirmé par une migration `ENABLE ROW LEVEL SECURITY` ou un test d'audit documenté.
- ⚠️ **RLS non trouvé** dans les migrations versionnées — aucun risque d'exposition publique
  identifié aujourd'hui (aucune de ces tables n'est appelée avec la clé anon), mais l'état
  RLS réel n'est pas garanti par le code source seul (a pu être activé manuellement dans
  l'interface Supabase sans migration associée) — à vérifier directement dans Supabase.

```mermaid
flowchart TD
    classDef protege fill:#1b4332,stroke:#2d6a4f,color:#d8f3dc
    classDef inconnu fill:#4a2c2a,stroke:#9c3d32,color:#ffe5e0

    subgraph B2C_DOM["Domaine B2C — artisans (Expertise Digitale)"]
        LEADS["leads 🔒\nname, email, company, industry,\nscore, status, pitch_commercial,\ncommune, siren, contacted_at..."]
        INTAKE["intake_responses ⚠️\nRéponses formulaire post-contact\n(description, zone, photos, GBP)"]
        ARTISANS["artisans 🔒 (policy dédiée)\nEspace self-service artisans\n(auth Supabase, RLS artisans_select_own)"]
    end

    subgraph B2B_DOM["Domaine B2B — outbound_chantiers (multi-clients)"]
        LPRO["leads_professionnels ⚠️\nArchitectes/promoteurs/MO,\nscore_activite_chantiers, score_final,\nstatut, signale_invalide"]
        CAMP["campagnes ⚠️\n1 ligne = 1 client/secteur/zone,\ntypes_acteur_cibles (codes NAF + poids)"]
        DEVIS["demandes_devis_particuliers 🔒\nFormulaire inbound (maîtres d'ouvrage,\nconsentement explicite)"]
    end

    subgraph CONTRAT_DOM["Contrats, paiement, remboursements"]
        CONTRACTS["contracts 🔒\nCycle intake→Yousign/interne→Stripe\nyousign_status, payment_status,\nsignature_token, signature_ip..."]
        REMB["remboursements ⚠️\nRemboursement Stripe réel (B2C)\nOU avoir commercial (B2B)"]
        AGCONF["agence_config ⚠️\nNom/adresse/SIRET agence\n(pré-remplissage bons de commande)"]
    end

    subgraph EMAIL_DOM["Suivi e-mail"]
        EVENTS["email_events ⚠️\n1 ligne / événement envoyé\n(ouverture/clic retirés)"]
        BLACKLIST["emails_blacklistes ⚠️\nHard bounce ou STOP,\nindépendante des leads"]
        MAILLOCK["mail_check_lock ⚠️\nVerrou mono-ligne relève IMAP"]
        MAILRUNS["mail_check_runs ⚠️\nHistorique des relèves\n(manuelle vs auto)"]
    end

    subgraph PORTAIL_DOM["Portail Client (auth dashboard)"]
        UDASH["utilisateurs_dashboard ⚠️\nComptes admin/client,\nmot_de_passe_hash (bcrypt)"]
        UCAMP["utilisateur_campagnes ⚠️\nLiaison compte ↔ campagnes\nautorisées (many-to-many)"]
    end

    subgraph RGPD_DOM["Conformité RGPD"]
        REGISTRE["registre_suppressions_rgpd 🔒\nemail_hash (SHA256), table_source,\ntraçabilité art. 17"]
    end

    subgraph SCAFFOLD["Scaffolding initial (jamais branché en usage réel)"]
        MEMORIES["agent_memories 🔒 (sans policy)\nMémoire vectorielle agents (pgvector)"]
        TASKS["tasks 🔒 (sans policy)\nFile de tâches génériques"]
        KPIS["kpis 🔒 (sans policy)\nKPIs business génériques"]
        ERRLOG["error_log ⚠️\nLog d'erreurs pour\nauto-amélioration"]
    end

    LEADS --> INTAKE --> CONTRACTS
    LPRO --> REMB
    CONTRACTS --> REMB
    CAMP --> LPRO
    CAMP --> UCAMP --> UDASH
    LEADS -.->|"email_hash"| REGISTRE
    LPRO -.->|"email_hash"| REGISTRE
    LEADS --> BLACKLIST
    LPRO --> BLACKLIST
    LEADS --> EVENTS
    LPRO --> EVENTS

    class LEADS,ARTISANS,DEVIS,CONTRACTS,REGISTRE,MEMORIES,TASKS,KPIS protege
    class INTAKE,LPRO,CAMP,REMB,AGCONF,EVENTS,BLACKLIST,MAILLOCK,MAILRUNS,UDASH,UCAMP,ERRLOG inconnu
```

## Qui écrit / lit quoi (principaux flux)

```mermaid
flowchart LR
    subgraph SCRIPTS["Agents / scripts"]
        SB2["scraper_batiment.py\n+ lead_worker.py"]
        SEP2["outbound_chantiers/\nscorer_et_publier.py"]
        MP2["mail_processor.py"]
        CS2["dashboard/\ncontrats_signature.py"]
        SI2["dashboard/\nsignature_interne.py"]
        RGPD2["dashboard/app_pages/\nsuppression_rgpd.py"]
    end

    SB2 -->|"insert/upsert"| LEADS2["leads"]
    SEP2 -->|"insert dédupliqué"| LPRO2["leads_professionnels"]
    MP2 -->|"update status"| LEADS2
    MP2 -->|"update status"| LPRO2
    MP2 -->|"insert hard bounce/STOP"| BL2["emails_blacklistes"]
    MP2 -->|"lock/unlock + historique"| ML2["mail_check_lock / mail_check_runs"]
    CS2 -->|"insert/update"| CONTRACTS2["contracts"]
    SI2 -->|"update signature_*"| CONTRACTS2
    RGPD2 -->|"delete"| LEADS2
    RGPD2 -->|"delete"| LPRO2
    RGPD2 -->|"insert trace"| REG2["registre_suppressions_rgpd"]
    RGPD2 -->|"blacklist"| BL2
```
