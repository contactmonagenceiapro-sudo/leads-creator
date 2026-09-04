-- Verrou pour livraison_devis.py : le cron horaire
-- (.github/workflows/livraison_devis.yml, runner GitHub Actions) et le
-- bouton manuel du dashboard (dashboard/process_runner.py::lancer_livraison_devis,
-- subprocess Streamlit Community Cloud) lancent tous les deux le MÊME
-- script, sur des machines différentes — aucun état en mémoire/fichier
-- local ne peut donc coordonner les deux. Sans ce verrou, un chevauchement
-- peut livrer la même demande de devis à deux artisans différents (viole
-- la garantie "un seul candidat à la fois" documentée dans livraison_devis.py)
-- ou dépasser le quota d'un abonnement (_quota_disponible est un
-- check-then-act). Même schéma et même doctrine que mail_check_lock (voir
-- sql/init_mail_check.sql, mail_processor.py::_acquerir_verrou/_liberer_verrou).

CREATE TABLE IF NOT EXISTS livraison_devis_lock (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- une seule ligne possible
    locked_at TIMESTAMPTZ,
    locked_by TEXT
);
INSERT INTO livraison_devis_lock (id, locked_at, locked_by)
VALUES (1, NULL, NULL)
ON CONFLICT (id) DO NOTHING;

-- Même doctrine RLS que toutes les autres tables de ce projet : accès
-- service_role uniquement, aucune policy anon/authenticated.
ALTER TABLE livraison_devis_lock ENABLE ROW LEVEL SECURITY;
