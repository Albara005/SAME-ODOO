-- 0006: inventory, sales orders and idempotency keys.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE idempotency_keys (
	scope VARCHAR(60) NOT NULL,
	key VARCHAR(100) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	resource_id VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_idempotency_scope_key UNIQUE (organization_id, scope, key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_idempotency_keys_organization_id ON idempotency_keys (organization_id);

CREATE TABLE warehouses (
	code VARCHAR(20) NOT NULL,
	name VARCHAR(200) NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_warehouse_org_code UNIQUE (organization_id, code),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_warehouses_organization_id ON warehouses (organization_id);

CREATE TABLE sales_orders (
	number VARCHAR(40) NOT NULL,
	customer_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	order_date DATE NOT NULL,
	valid_until DATE,
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
	CONSTRAINT uq_sales_order_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	FOREIGN KEY(organization_id, warehouse_id) REFERENCES warehouses (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_sales_orders_customer_id ON sales_orders (customer_id);
CREATE INDEX ix_sales_orders_organization_id ON sales_orders (organization_id);
CREATE INDEX ix_sales_orders_status ON sales_orders (status);

CREATE TABLE sales_order_lines (
	order_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	product_id VARCHAR(36),
	description VARCHAR(500) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	unit_price NUMERIC(18, 6) NOT NULL,
	discount_percent NUMERIC(7, 4) NOT NULL,
	tax_id VARCHAR(36),
	tax_rate NUMERIC(7, 4) NOT NULL,
	net_minor BIGINT NOT NULL,
	qty_delivered NUMERIC(18, 4) NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, order_id) REFERENCES sales_orders (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, tax_id) REFERENCES taxes (organization_id, id),
	CONSTRAINT ck_so_line_qty_positive CHECK (CAST(quantity AS NUMERIC) > 0),
	CONSTRAINT ck_so_line_price_nonnegative CHECK (CAST(unit_price AS NUMERIC) >= 0),
	CONSTRAINT ck_so_line_discount CHECK (CAST(discount_percent AS NUMERIC) >= 0 AND CAST(discount_percent AS NUMERIC) <= 100),
	CONSTRAINT ck_so_line_delivered_range CHECK (CAST(qty_delivered AS NUMERIC) >= 0 AND CAST(qty_delivered AS NUMERIC) <= CAST(quantity AS NUMERIC)),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_sales_order_lines_order_id ON sales_order_lines (order_id);
CREATE INDEX ix_sales_order_lines_organization_id ON sales_order_lines (organization_id);

CREATE TABLE stock_levels (
	product_id VARCHAR(36) NOT NULL,
	warehouse_id VARCHAR(36) NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	value_minor BIGINT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_stock_level UNIQUE (organization_id, product_id, warehouse_id),
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, warehouse_id) REFERENCES warehouses (organization_id, id),
	CONSTRAINT ck_stock_level_nonnegative CHECK (CAST(quantity AS NUMERIC) >= 0),
	CONSTRAINT ck_stock_value_nonnegative CHECK (value_minor >= 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_stock_levels_organization_id ON stock_levels (organization_id);

CREATE TABLE stock_moves (
	reference VARCHAR(40) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	product_id VARCHAR(36) NOT NULL,
	warehouse_id VARCHAR(36) NOT NULL,
	move_date DATE NOT NULL,
	quantity NUMERIC(18, 4) NOT NULL,
	value_minor BIGINT NOT NULL,
	source_type VARCHAR(40) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	source_line_id VARCHAR(36),
	journal_entry_id VARCHAR(36),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, product_id) REFERENCES products (organization_id, id),
	FOREIGN KEY(organization_id, warehouse_id) REFERENCES warehouses (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_stock_move_nonzero CHECK (CAST(quantity AS NUMERIC) <> 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_stock_moves_organization_id ON stock_moves (organization_id);
CREATE INDEX ix_stock_moves_product_id ON stock_moves (product_id);
CREATE INDEX ix_stock_moves_reference ON stock_moves (reference);

-- Invoice lines created from a sales order keep a link to the order line.
ALTER TABLE invoice_lines ADD COLUMN sales_order_line_id VARCHAR(36);
ALTER TABLE invoice_lines ADD FOREIGN KEY (organization_id, sales_order_line_id)
    REFERENCES sales_order_lines (organization_id, id);
CREATE INDEX ix_invoice_lines_sales_order_line_id ON invoice_lines (sales_order_line_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['warehouses', 'stock_levels', 'stock_moves', 'sales_orders', 'sales_order_lines',
                             'idempotency_keys']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- Runtime role privileges. Stock moves are the inventory history: insert-only.
DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON warehouses, sales_orders, sales_order_lines TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON stock_levels TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON stock_moves, idempotency_keys TO %I', app_role);
END $$;
