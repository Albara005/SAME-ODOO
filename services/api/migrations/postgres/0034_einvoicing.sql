-- 0034: ZATCA e-invoicing phase 2 (novara/einvoice): the organization's device and the signed documents it
-- issued; customers' national address parts and taxes' VAT categories that e-invoices need.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

ALTER TABLE customers ADD COLUMN building_number VARCHAR(10) DEFAULT '' NOT NULL;
ALTER TABLE customers ADD COLUMN district VARCHAR(100) DEFAULT '' NOT NULL;
ALTER TABLE vendors ADD COLUMN building_number VARCHAR(10) DEFAULT '' NOT NULL;
ALTER TABLE vendors ADD COLUMN district VARCHAR(100) DEFAULT '' NOT NULL;
ALTER TABLE taxes ADD COLUMN zatca_category VARCHAR(1) DEFAULT '' NOT NULL;
ALTER TABLE taxes ADD COLUMN exemption_code VARCHAR(40) DEFAULT '' NOT NULL;
ALTER TABLE taxes ADD COLUMN exemption_reason VARCHAR(200) DEFAULT '' NOT NULL;
ALTER TABLE taxes ADD CONSTRAINT ck_tax_zatca_category CHECK (zatca_category IN ('', 'S', 'Z', 'E', 'O'));

CREATE TABLE einvoice_settings (
	environment VARCHAR(12) NOT NULL,
	status VARCHAR(12) NOT NULL,
	crn VARCHAR(20) NOT NULL,
	street VARCHAR(120) NOT NULL,
	building_number VARCHAR(10) NOT NULL,
	district VARCHAR(120) NOT NULL,
	city VARCHAR(120) NOT NULL,
	postal_code VARCHAR(10) NOT NULL,
	branch_name VARCHAR(64) NOT NULL,
	industry VARCHAR(64) NOT NULL,
	private_key TEXT,
	compliance_request_id VARCHAR(40) NOT NULL,
	compliance_token TEXT,
	compliance_secret TEXT,
	production_token TEXT,
	production_secret TEXT,
	certificate_expires_at TIMESTAMP WITH TIME ZONE,
	onboarded_at TIMESTAMP WITH TIME ZONE,
	onboarded_by VARCHAR(36),
	icv INTEGER NOT NULL,
	last_hash VARCHAR(100) NOT NULL,
	last_error VARCHAR(500) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_einvoice_settings_org UNIQUE (organization_id),
	CONSTRAINT ck_einvoice_environment CHECK (environment IN ('sandbox', 'simulation', 'production')),
	CONSTRAINT ck_einvoice_status CHECK (status IN ('off', 'onboarded')),
	FOREIGN KEY(onboarded_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_einvoice_settings_organization_id ON einvoice_settings (organization_id);

CREATE TABLE einvoice_documents (
	source_type VARCHAR(12) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	number VARCHAR(40) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	type_code VARCHAR(3) NOT NULL,
	environment VARCHAR(12) NOT NULL,
	icv INTEGER NOT NULL,
	uuid VARCHAR(36) NOT NULL,
	invoice_hash VARCHAR(100) NOT NULL,
	xml TEXT NOT NULL,
	qr TEXT NOT NULL,
	status VARCHAR(10) NOT NULL,
	warnings JSON NOT NULL,
	errors JSON NOT NULL,
	note VARCHAR(300) NOT NULL,
	cleared_xml TEXT,
	attempts INTEGER NOT NULL,
	last_attempt_at TIMESTAMP WITH TIME ZONE,
	accepted_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_einvoice_document_source UNIQUE (organization_id, source_type, source_id),
	CONSTRAINT uq_einvoice_document_uuid UNIQUE (organization_id, uuid),
	CONSTRAINT ck_einvoice_document_source CHECK (source_type IN ('invoice', 'credit_note')),
	CONSTRAINT ck_einvoice_document_kind CHECK (kind IN ('standard', 'simplified')),
	CONSTRAINT ck_einvoice_document_status CHECK (status IN ('pending', 'reported', 'cleared', 'rejected')),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_einvoice_documents_organization_id ON einvoice_documents (organization_id);
CREATE INDEX ix_einvoice_documents_source_id ON einvoice_documents (source_id);
CREATE INDEX ix_einvoice_documents_status ON einvoice_documents (status);

ALTER TABLE einvoice_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON einvoice_settings USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE einvoice_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON einvoice_documents USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- An issued document is part of the device's chain: what was signed never changes and nothing is deleted.
-- Only what ZATCA said about it (status, messages, the cleared copy, attempts) can be updated.
CREATE FUNCTION einvoice_document_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'an issued e-invoice cannot be deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.source_type, NEW.source_id, NEW.number, NEW.kind, NEW.type_code, NEW.environment, NEW.icv, NEW.uuid,
        NEW.invoice_hash, NEW.xml, NEW.qr)
       IS DISTINCT FROM
       (OLD.source_type, OLD.source_id, OLD.number, OLD.kind, OLD.type_code, OLD.environment, OLD.icv, OLD.uuid,
        OLD.invoice_hash, OLD.xml, OLD.qr) THEN
        RAISE EXCEPTION 'an issued e-invoice cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('reported', 'cleared') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'an accepted e-invoice cannot change status' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER einvoice_document_final BEFORE UPDATE OR DELETE ON einvoice_documents
    FOR EACH ROW EXECUTE FUNCTION einvoice_document_is_final();

-- The worker sends pending documents organization by organization; this lists which have any.
CREATE FUNCTION app_orgs_with_pending_einvoices() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT DISTINCT organization_id FROM einvoice_documents WHERE status = 'pending'
    $$;
REVOKE ALL ON FUNCTION app_orgs_with_pending_einvoices() FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON einvoice_settings, einvoice_documents TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_orgs_with_pending_einvoices() TO %I', app_role);
END $$;
