# sql/ — schéma géré à la main, sans outil de migration versionné

Ce projet n'utilise pas Alembic (ni aucun autre outil de migration
automatisé) : chaque fichier `sql/*.sql` est exécuté manuellement dans
l'éditeur SQL Supabase (voir l'en-tête de `init_migrations_appliquees.sql`).
Ce README documente l'ordre d'application recommandé — voir
`audit/audit_verification_2026-09-08.md`, constat m3.

## Deux familles de fichiers

- `init_*.sql` — créent une table/vue (`CREATE TABLE IF NOT EXISTS`,
  additifs par construction). Généralement indépendants les uns des autres
  **à l'exception des dépendances de clé étrangère** (un fichier qui
  référence `leads(id)` doit être appliqué après `init.sql`, qui crée
  `leads`).
- `fix_*.sql` — modifient une table déjà créée par un `init_*.sql`
  antérieur (`ALTER TABLE`, policy RLS, index...) : doivent **toujours**
  être appliqués après le `init_*.sql` correspondant.

## Ordre recommandé

La règle la plus simple et la plus sûre : appliquer les fichiers dans leur
**ordre chronologique de premier commit** (`git log --reverse
--diff-filter=A --name-only -- 'sql/*.sql'`) — cet ordre garantit le
respect des dépendances FK, puisqu'un fichier n'a pu référencer une table
que si elle existait déjà au moment où il a été écrit. Ce n'est pas une
supposition : c'est la méthode déjà utilisée et vérifiée statiquement pour
produire `bootstrap_environnement_test.sql` (voir son en-tête) — la liste
ci-dessous applique exactement le même principe à l'ensemble du dossier,
mise à jour au 08/09/2026 (62 fichiers) :

1. `init.sql`
2. `init_campagnes.sql`
3. `init_demandes_devis_particuliers.sql`
4. `init_email_tracking.sql`
5. `init_enrichissement_pro.sql`
6. `init_leads_professionnels.sql`
7. `init_portail_client.sql`
8. `init_remboursements.sql`
9. `init_artisans.sql` (⚠️ OBSOLÈTE — voir son propre en-tête, conservé pour l'historique, ne pas ré-appliquer sur un environnement neuf)
10. `init_bounces.sql`
11. `init_campagnes_brouillon.sql`
12. `init_agence_config.sql`
13. `init_absence.sql`
14. `init_delais_livraison.sql`
15. `init_email_status.sql`
16. `init_mail_check.sql`
17. `init_email_reponses.sql`
18. `init_email_introuvable.sql`
19. `init_statut_contact_manuel.sql`
20. `init_taille_entreprise.sql`
21. `init_vue_score_conversion.sql`
22. `init_commune_leads.sql`
23. `init_taille_entreprise_leads.sql`
24. `init_signature_interne.sql`
25. `init_registre_suppressions_rgpd.sql`
26. `fix_linkedin_url_annuaire.sql`
27. `init_intake_responses_leads_b2c.sql`
28. `init_tarification_paliers_b2c.sql`
29. `fix_rls_leads_kpis.sql`
30. `fix_rls_5_tables_restantes.sql`
31. `fix_linkedin_url_annuaire_2.sql`
32. `fix_linkedin_url_annuaire_3.sql`
33. `fix_domaines_non_pertinents_4.sql`
34. `fix_rls_error_log.sql`
35. `init_demandes_devis_particuliers_generique.sql`
36. `init_demandes_devis_particuliers_livraison.sql`
37. `init_intake_responses_corps_metier_controle.sql`
38. `init_verification_pro_artisans.sql`
39. `init_reclamations.sql`
40. `init_utilisateur_leads.sql`
41. `fix_extension_vector_schema_dediee.sql`
42. `fix_index_remboursements_lead_professionnel.sql`
43. `fix_policy_artisans_select_own_perf.sql`
44. `fix_rls_articles.sql`
45. `fix_view_score_conversion_security_invoker.sql`
46. `init_migrations_appliquees.sql`
47. `fix_rls_ceo_reports.sql`
48. `init_sante_base_donnees.sql`
49. `init_journal_audit_admin.sql`
50. `init_vue_policies_rls.sql`
51. `init_couts_infrastructure.sql`
52. `init_echeances.sql`
53. `init_propositions_expirees.sql`
54. `init_satisfaction_enquetes.sql`
55. `init_demandes_devis_particuliers_confirmation.sql`
56. `init_stripe_webhook_events.sql`
57. `bootstrap_environnement_test.sql` (⚠️ PAS pour la prod — voir son propre en-tête ; instantané figé de la structure au 02/09/2026, utile pour un nouveau projet Supabase de test, pas un fichier à appliquer sur l'environnement de prod)
58. `init_livraison_devis_lock.sql`
59. `fix_remboursements_statut_en_cours.sql`
60. `init_rate_limit_formulaires_publics.sql`
61. `fix_leads_token_acces_public.sql`
62. `fix_stripe_webhook_events_statut_en_cours.sql`

## Régénérer cette liste

```bash
git log --reverse --diff-filter=A --name-only --pretty=format: -- 'sql/*.sql' \
  | grep -v '^$' | awk '!seen[$0]++'
```

À refaire à chaque ajout de fichier `sql/` — cette liste n'est pas
maintenue automatiquement (voir `scripts/controle_sante_bdd.py::
controler_derive_migrations_sql`, qui détecte les fichiers jamais
journalisés dans `migrations_appliquees`, mais ne vérifie pas l'ordre).

## Suivi de ce qui a réellement été appliqué

Après avoir exécuté un fichier dans l'éditeur SQL Supabase :

```sql
INSERT INTO migrations_appliquees (nom) VALUES ('nom_du_fichier.sql');
```

Voir `init_migrations_appliquees.sql` pour le détail (table purement
déclarative, pas de contrainte d'unicité — plusieurs fichiers sont
volontairement ré-exécutables). Le contrôle de santé quotidien
(`scripts/controle_sante_bdd.py`) compare cette table au contenu réel de ce
dossier et signale (statut `attention`) tout fichier présent ici sans
ligne correspondante.
