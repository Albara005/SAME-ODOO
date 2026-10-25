-- 0032: online payments (novara/payments): each organization's provider settings, and payment links on invoices.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

-- A payment link points at the payment it made, within the same organization.
ALTER TABLE payments ADD CONSTRAINT uq_payments_org_id UNIQUE (organization_id, id);

CREATE TABLE payment_settings (
	provider VARCHAR(10) NOT NULL,
	stripe_secret_key TEXT,
	stripe_key_hint VARCHAR(16) NOT NULL,
	stripe_webhook_secret TEXT,
	webhook_token TEXT NOT NULL,
	webhook_hash VARCHAR(64) NOT NULL,
	updated_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payment_settings_org UNIQUE (organization_id),
	CONSTRAINT ck_payment_settings_provider CHECK (provider IN ('none', 'stripe', 'test')),
	UNIQUE (webhook_hash),
	FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_payment_settings_organization_id ON payment_settings (organization_id);

CREATE TABLE payment_links (
	invoice_id VARCHAR(36) NOT NULL,
	provider VARCHAR(10) NOT NULL,
	session_id VARCHAR(255) NOT NULL,
	url TEXT NOT NULL,
	amount_minor BIGINT NOT NULL,
	currency VARCHAR(3) NOT NULL,
	status VARCHAR(10) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	paid_at TIMESTAMP WITH TIME ZONE,
	payment_id VARCHAR(36),
	provider_reference VARCHAR(255) NOT NULL,
	note VARCHAR(300) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, invoice_id) REFERENCES invoices (organization_id, id),
	FOREIGN KEY(organization_id, payment_id) REFERENCES payments (organization_id, id),
	CONSTRAINT uq_payment_link_session UNIQUE (provider, session_id),
	CONSTRAINT ck_payment_link_amount_positive CHECK (amount_minor > 0),
	CONSTRAINT ck_payment_link_status CHECK (status IN ('open', 'paid', 'unapplied', 'expired', 'cancelled')),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_payment_links_organization_id ON payment_links (organization_id);
CREATE INDEX ix_payment_links_status ON payment_links (status);
CREATE INDEX ix_payment_links_invoice_id ON payment_links (invoice_id);

ALTER TABLE payment_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON payment_settings USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE payment_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON payment_links USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- A provider's call carries only the webhook address (or, for the test provider, the checkout session),
-- so the API can't see the row before it knows the organization. These answer just that.
CREATE FUNCTION payment_webhook_org(p_hash text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM payment_settings WHERE webhook_hash = p_hash
    $$;
REVOKE ALL ON FUNCTION payment_webhook_org(text) FROM PUBLIC;
CREATE FUNCTION payment_link_org(p_provider text, p_session text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM payment_links WHERE provider = p_provider AND session_id = p_session
    $$;
REVOKE ALL ON FUNCTION payment_link_org(text, text) FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON payment_settings TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON payment_links TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION payment_webhook_org(text) TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION payment_link_org(text, text) TO %I', app_role);
END $$;
