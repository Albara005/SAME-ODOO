-- 0027: the text inside uploaded files (PDF, Word, plain text) as passages for ranked search,
-- and the files an agent can look things up in. Existing files are read lazily on the next search.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

ALTER TABLE files ADD COLUMN text_status VARCHAR(10) DEFAULT 'pending' NOT NULL;
ALTER TABLE files ADD COLUMN text_chars INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE files ADD CONSTRAINT uq_files_org_id UNIQUE (organization_id, id);

CREATE TABLE file_chunks (
	file_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	text TEXT NOT NULL,
	terms TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, file_id) REFERENCES files (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_file_chunks_file_id ON file_chunks (file_id);
CREATE INDEX ix_file_chunks_organization_id ON file_chunks (organization_id);

ALTER TABLE file_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON file_chunks USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

ALTER TABLE agent_versions ADD COLUMN knowledge JSON DEFAULT '[]' NOT NULL;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON file_chunks TO %I', app_role);
END $$;
