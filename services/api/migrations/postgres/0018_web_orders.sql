-- 0018: order requests from published App Studio sites (data keys with the "orders" scope).
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

-- Order requests point at the key they came with (same organization).
ALTER TABLE app_data_keys ADD UNIQUE (organization_id, id);

CREATE TABLE web_orders (
	number VARCHAR(40) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	key_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	customer_name VARCHAR(200) NOT NULL,
	email VARCHAR(320) NOT NULL,
	phone VARCHAR(40) NOT NULL,
	note TEXT NOT NULL,
	lines JSON NOT NULL,
	currency VARCHAR(3) NOT NULL,
	subtotal_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	total_minor BIGINT NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	sales_order_id VARCHAR(36),
	customer_id VARCHAR(36),
	handled_by VARCHAR(36),
	handled_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_web_order_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, project_id) REFERENCES app_projects (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, key_id) REFERENCES app_data_keys (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, sales_order_id) REFERENCES sales_orders (organization_id, id),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	FOREIGN KEY(handled_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_web_orders_ip_address ON web_orders (ip_address);
CREATE INDEX ix_web_orders_key_id ON web_orders (key_id);
CREATE INDEX ix_web_orders_organization_id ON web_orders (organization_id);
CREATE INDEX ix_web_orders_project_id ON web_orders (project_id);
CREATE INDEX ix_web_orders_status ON web_orders (status);

ALTER TABLE web_orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON web_orders USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON web_orders TO %I', app_role);
END $$;
