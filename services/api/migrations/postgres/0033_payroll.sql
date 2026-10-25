-- 0033: payroll (novara/modules/payroll): contributions, each employee's pay, monthly pay runs and payslips.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE payroll_contributions (
	name VARCHAR(80) NOT NULL,
	employee_rate NUMERIC(7, 4) NOT NULL,
	employer_rate NUMERIC(7, 4) NOT NULL,
	base_cap_minor BIGINT,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_payroll_contribution_name UNIQUE (organization_id, name),
	CONSTRAINT ck_payroll_contribution_rates CHECK (CAST(employee_rate AS NUMERIC) BETWEEN 0 AND 100 AND CAST(employer_rate AS NUMERIC) BETWEEN 0 AND 100),
	CONSTRAINT ck_payroll_contribution_cap CHECK (base_cap_minor IS NULL OR base_cap_minor > 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_payroll_contributions_organization_id ON payroll_contributions (organization_id);

CREATE TABLE employee_pay (
	employee_id VARCHAR(36) NOT NULL,
	iban VARCHAR(34) NOT NULL,
	contribution_ids JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_employee_pay_employee UNIQUE (organization_id, employee_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_employee_pay_employee_id ON employee_pay (employee_id);
CREATE INDEX ix_employee_pay_organization_id ON employee_pay (organization_id);

CREATE TABLE pay_items (
	employee_id VARCHAR(36) NOT NULL,
	position INTEGER NOT NULL,
	name VARCHAR(80) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	amount_minor BIGINT NOT NULL,
	contributory BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT ck_pay_item_kind CHECK (kind IN ('earning', 'deduction')),
	CONSTRAINT ck_pay_item_amount CHECK (amount_minor > 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_pay_items_employee_id ON pay_items (employee_id);
CREATE INDEX ix_pay_items_organization_id ON pay_items (organization_id);

CREATE TABLE pay_runs (
	period_start DATE NOT NULL,
	period_end DATE NOT NULL,
	status VARCHAR(10) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	gross_minor BIGINT NOT NULL,
	deductions_minor BIGINT NOT NULL,
	employee_contributions_minor BIGINT NOT NULL,
	employer_contributions_minor BIGINT NOT NULL,
	net_minor BIGINT NOT NULL,
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
	FOREIGN KEY(organization_id, journal_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, payment_entry_id) REFERENCES journal_entries (organization_id, id),
	FOREIGN KEY(organization_id, reversal_entry_id) REFERENCES journal_entries (organization_id, id),
	CONSTRAINT ck_pay_run_status CHECK (status IN ('draft', 'posted', 'paid', 'cancelled')),
	CONSTRAINT ck_pay_run_period CHECK (period_end >= period_start),
	FOREIGN KEY(posted_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_pay_runs_organization_id ON pay_runs (organization_id);
CREATE INDEX ix_pay_runs_status ON pay_runs (status);
CREATE UNIQUE INDEX uq_pay_run_month ON pay_runs (organization_id, period_start) WHERE status <> 'cancelled';

CREATE TABLE payslips (
	run_id VARCHAR(36) NOT NULL,
	employee_id VARCHAR(36) NOT NULL,
	employee_name VARCHAR(200) NOT NULL,
	iban VARCHAR(34) NOT NULL,
	days_in_period INTEGER NOT NULL,
	days_paid NUMERIC(5, 1) NOT NULL,
	unpaid_leave_days NUMERIC(5, 1) NOT NULL,
	lines JSON NOT NULL,
	gross_minor BIGINT NOT NULL,
	deductions_minor BIGINT NOT NULL,
	employee_contributions_minor BIGINT NOT NULL,
	employer_contributions_minor BIGINT NOT NULL,
	net_minor BIGINT NOT NULL,
	id VARCHAR(36) NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, run_id) REFERENCES pay_runs (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id),
	CONSTRAINT uq_payslip_run_employee UNIQUE (organization_id, run_id, employee_id),
	CONSTRAINT ck_payslip_net CHECK (net_minor >= 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_payslips_employee_id ON payslips (employee_id);
CREATE INDEX ix_payslips_organization_id ON payslips (organization_id);
CREATE INDEX ix_payslips_run_id ON payslips (run_id);

ALTER TABLE payroll_contributions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON payroll_contributions USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE employee_pay ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON employee_pay USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE pay_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pay_items USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE pay_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pay_runs USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE payslips ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON payslips USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- A posted run is final: its amounts and entries can't change and it can't be deleted (it is cancelled with a
-- reversing entry instead), and its payslips can't be changed or removed.
CREATE FUNCTION pay_run_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'a posted pay run cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.period_start, NEW.period_end, NEW.currency, NEW.gross_minor, NEW.deductions_minor,
        NEW.employee_contributions_minor, NEW.employer_contributions_minor, NEW.net_minor, NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.period_start, OLD.period_end, OLD.currency, OLD.gross_minor, OLD.deductions_minor,
        OLD.employee_contributions_minor, OLD.employer_contributions_minor, OLD.net_minor, OLD.journal_entry_id
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

CREATE TRIGGER pay_run_final BEFORE UPDATE OR DELETE ON pay_runs
    FOR EACH ROW EXECUTE FUNCTION pay_run_is_final();

CREATE FUNCTION payslip_requires_draft() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    run_status text;
BEGIN
    SELECT status INTO run_status FROM pay_runs
     WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.run_id ELSE NEW.run_id END;
    IF run_status IS NOT NULL AND run_status <> 'draft' THEN
        RAISE EXCEPTION 'payslips of a posted pay run cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER payslip_draft_only BEFORE INSERT OR UPDATE OR DELETE ON payslips
    FOR EACH ROW EXECUTE FUNCTION payslip_requires_draft();

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON payroll_contributions, employee_pay TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON pay_items, pay_runs TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON payslips TO %I', app_role);
END $$;
