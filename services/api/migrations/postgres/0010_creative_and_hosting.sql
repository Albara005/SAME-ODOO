-- 0010: Creative Studio brand kits and logos; private hosting requests.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE brand_kits (
	name VARCHAR(120) NOT NULL,
	tagline VARCHAR(120) NOT NULL,
	primary_color VARCHAR(7) NOT NULL,
	secondary_color VARCHAR(7),
	font_pair VARCHAR(30) NOT NULL,
	system JSON NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_brand_kits_organization_id ON brand_kits (organization_id);

CREATE TABLE hosting_requests (
	kind VARCHAR(40) NOT NULL,
	size VARCHAR(20) NOT NULL,
	region VARCHAR(40) NOT NULL,
	reason TEXT NOT NULL,
	quoted_monthly_cents BIGINT NOT NULL,
	status VARCHAR(20) NOT NULL,
	requested_by VARCHAR(36),
	approved_by VARCHAR(36),
	approved_at TIMESTAMP WITH TIME ZONE,
	operator_note TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_hosting_requests_organization_id ON hosting_requests (organization_id);
CREATE INDEX ix_hosting_requests_status ON hosting_requests (status);

CREATE TABLE brand_logos (
	brand_kit_id VARCHAR(36) NOT NULL,
	spec JSON NOT NULL,
	source VARCHAR(20) NOT NULL,
	note TEXT NOT NULL,
	"primary" BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, brand_kit_id) REFERENCES brand_kits (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_brand_logos_brand_kit_id ON brand_logos (brand_kit_id);
CREATE INDEX ix_brand_logos_organization_id ON brand_logos (organization_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['brand_kits', 'brand_logos', 'hosting_requests']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- The API may create hosting requests and record the owner's approval or a cancellation, but
-- never mark one provisioning/delivered/rejected: those are operator transitions (novara.ops),
-- run with the schema owner's role.
CREATE FUNCTION hosting_request_transition_allowed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('provisioning', 'delivered', 'rejected') AND NEW.status IS DISTINCT FROM OLD.status
       AND current_user <> (SELECT tableowner FROM pg_tables
                            WHERE schemaname = 'public' AND tablename = 'hosting_requests') THEN
        RAISE EXCEPTION 'only platform operators can move a hosting request to %', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER hosting_request_operator_only BEFORE UPDATE ON hosting_requests
    FOR EACH ROW EXECUTE FUNCTION hosting_request_transition_allowed();

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON brand_kits, brand_logos, hosting_requests TO %I', app_role);
END $$;
