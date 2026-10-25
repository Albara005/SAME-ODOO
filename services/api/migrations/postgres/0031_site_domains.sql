-- 0031: customers' own domains for published sites (novara/studio/domains.py).
-- No RLS on purpose, like app_site_routes: a request for a domain must find its site before a tenant
-- is bound. It holds routing data only; the API checks the organization on every management call.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE app_site_domains (
	organization_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	hostname VARCHAR(253) NOT NULL,
	token VARCHAR(64) NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	last_checked_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(300) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_site_domain_org_host UNIQUE (organization_id, hostname),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_app_site_domains_hostname ON app_site_domains (hostname);
CREATE INDEX ix_app_site_domains_organization_id ON app_site_domains (organization_id);
CREATE INDEX ix_app_site_domains_project_id ON app_site_domains (project_id);
-- Only one organization can have proven a hostname.
CREATE UNIQUE INDEX uq_site_domain_verified ON app_site_domains (hostname) WHERE verified_at IS NOT NULL;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app_site_domains TO %I', app_role);
END $$;
