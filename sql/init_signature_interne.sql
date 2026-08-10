-- Signature électronique interne (simple, article 1367 du Code civil) —
-- alternative à Yousign pendant la phase sans budget / sandbox non valable
-- légalement (voir signature_interne.py, diagnostic + plan validés le 10/08).
-- Additive : n'affecte aucune ligne existante.
--
-- signature_provider distingue COMMENT un contrat a été signé, mais ne
-- remplace jamais yousign_status='signe' comme test "ce contrat est-il
-- signé ?" partout ailleurs dans le dashboard (gestion_clients.py,
-- data_access.py) — conservé tel quel pour ne rien casser, quel que soit le
-- provider.
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_provider TEXT NOT NULL DEFAULT 'interne'
    CHECK (signature_provider IN ('interne', 'yousign'));

-- Lien public unique et non-devinable (secrets.token_urlsafe(32), voir
-- signature_interne.envoyer_contrat_signature_interne). UNIQUE : la page de
-- signature publique résout STRICTEMENT par ce token, jamais par
-- lead_id/contract_id (voir pages_publiques.afficher_signature).
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_token TEXT UNIQUE;

-- Preuve de consentement (art. 1367 al. 2 : identifier le signataire +
-- établir le lien avec l'acte) — capturées uniquement au moment du clic
-- "J'accepte les conditions", jamais avant.
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_nom_saisi TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_ip TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_user_agent TEXT;

-- Empreinte SHA-256 + copie intégrale (base64, pas bytea : évite toute
-- ambiguïté d'encodage côté PostgREST/JSON) du PDF exact montré au moment
-- de la signature — permet de reproduire mot pour mot le document accepté,
-- indépendamment de toute évolution ultérieure du code de génération.
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_document_hash TEXT;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signature_document_pdf_base64 TEXT;

CREATE INDEX IF NOT EXISTS idx_contracts_signature_token ON contracts(signature_token);
