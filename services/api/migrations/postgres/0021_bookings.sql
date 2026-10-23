-- 0021: bookings: the booking rules and opening hours, bookable services, and bookings on one calendar.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE booking_settings (
	timezone VARCHAR(64) NOT NULL,
	slot_minutes INTEGER NOT NULL,
	notice_hours INTEGER NOT NULL,
	days_ahead INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_booking_settings_org UNIQUE (organization_id),
	CONSTRAINT ck_booking_slot CHECK (slot_minutes IN (15, 30, 60)),
	CONSTRAINT ck_booking_window CHECK (notice_hours >= 0 AND notice_hours <= 720 AND days_ahead > 0 AND days_ahead <= 366),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_booking_settings_organization_id ON booking_settings (organization_id);

CREATE TABLE opening_hours (
	weekday INTEGER NOT NULL,
	opens_at VARCHAR(5) NOT NULL,
	closes_at VARCHAR(5) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_opening_weekday CHECK (weekday BETWEEN 0 AND 6),
	CONSTRAINT ck_opening_range CHECK (opens_at < closes_at),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_opening_hours_organization_id ON opening_hours (organization_id);

CREATE TABLE bookable_services (
	name VARCHAR(120) NOT NULL,
	description TEXT NOT NULL,
	duration_minutes INTEGER NOT NULL,
	price NUMERIC(18, 6),
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_bookable_service_name UNIQUE (organization_id, name),
	CONSTRAINT ck_service_duration CHECK (duration_minutes > 0 AND duration_minutes <= 720),
	CONSTRAINT ck_service_price CHECK (price IS NULL OR CAST(price AS NUMERIC) >= 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_bookable_services_organization_id ON bookable_services (organization_id);

CREATE TABLE bookings (
	number VARCHAR(40) NOT NULL,
	service_id VARCHAR(36) NOT NULL,
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(20) NOT NULL,
	name VARCHAR(200) NOT NULL,
	email VARCHAR(320) NOT NULL,
	phone VARCHAR(40) NOT NULL,
	note VARCHAR(1000) NOT NULL,
	customer_id VARCHAR(36),
	source VARCHAR(120) NOT NULL,
	key_id VARCHAR(36),
	ip_address VARCHAR(64) NOT NULL,
	created_by VARCHAR(36),
	decided_by VARCHAR(36),
	decided_at TIMESTAMP WITH TIME ZONE,
	decision_note VARCHAR(500) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_booking_org_number UNIQUE (organization_id, number),
	FOREIGN KEY(organization_id, service_id) REFERENCES bookable_services (organization_id, id),
	FOREIGN KEY(organization_id, customer_id) REFERENCES customers (organization_id, id),
	CONSTRAINT ck_booking_range CHECK (starts_at < ends_at),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(decided_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_bookings_ip_address ON bookings (ip_address);
CREATE INDEX ix_bookings_key_id ON bookings (key_id);
CREATE INDEX ix_bookings_organization_id ON bookings (organization_id);
CREATE INDEX ix_bookings_service_id ON bookings (service_id);
CREATE INDEX ix_bookings_starts_at ON bookings (starts_at);
CREATE INDEX ix_bookings_status ON bookings (status);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['booking_settings', 'opening_hours', 'bookable_services', 'bookings']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON booking_settings, bookable_services, bookings TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON opening_hours TO %I', app_role);
END $$;
