-- 0004: business core: customers, products, taxes, double-entry ledger, invoices, payments.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE accounts (
	code VARCHAR(20) NOT NULL,
	name VARCHAR(200) NOT NULL,
	type VARCHAR(20) NOT NULL,
	system_key VARCHAR(40),
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_account_org_code UNIQUE (organization_id, code),
	CONSTRAINT uq_account_org_system_key UNIQUE (organization_id, system_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_accounts_organization_id ON accounts (organization_id);

CREATE TABLE customers (
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

CREATE INDEX ix_customers_name ON customers (name);
CREATE INDEX ix_customers_organization_id ON customers (organization_id);

CREATE TABLE journal_entries (
	number VARCHAR(40) NOT NULL,
	entry_date DATE NOT NULL,
	currency VARCHAR(3) NOT NULL,
	memo VARCHAR(300) NOT NULL,
	source_type VARCHAR(40) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	reversal_of_id VARCHAR(36),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_journal_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, reversal_of_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_journal_entries_organization_id ON journal_entries (organization_id);

CREATE TABLE number_sequences (
	key VARCHAR(60) NOT NULL,
	next_value INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_sequence_org_key UNIQUE (organization_id, key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_number_sequences_organization_id ON number_sequences (organization_id);

CREATE TABLE invoices (
	number VARCHAR(40),
	customer_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	issue_date DATE NOT NULL,
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
	CONSTRAINT uq_invoice_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_invoice_paid_range CHECK (amount_paid_minor >= 0 AND amount_paid_minor <= total_minor),
	FOREIGN KEY(posted_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_invoices_customer_id ON invoices (customer_id);
CREATE INDEX ix_invoices_organization_id ON invoices (organization_id);
CREATE INDEX ix_invoices_status ON invoices (status);

CREATE TABLE journal_lines (
	entry_id VARCHAR(36) NOT NULL,
	account_id VARCHAR(36) NOT NULL,
	debit_minor BIGINT NOT NULL,
	credit_minor BIGINT NOT NULL,
	customer_id VARCHAR(36),
	label VARCHAR(300) NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, entry_id) REFERENCES journal_entries (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, account_id) REFERENCES accounts (organization_id, id),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	CONSTRAINT ck_journal_line_one_side CHECK (debit_minor >= 0 AND credit_minor >= 0 AND (debit_minor = 0) <> (credit_minor = 0)),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_journal_lines_account_id ON journal_lines (account_id);
CREATE INDEX ix_journal_lines_customer_id ON journal_lines (customer_id);
CREATE INDEX ix_journal_lines_entry_id ON journal_lines (entry_id);
CREATE INDEX ix_journal_lines_organization_id ON journal_lines (organization_id);

CREATE TABLE taxes (
	name VARCHAR(100) NOT NULL,
	rate NUMERIC(7, 4) NOT NULL,
	account_id VARCHAR(36) NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, account_id) REFERENCES accounts (organization_id, id),
	CONSTRAINT ck_tax_rate_range CHECK (CAST(rate AS NUMERIC) >= 0 AND CAST(rate AS NUMERIC) <= 100),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_taxes_organization_id ON taxes (organization_id);

CREATE TABLE payments (
	invoice_id VARCHAR(36) NOT NULL,
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
	FOREIGN KEY(organization_id, invoice_id) REFERENCES invoices (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT uq_payment_org_idempotency UNIQUE (organization_id, idempotency_key),
	CONSTRAINT ck_payment_amount_positive CHECK (amount_minor > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_payments_invoice_id ON payments (invoice_id);
CREATE INDEX ix_payments_organization_id ON payments (organization_id);

CREATE TABLE products (
	name VARCHAR(200) NOT NULL,
	sku VARCHAR(64),
	kind VARCHAR(20) NOT NULL,
	unit VARCHAR(20) NOT NULL,
	sale_price NUMERIC(18, 6) NOT NULL,
	default_tax_id VARCHAR(36),
	description TEXT NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_product_org_sku UNIQUE (organization_id, sku),
	FOREIGN KEY(organization_id, default_tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_product_price_nonnegative CHECK (CAST(sale_price AS NUMERIC) >= 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_products_name ON products (name);
CREATE INDEX ix_products_organization_id ON products (organization_id);

CREATE TABLE invoice_lines (
	invoice_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_price NUMERIC(18, 6) NOT NULL,
	discount_percent NUMERIC(7, 4) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, invoice_id) REFERENCES invoices (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_invoice_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_invoice_line_price_nonnegative CHECK (CAST(unit_price AS NUMERIC) >= 0),
	CONSTRAINT ck_invoice_line_discount CHECK (CAST(discount_percent AS NUMERIC) >= 0 AND CAST(discount_percent AS NUMERIC) <= 100),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_invoice_lines_invoice_id ON invoice_lines (invoice_id);
CREATE INDEX ix_invoice_lines_organization_id ON invoice_lines (organization_id);
