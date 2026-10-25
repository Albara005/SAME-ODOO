-- 0042: exchange rates by day, and revaluing what customers still owe in other currencies at a period's end
-- (novara/modules/invoicing/fx.py): the unrealized difference is posted on the day and reversed the next.

CREATE TABLE exchange_rates (
	currency VARCHAR(3) NOT NULL,
	day DATE NOT NULL,
	rate NUMERIC(18, 8) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_exchange_rate_day UNIQUE (organization_id, currency, day),
	CONSTRAINT ck_exchange_rate_positive CHECK (CAST(rate AS NUMERIC) > 0),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_exchange_rates_organization_id ON exchange_rates (organization_id);

CREATE TABLE fx_revaluations (
	as_of DATE NOT NULL,
	status VARCHAR(10) NOT NULL,
	rates JSON NOT NULL,
	lines JSON NOT NULL,
	difference_minor BIGINT NOT NULL,
	entry_id VARCHAR(36),
	reversal_entry_id VARCHAR(36),
	cancel_entry_id VARCHAR(36),
	cancel_reversal_entry_id VARCHAR(36),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, cancel_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, cancel_reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_fx_revaluation_status CHECK (status IN ('posted', 'cancelled')),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_fx_revaluations_organization_id ON fx_revaluations (organization_id);
CREATE UNIQUE INDEX uq_fx_revaluation_day ON fx_revaluations (organization_id, as_of) WHERE status = 'posted';

ALTER TABLE exchange_rates ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON exchange_rates USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE fx_revaluations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fx_revaluations USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON exchange_rates TO %I', app_role);
    -- A revaluation is cancelled (status and the cancelling entries), never deleted.
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON fx_revaluations TO %I', app_role);
END $$;
