-- 0008: App Studio: projects, immutable versions, deployments, public site routes.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE app_projects (
	name VARCHAR(120) NOT NULL,
	slug VARCHAR(60) NOT NULL,
	template VARCHAR(40) NOT NULL,
	description TEXT NOT NULL,
	working_files JSON NOT NULL,
	base_version INTEGER NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_app_project_slug UNIQUE (organization_id, slug),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_projects_organization_id ON app_projects (organization_id);

CREATE TABLE app_site_routes (
	site_key VARCHAR(64) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	environment VARCHAR(20) NOT NULL,
	deployment_id VARCHAR(36) NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (site_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_site_routes_organization_id ON app_site_routes (organization_id);
CREATE INDEX ix_app_site_routes_project_id ON app_site_routes (project_id);

CREATE TABLE app_versions (
	project_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	message VARCHAR(300) NOT NULL,
	files JSON NOT NULL,
	checks JSON NOT NULL,
	checks_passed BOOLEAN NOT NULL,
	author_kind VARCHAR(20) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, project_id) REFERENCES app_projects (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_app_version_number UNIQUE (organization_id, project_id, number),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_versions_organization_id ON app_versions (organization_id);
CREATE INDEX ix_app_versions_project_id ON app_versions (project_id);

CREATE TABLE app_deployments (
	project_id VARCHAR(36) NOT NULL,
	version_id VARCHAR(36) NOT NULL,
	environment VARCHAR(20) NOT NULL,
	site_key VARCHAR(64) NOT NULL,
	status VARCHAR(20) NOT NULL,
	approved_by VARCHAR(36),
	approved_at TIMESTAMP WITH TIME ZONE,
	deployed_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, project_id) REFERENCES app_projects (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, version_id) REFERENCES app_versions (organization_id, id),
	FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(deployed_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_deployments_organization_id ON app_deployments (organization_id);
CREATE INDEX ix_app_deployments_project_id ON app_deployments (project_id);
CREATE INDEX ix_app_deployments_site_key ON app_deployments (site_key);
CREATE INDEX ix_app_deployments_status ON app_deployments (status);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['app_projects', 'app_versions', 'app_deployments']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- app_site_routes has no RLS on purpose: serving a public site must find its organization
-- before a tenant is bound. It holds routing data only (key -> organization, deployment);
-- site content stays in tenant-scoped tables and is read after binding that organization.

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app_projects TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON app_versions TO %I', app_role);  -- versions are immutable
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON app_deployments, app_site_routes TO %I', app_role);
END $$;
