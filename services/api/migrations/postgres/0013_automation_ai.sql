-- 0013: scheduled workflows, signed webhooks, a monthly AI allowance per member, approved assistant notes.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

ALTER TABLE workflows ADD COLUMN schedule JSON;
ALTER TABLE ai_settings ADD COLUMN user_monthly_budget_micros BIGINT;

CREATE TABLE ai_memories (
	user_id VARCHAR(36) NOT NULL,
	text VARCHAR(500) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_memories_organization_id ON ai_memories (organization_id);
CREATE INDEX ix_ai_memories_user_id ON ai_memories (user_id);

CREATE TABLE webhook_secrets (
	secret TEXT NOT NULL,
	hint VARCHAR(12) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_webhook_secret_org UNIQUE (organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_webhook_secrets_organization_id ON webhook_secrets (organization_id);

ALTER TABLE ai_memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ai_memories USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE webhook_secrets ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON webhook_secrets USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON ai_memories TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON webhook_secrets TO %I', app_role);
END $$;
