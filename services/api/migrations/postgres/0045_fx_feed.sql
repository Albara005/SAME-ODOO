-- 0045: exchange rates fetched from the European Central Bank's daily reference rates (novara/modules/invoicing/
-- fx_feed.py). A rate set by hand stays; the feed's are marked as such.

ALTER TABLE exchange_rates ADD COLUMN source VARCHAR(10) DEFAULT 'manual' NOT NULL;
ALTER TABLE exchange_rates ADD CONSTRAINT ck_exchange_rate_source CHECK (source IN ('manual', 'ecb'));

CREATE TABLE fx_feeds (
	enabled BOOLEAN NOT NULL,
	currencies JSON NOT NULL,
	last_day DATE,
	last_run_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(300) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_fx_feed_org UNIQUE (organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_fx_feeds_organization_id ON fx_feeds (organization_id);

ALTER TABLE fx_feeds ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fx_feeds USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- The worker fetches the feed once and saves it for each organization that keeps rates; this lists them.
CREATE FUNCTION app_orgs_with_fx_feed() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM fx_feeds WHERE enabled
    $$;
REVOKE ALL ON FUNCTION app_orgs_with_fx_feed() FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON fx_feeds TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_orgs_with_fx_feed() TO %I', app_role);
END $$;
