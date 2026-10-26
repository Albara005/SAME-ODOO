-- 0014: publishable data keys for App Studio sites.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE app_data_keys (
	project_id VARCHAR(36) NOT NULL,
	name VARCHAR(80) NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	hint VARCHAR(16) NOT NULL,
	scopes JSON NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	revoked_at TIMESTAMP WITH TIME ZONE,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, project_id) REFERENCES app_projects (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_app_data_key_hash UNIQUE (key_hash),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_data_keys_organization_id ON app_data_keys (organization_id);
CREATE INDEX ix_app_data_keys_project_id ON app_data_keys (project_id);

ALTER TABLE app_data_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app_data_keys USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- A site's request carries only the key, so the API can't see the row before it knows the
-- organization. This answers just that: which organization holds an active key with this hash.
CREATE FUNCTION app_data_key_org(p_key_hash text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM app_data_keys WHERE key_hash = p_key_hash AND revoked_at IS NULL
    $$;
REVOKE ALL ON FUNCTION app_data_key_org(text) FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON app_data_keys TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_data_key_org(text) TO %I', app_role);
END $$;
