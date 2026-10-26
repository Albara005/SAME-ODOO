-- 0026: proof of work for order and booking requests from published sites (novara/studio/proof.py).
-- Keys made before this keep working without it (require_proof false); new keys default to it.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

ALTER TABLE app_data_keys ADD COLUMN require_proof BOOLEAN DEFAULT false NOT NULL;

CREATE TABLE spent_proofs (
	salt VARCHAR(32) NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (salt),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_spent_proofs_expires_at ON spent_proofs (expires_at);
CREATE INDEX ix_spent_proofs_ip_address ON spent_proofs (ip_address);
CREATE INDEX ix_spent_proofs_organization_id ON spent_proofs (organization_id);

ALTER TABLE spent_proofs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spent_proofs USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON spent_proofs TO %I', app_role);
END $$;
