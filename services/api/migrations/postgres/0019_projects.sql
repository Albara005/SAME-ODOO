-- 0019: projects, their hours, and invoicing billable hours (project tasks are ordinary tasks).
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE projects (
	name VARCHAR(200) NOT NULL,
	description TEXT NOT NULL,
	customer_id VARCHAR(36),
	status VARCHAR(20) NOT NULL,
	hourly_rate NUMERIC(18, 6),
	tax_id VARCHAR(36),
	budget_hours NUMERIC(10, 2),
	manager_user_id VARCHAR(36),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_project_rate CHECK (hourly_rate IS NULL OR CAST(hourly_rate AS NUMERIC) >= 0),
	CONSTRAINT ck_project_budget CHECK (budget_hours IS NULL OR CAST(budget_hours AS NUMERIC) > 0),
	FOREIGN KEY(manager_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_projects_customer_id ON projects (customer_id);
CREATE INDEX ix_projects_organization_id ON projects (organization_id);
CREATE INDEX ix_projects_status ON projects (status);

CREATE TABLE time_entries (
	project_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	entry_date DATE NOT NULL,
	hours NUMERIC(5, 2) NOT NULL,
	description VARCHAR(500) NOT NULL,
	billable BOOLEAN NOT NULL,
	task_id VARCHAR(36),
	invoice_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, project_id) REFERENCES projects (organization_id, id),
	FOREIGN KEY(organization_id, task_id) REFERENCES tasks (organization_id, id),
	FOREIGN KEY(organization_id, invoice_id) REFERENCES invoices (organization_id, id),
	CONSTRAINT ck_time_entry_hours CHECK (CAST(hours AS NUMERIC) > 0 AND CAST(hours AS NUMERIC) <= 24),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_time_entries_invoice_id ON time_entries (invoice_id);
CREATE INDEX ix_time_entries_organization_id ON time_entries (organization_id);
CREATE INDEX ix_time_entries_project_id ON time_entries (project_id);
CREATE INDEX ix_time_entries_user_id ON time_entries (user_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['projects', 'time_entries']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON projects TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON time_entries TO %I', app_role);
END $$;
