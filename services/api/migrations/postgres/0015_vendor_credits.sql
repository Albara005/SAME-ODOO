-- 0015: vendor credits and goods sent back to vendors; retained earnings and manual journal entries
-- need no schema change (the account is created with the chart, entries use the existing tables).
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

-- Bills: the part of vendor credits that reduced what is due.
ALTER TABLE vendor_bills ADD COLUMN amount_credited_minor BIGINT NOT NULL DEFAULT 0;
ALTER TABLE vendor_bills DROP CONSTRAINT ck_bill_paid_range;
ALTER TABLE vendor_bills ADD CONSTRAINT ck_bill_paid_range CHECK (
    amount_paid_minor >= 0 AND amount_credited_minor >= 0 AND amount_paid_minor + amount_credited_minor <= total_minor);

-- An entry is reversed at most once (manual entries and year-end closes can be reversed on request,
-- and two requests at once must not both succeed; the API role can't lock ledger rows).
CREATE UNIQUE INDEX uq_journal_entry_reversed_once ON journal_entries (organization_id, reversal_of_id)
    WHERE reversal_of_id IS NOT NULL;

-- Vendor credit lines point at the bill line they credit (same organization).
ALTER TABLE vendor_bill_lines ADD UNIQUE (organization_id, id);

CREATE TABLE vendor_credits (
	number VARCHAR(40) NOT NULL,
	bill_id VARCHAR(36) NOT NULL,
	vendor_id VARCHAR(36) NOT NULL,
	vendor_reference VARCHAR(60) NOT NULL,
	issue_date DATE NOT NULL,
	currency VARCHAR(3) NOT NULL,
	reason VARCHAR(500) NOT NULL,
	subtotal_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	total_minor BIGINT NOT NULL,
	applied_minor BIGINT NOT NULL,
	refunded_minor BIGINT NOT NULL,
	journal_entry_id VARCHAR(36) NOT NULL,
	stock_reference VARCHAR(40) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_vendor_credit_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, bill_id) REFERENCES vendor_bills (organization_id, id),
	FOREIGN KEY(organization_id, vendor_id) REFERENCES vendors (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_vendor_credit_total_positive CHECK (total_minor > 0),
	CONSTRAINT ck_vendor_credit_settled_range CHECK (applied_minor >= 0 AND refunded_minor >= 0 AND applied_minor + refunded_minor <= total_minor),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_credits_bill_id ON vendor_credits (bill_id);
CREATE INDEX ix_vendor_credits_organization_id ON vendor_credits (organization_id);
CREATE INDEX ix_vendor_credits_vendor_id ON vendor_credits (vendor_id);

CREATE TABLE vendor_credit_lines (
	credit_id VARCHAR(36) NOT NULL,
	bill_line_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_cost NUMERIC(18, 6) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	returned_quantity NUMERIC(18, 4) NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, credit_id) REFERENCES vendor_credits (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, bill_line_id) REFERENCES vendor_bill_lines (organization_id, id),
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_vendor_credit_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_vendor_credit_line_returned_range CHECK (CAST(returned_quantity AS NUMERIC) >= 0 AND CAST(returned_quantity AS NUMERIC) <= CAST(quantity AS NUMERIC)),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_credit_lines_bill_line_id ON vendor_credit_lines (bill_line_id);
CREATE INDEX ix_vendor_credit_lines_credit_id ON vendor_credit_lines (credit_id);
CREATE INDEX ix_vendor_credit_lines_organization_id ON vendor_credit_lines (organization_id);

CREATE TABLE vendor_credit_taxes (
	credit_id VARCHAR(36) NOT NULL,
	tax_id VARCHAR(36) NOT NULL,
	rate NUMERIC(7, 4) NOT NULL,
	base_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, credit_id) REFERENCES vendor_credits (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_credit_taxes_credit_id ON vendor_credit_taxes (credit_id);
CREATE INDEX ix_vendor_credit_taxes_organization_id ON vendor_credit_taxes (organization_id);

CREATE TABLE vendor_refunds (
	credit_id VARCHAR(36) NOT NULL,
	refund_date DATE NOT NULL,
	amount_minor BIGINT NOT NULL,
	method VARCHAR(20) NOT NULL,
	reference VARCHAR(120) NOT NULL,
	journal_entry_id VARCHAR(36) NOT NULL,
	idempotency_key VARCHAR(100),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, credit_id) REFERENCES vendor_credits (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT uq_vendor_refund_org_idempotency UNIQUE (organization_id, idempotency_key),
	CONSTRAINT ck_vendor_refund_amount_positive CHECK (amount_minor > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_refunds_credit_id ON vendor_refunds (credit_id);
CREATE INDEX ix_vendor_refunds_organization_id ON vendor_refunds (organization_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['vendor_credits', 'vendor_credit_lines', 'vendor_credit_taxes', 'vendor_refunds']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- Like customer credit notes: append-only for the API, except a credit's refunded amount.
DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON vendor_credits TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON vendor_credit_lines, vendor_credit_taxes, vendor_refunds TO %I', app_role);
END $$;
