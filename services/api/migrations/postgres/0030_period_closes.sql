-- 0030: closing the books month by month: who closed or reopened which month, when and why
-- (novara/modules/accounting/periods.py). Append-only: the lock date says what is closed now.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE period_closes (
	period_end DATE NOT NULL,
	action VARCHAR(10) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	drafts JSON NOT NULL,
	user_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_period_close_action CHECK (action IN ('closed', 'reopened')),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_period_closes_organization_id ON period_closes (organization_id);
CREATE INDEX ix_period_closes_period_end ON period_closes (period_end);

ALTER TABLE period_closes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON period_closes USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT ON period_closes TO %I', app_role);
END $$;
