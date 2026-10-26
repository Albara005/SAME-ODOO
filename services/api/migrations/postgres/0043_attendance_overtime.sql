-- 0043: attendance and overtime (novara/modules/hr/attendance.py). People check in and out (or HR enters their
-- hours); time beyond the policy's working day, or on a weekend day or holiday, is overtime; what HR or the
-- department's manager approves for a month is paid with that month's pay run, at the organization's overtime rule.

ALTER TABLE hr_policies ADD COLUMN timezone VARCHAR(64) DEFAULT 'Asia/Riyadh' NOT NULL;
ALTER TABLE hr_policies ADD COLUMN daily_minutes INTEGER DEFAULT 480 NOT NULL;
ALTER TABLE hr_policies ADD CONSTRAINT ck_hr_policy_daily_minutes CHECK (daily_minutes BETWEEN 60 AND 960);

-- Overtime is worked out on the basic wage: marked on the earnings that make it up.
ALTER TABLE pay_items ADD COLUMN basic BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE payroll_settings ADD COLUMN overtime_rule VARCHAR(10) DEFAULT 'none' NOT NULL;
ALTER TABLE payroll_settings ADD COLUMN overtime_multiplier NUMERIC(4, 2) DEFAULT 1.5 NOT NULL;
ALTER TABLE payroll_settings ADD CONSTRAINT ck_payroll_settings_overtime
    CHECK (overtime_rule IN ('none', 'saudi', 'multiplier'));
ALTER TABLE payroll_settings ADD CONSTRAINT ck_payroll_settings_multiplier
    CHECK (CAST(overtime_multiplier AS NUMERIC) BETWEEN 1 AND 5);

CREATE TABLE attendance_entries (
	employee_id VARCHAR(36) NOT NULL,
	day DATE NOT NULL,
	check_in TIMESTAMP WITH TIME ZONE NOT NULL,
	check_out TIMESTAMP WITH TIME ZONE,
	source VARCHAR(4) NOT NULL,
	note VARCHAR(300) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id),
	CONSTRAINT ck_attendance_times CHECK (check_out IS NULL OR check_out > check_in),
	CONSTRAINT ck_attendance_source CHECK (source IN ('self', 'hr')),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_attendance_entries_employee_id ON attendance_entries (employee_id);
CREATE INDEX ix_attendance_entries_organization_id ON attendance_entries (organization_id);
CREATE INDEX ix_attendance_entries_day ON attendance_entries (day);
-- One open check-in per person.
CREATE UNIQUE INDEX uq_attendance_open ON attendance_entries (organization_id, employee_id) WHERE check_out IS NULL;

CREATE TABLE overtime_approvals (
	employee_id VARCHAR(36) NOT NULL,
	month DATE NOT NULL,
	minutes INTEGER NOT NULL,
	recorded_minutes INTEGER NOT NULL,
	note VARCHAR(500) NOT NULL,
	approved_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id),
	CONSTRAINT uq_overtime_month UNIQUE (organization_id, employee_id, month),
	CONSTRAINT ck_overtime_minutes CHECK (minutes > 0 AND minutes <= recorded_minutes),
	FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_overtime_approvals_employee_id ON overtime_approvals (employee_id);
CREATE INDEX ix_overtime_approvals_organization_id ON overtime_approvals (organization_id);

ALTER TABLE attendance_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON attendance_entries USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE overtime_approvals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON overtime_approvals USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON attendance_entries TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON overtime_approvals TO %I', app_role);
END $$;
