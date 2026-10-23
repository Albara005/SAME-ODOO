-- 0012: closing the books, financial statements, credit notes and refunds, purchasing, e-invoice QR.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

-- Organization: seller VAT number (invoices, QR code) and the closed-books date.
ALTER TABLE organizations ADD COLUMN vat_number VARCHAR(20) NOT NULL DEFAULT '';
ALTER TABLE organizations ADD COLUMN books_locked_until DATE;

-- Invoices: the part of credit notes that reduced what is due.
ALTER TABLE invoices ADD COLUMN amount_credited_minor BIGINT NOT NULL DEFAULT 0;
ALTER TABLE invoices DROP CONSTRAINT ck_invoice_paid_range;
ALTER TABLE invoices ADD CONSTRAINT ck_invoice_paid_range CHECK (
    amount_paid_minor >= 0 AND amount_credited_minor >= 0 AND amount_paid_minor + amount_credited_minor <= total_minor);

-- Credit note lines point at the invoice line they credit (same organization).
ALTER TABLE invoice_lines ADD UNIQUE (organization_id, id);

CREATE TABLE vendors (
	name VARCHAR(200) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	email VARCHAR(320) NOT NULL,
	phone VARCHAR(40) NOT NULL,
	tax_number VARCHAR(40) NOT NULL,
	commercial_registration VARCHAR(40) NOT NULL,
	address_line VARCHAR(300) NOT NULL,
	city VARCHAR(100) NOT NULL,
	postal_code VARCHAR(20) NOT NULL,
	country VARCHAR(2) NOT NULL,
	notes TEXT NOT NULL,
	active BOOLEAN NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendors_name ON vendors (name);
CREATE INDEX ix_vendors_organization_id ON vendors (organization_id);

CREATE TABLE credit_notes (
	number VARCHAR(40) NOT NULL,
	invoice_id VARCHAR(36) NOT NULL,
	customer_id VARCHAR(36) NOT NULL,
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
	CONSTRAINT uq_credit_note_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, invoice_id) REFERENCES invoices (organization_id, id),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_credit_note_total_positive CHECK (total_minor > 0),
	CONSTRAINT ck_credit_note_settled_range CHECK (applied_minor >= 0 AND refunded_minor >= 0 AND applied_minor + refunded_minor <= total_minor),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_credit_notes_customer_id ON credit_notes (customer_id);
CREATE INDEX ix_credit_notes_invoice_id ON credit_notes (invoice_id);
CREATE INDEX ix_credit_notes_organization_id ON credit_notes (organization_id);

CREATE TABLE credit_note_lines (
	credit_note_id VARCHAR(36) NOT NULL,
	invoice_line_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_price NUMERIC(18, 6) NOT NULL,
	discount_percent NUMERIC(7, 4) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	returned_quantity NUMERIC(18, 4) NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, credit_note_id) REFERENCES credit_notes (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, invoice_line_id) REFERENCES invoice_lines (organization_id, id),
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_credit_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_credit_line_returned_range CHECK (CAST(returned_quantity AS NUMERIC) >= 0 AND CAST(returned_quantity AS NUMERIC) <= CAST(quantity AS NUMERIC)),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_credit_note_lines_credit_note_id ON credit_note_lines (credit_note_id);
CREATE INDEX ix_credit_note_lines_invoice_line_id ON credit_note_lines (invoice_line_id);
CREATE INDEX ix_credit_note_lines_organization_id ON credit_note_lines (organization_id);

CREATE TABLE credit_note_taxes (
	credit_note_id VARCHAR(36) NOT NULL,
	tax_id VARCHAR(36) NOT NULL,
	rate NUMERIC(7, 4) NOT NULL,
	base_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, credit_note_id) REFERENCES credit_notes (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_credit_note_taxes_credit_note_id ON credit_note_taxes (credit_note_id);
CREATE INDEX ix_credit_note_taxes_organization_id ON credit_note_taxes (organization_id);

CREATE TABLE refunds (
	credit_note_id VARCHAR(36) NOT NULL,
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
	FOREIGN KEY(organization_id, credit_note_id) REFERENCES credit_notes (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT uq_refund_org_idempotency UNIQUE (organization_id, idempotency_key),
	CONSTRAINT ck_refund_amount_positive CHECK (amount_minor > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_refunds_credit_note_id ON refunds (credit_note_id);
CREATE INDEX ix_refunds_organization_id ON refunds (organization_id);

CREATE TABLE purchase_orders (
	number VARCHAR(40) NOT NULL,
	vendor_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	order_date DATE NOT NULL,
	expected_date DATE,
	currency VARCHAR(3) NOT NULL,
	warehouse_id VARCHAR(36),
	notes TEXT NOT NULL,
	subtotal_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	total_minor BIGINT NOT NULL,
	confirmed_at TIMESTAMP WITH TIME ZONE,
	cancelled_at TIMESTAMP WITH TIME ZONE,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_purchase_order_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, vendor_id) REFERENCES vendors (organization_id, id),
	FOREIGN KEY(organization_id, warehouse_id) REFERENCES warehouses (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_purchase_orders_organization_id ON purchase_orders (organization_id);
CREATE INDEX ix_purchase_orders_status ON purchase_orders (status);
CREATE INDEX ix_purchase_orders_vendor_id ON purchase_orders (vendor_id);

CREATE TABLE purchase_order_lines (
	order_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_cost NUMERIC(18, 6) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	qty_received NUMERIC(18, 4) NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, order_id) REFERENCES purchase_orders (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_po_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_po_line_cost_nonnegative CHECK (CAST(unit_cost AS NUMERIC) >= 0),
	CONSTRAINT ck_po_line_received_range CHECK (CAST(qty_received AS NUMERIC) >= 0 AND CAST(qty_received AS NUMERIC) <= CAST(quantity AS NUMERIC)),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_purchase_order_lines_order_id ON purchase_order_lines (order_id);
CREATE INDEX ix_purchase_order_lines_organization_id ON purchase_order_lines (organization_id);

CREATE TABLE vendor_bills (
	number VARCHAR(40),
	vendor_reference VARCHAR(60) NOT NULL,
	vendor_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	bill_date DATE NOT NULL,
	due_date DATE NOT NULL,
	currency VARCHAR(3) NOT NULL,
	notes TEXT NOT NULL,
	subtotal_minor BIGINT NOT NULL,
	tax_minor BIGINT NOT NULL,
	total_minor BIGINT NOT NULL,
	amount_paid_minor BIGINT NOT NULL,
	journal_entry_id VARCHAR(36),
	reversal_entry_id VARCHAR(36),
	posted_at TIMESTAMP WITH TIME ZONE,
	posted_by VARCHAR(36),
	cancelled_at TIMESTAMP WITH TIME ZONE,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_vendor_bill_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, vendor_id) REFERENCES vendors (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_bill_paid_range CHECK (amount_paid_minor >= 0 AND amount_paid_minor <= total_minor),
	FOREIGN KEY(posted_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_bills_organization_id ON vendor_bills (organization_id);
CREATE INDEX ix_vendor_bills_status ON vendor_bills (status);
CREATE INDEX ix_vendor_bills_vendor_id ON vendor_bills (vendor_id);

CREATE TABLE vendor_bill_lines (
	bill_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_cost NUMERIC(18, 6) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	account_id VARCHAR(36),
	purchase_order_line_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, bill_id) REFERENCES vendor_bills (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	FOREIGN KEY(organization_id, account_id) REFERENCES accounts (organization_id, id),
	FOREIGN KEY(organization_id, purchase_order_line_id) REFERENCES purchase_order_lines (organization_id, id),
	CONSTRAINT ck_bill_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_bill_line_cost_nonnegative CHECK (CAST(unit_cost AS NUMERIC) >= 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_vendor_bill_lines_bill_id ON vendor_bill_lines (bill_id);
CREATE INDEX ix_vendor_bill_lines_organization_id ON vendor_bill_lines (organization_id);
CREATE INDEX ix_vendor_bill_lines_purchase_order_line_id ON vendor_bill_lines (purchase_order_line_id);

CREATE TABLE bill_payments (
	bill_id VARCHAR(36) NOT NULL,
	payment_date DATE NOT NULL,
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
	FOREIGN KEY(organization_id, bill_id) REFERENCES vendor_bills (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT uq_bill_payment_org_idempotency UNIQUE (organization_id, idempotency_key),
	CONSTRAINT ck_bill_payment_amount_positive CHECK (amount_minor > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_bill_payments_bill_id ON bill_payments (bill_id);
CREATE INDEX ix_bill_payments_organization_id ON bill_payments (organization_id);

-- Payables subledger: journal lines name the vendor, like they name the customer.
ALTER TABLE journal_lines ADD COLUMN vendor_id VARCHAR(36);
ALTER TABLE journal_lines ADD FOREIGN KEY (organization_id, vendor_id) REFERENCES vendors (organization_id, id);
CREATE INDEX ix_journal_lines_vendor_id ON journal_lines (vendor_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['vendors', 'credit_notes', 'credit_note_lines', 'credit_note_taxes', 'refunds',
                             'purchase_orders', 'purchase_order_lines', 'vendor_bills', 'vendor_bill_lines',
                             'bill_payments']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- Closed books: nothing may be dated on or before the organization's lock date, whatever code inserts it.
CREATE FUNCTION journal_entry_in_open_period() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    locked date;
BEGIN
    SELECT books_locked_until INTO locked FROM organizations WHERE id = NEW.organization_id;
    IF locked IS NOT NULL AND NEW.entry_date <= locked THEN
        RAISE EXCEPTION 'the books are closed up to %', locked USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER journal_entry_open_period BEFORE INSERT ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION journal_entry_in_open_period();

-- Posted vendor bills are final, like posted invoices (see 0005).
CREATE FUNCTION vendor_bill_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'posted bill % cannot be deleted', OLD.number USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.vendor_id, NEW.vendor_reference, NEW.bill_date, NEW.due_date, NEW.currency, NEW.notes,
        NEW.subtotal_minor, NEW.tax_minor, NEW.total_minor, NEW.number, NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.vendor_id, OLD.vendor_reference, OLD.bill_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id
    ) THEN
        RAISE EXCEPTION 'posted bill % cannot be edited', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'cancelled bill % cannot be reopened', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER vendor_bill_final BEFORE UPDATE OR DELETE ON vendor_bills
    FOR EACH ROW EXECUTE FUNCTION vendor_bill_is_final();

CREATE FUNCTION vendor_bill_line_requires_draft() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_status text;
BEGIN
    SELECT status INTO parent_status FROM vendor_bills
     WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.bill_id ELSE NEW.bill_id END;
    IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
        RAISE EXCEPTION 'lines of a posted bill cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER vendor_bill_line_draft_only BEFORE INSERT OR UPDATE OR DELETE ON vendor_bill_lines
    FOR EACH ROW EXECUTE FUNCTION vendor_bill_line_requires_draft();

-- Credit notes, their lines and every payment are append-only for the API; a credit note's
-- applied/refunded amounts change as refunds are paid.
DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON vendors, purchase_orders, purchase_order_lines,'
                   ' vendor_bills, vendor_bill_lines TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON credit_notes TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON credit_note_lines, credit_note_taxes, refunds, bill_payments TO %I', app_role);
END $$;
