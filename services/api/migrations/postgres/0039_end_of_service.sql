-- 0039: end-of-service benefits (novara/modules/payroll/eosb.py). The organization picks its market's rules; earnings
-- marked as counting for end of service make up the wage; each posted pay run grows the provision to the full award
-- everyone would get that day; a leaver's settlement releases it and pays the award and unused leave.

-- Earnings that count for social insurance (basic salary and housing, usually) count for end of service too, until
-- someone changes them.
ALTER TABLE pay_items ADD COLUMN end_of_service BOOLEAN DEFAULT false NOT NULL;
UPDATE pay_items SET end_of_service = contributory WHERE kind = 'earning';

ALTER TABLE pay_runs ADD COLUMN eosb_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE payslips ADD COLUMN service_days INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE payslips ADD COLUMN eosb_liability_minor BIGINT DEFAULT 0 NOT NULL;

CREATE TABLE payroll_settings (
	eosb_rule VARCHAR(10) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_payroll_settings_org UNIQUE (organization_id),
	CONSTRAINT ck_payroll_settings_rule CHECK (eosb_rule IN ('saudi', 'uae', 'none')),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_payroll_settings_organization_id ON payroll_settings (organization_id);

CREATE TABLE end_of_service (
	employee_id VARCHAR(36) NOT NULL,
	employee_name VARCHAR(200) NOT NULL,
	status VARCHAR(10) NOT NULL,
	rule VARCHAR(10) NOT NULL,
	reason VARCHAR(20) NOT NULL,
	hire_date DATE NOT NULL,
	last_day DATE NOT NULL,
	currency VARCHAR(3) NOT NULL,
	service_days INTEGER NOT NULL,
	wage_minor BIGINT NOT NULL,
	months VARCHAR(40) NOT NULL,
	share VARCHAR(10) NOT NULL,
	full_award_minor BIGINT NOT NULL,
	award_minor BIGINT NOT NULL,
	leave_days NUMERIC(5, 1) NOT NULL,
	leave_pay_minor BIGINT NOT NULL,
	provision_minor BIGINT NOT NULL,
	notes TEXT NOT NULL,
	journal_entry_id VARCHAR(36),
	payment_entry_id VARCHAR(36),
	reversal_entry_id VARCHAR(36),
	payment_date DATE,
	posted_at TIMESTAMP WITH TIME ZONE,
	posted_by VARCHAR(36),
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id),
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, payment_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_end_of_service_status CHECK (status IN ('draft', 'posted', 'paid', 'cancelled')),
	CONSTRAINT ck_end_of_service_amounts CHECK (award_minor >= 0 AND leave_pay_minor >= 0 AND provision_minor >= 0),
	CONSTRAINT ck_end_of_service_leave_days CHECK (CAST(leave_days AS NUMERIC) >= 0),
	FOREIGN KEY(posted_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_end_of_service_employee ON end_of_service (organization_id, employee_id) WHERE status <> 'cancelled';
CREATE INDEX ix_end_of_service_organization_id ON end_of_service (organization_id);
CREATE INDEX ix_end_of_service_employee_id ON end_of_service (employee_id);

ALTER TABLE payroll_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON payroll_settings USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE end_of_service ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON end_of_service USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- A posted run's provision change is as final as the rest of it.
CREATE OR REPLACE FUNCTION pay_run_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'a posted pay run cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.period_start, NEW.period_end, NEW.currency, NEW.gross_minor, NEW.deductions_minor,
        NEW.employee_contributions_minor, NEW.employer_contributions_minor, NEW.net_minor, NEW.eosb_minor,
        NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.period_start, OLD.period_end, OLD.currency, OLD.gross_minor, OLD.deductions_minor,
        OLD.employee_contributions_minor, OLD.employer_contributions_minor, OLD.net_minor, OLD.eosb_minor,
        OLD.journal_entry_id
    ) THEN
        RAISE EXCEPTION 'a posted pay run cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
        RAISE EXCEPTION 'a posted pay run cannot go back to draft' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'a cancelled pay run cannot be reopened' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- A posted settlement is final in the same way: cancelled with a reversing entry, never edited or deleted.
CREATE FUNCTION end_of_service_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'a posted end-of-service settlement cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.employee_id, NEW.rule, NEW.reason, NEW.hire_date, NEW.last_day, NEW.currency, NEW.service_days,
        NEW.wage_minor, NEW.months, NEW.share, NEW.full_award_minor, NEW.award_minor, NEW.leave_days,
        NEW.leave_pay_minor, NEW.provision_minor, NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.employee_id, OLD.rule, OLD.reason, OLD.hire_date, OLD.last_day, OLD.currency, OLD.service_days,
        OLD.wage_minor, OLD.months, OLD.share, OLD.full_award_minor, OLD.award_minor, OLD.leave_days,
        OLD.leave_pay_minor, OLD.provision_minor, OLD.journal_entry_id
    ) THEN
        RAISE EXCEPTION 'a posted end-of-service settlement cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
        RAISE EXCEPTION 'a posted end-of-service settlement cannot go back to draft' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('paid', 'cancelled') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'a % end-of-service settlement cannot change status', OLD.status USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER end_of_service_final BEFORE UPDATE OR DELETE ON end_of_service
    FOR EACH ROW EXECUTE FUNCTION end_of_service_is_final();

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON payroll_settings TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON end_of_service TO %I', app_role);
END $$;
