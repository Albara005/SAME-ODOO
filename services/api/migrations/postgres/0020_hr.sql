-- 0020: people: departments, employee records, leave types, public holidays, the weekend, and leave requests.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE hr_policies (
	weekend JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_hr_policy_org UNIQUE (organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_hr_policies_organization_id ON hr_policies (organization_id);

CREATE TABLE holidays (
	day DATE NOT NULL,
	name VARCHAR(120) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_holiday_day UNIQUE (organization_id, day),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_holidays_organization_id ON holidays (organization_id);

CREATE TABLE departments (
	name VARCHAR(120) NOT NULL,
	manager_user_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_department_name UNIQUE (organization_id, name),
	FOREIGN KEY(manager_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_departments_organization_id ON departments (organization_id);

CREATE TABLE employees (
	full_name VARCHAR(200) NOT NULL,
	job_title VARCHAR(120) NOT NULL,
	department_id VARCHAR(36),
	user_id VARCHAR(36),
	work_email VARCHAR(320) NOT NULL,
	phone VARCHAR(40) NOT NULL,
	hire_date DATE,
	end_date DATE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, department_id) REFERENCES departments (organization_id, id),
	CONSTRAINT uq_employee_user UNIQUE (organization_id, user_id),
	CONSTRAINT ck_employee_dates CHECK (end_date IS NULL OR hire_date IS NULL OR end_date >= hire_date),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_employees_department_id ON employees (department_id);
CREATE INDEX ix_employees_organization_id ON employees (organization_id);

CREATE TABLE leave_types (
	name VARCHAR(80) NOT NULL,
	annual_days INTEGER,
	paid BOOLEAN NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_leave_type_name UNIQUE (organization_id, name),
	CONSTRAINT ck_leave_type_days CHECK (annual_days IS NULL OR annual_days > 0),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_leave_types_organization_id ON leave_types (organization_id);

CREATE TABLE leave_requests (
	employee_id VARCHAR(36) NOT NULL,
	leave_type_id VARCHAR(36) NOT NULL,
	start_date DATE NOT NULL,
	end_date DATE NOT NULL,
	days INTEGER NOT NULL,
	reason VARCHAR(500) NOT NULL,
	status VARCHAR(20) NOT NULL,
	requested_by VARCHAR(36),
	decided_by VARCHAR(36),
	decided_at TIMESTAMP WITH TIME ZONE,
	decision_note VARCHAR(500) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, employee_id) REFERENCES employees (organization_id, id),
	FOREIGN KEY(organization_id, leave_type_id) REFERENCES leave_types (organization_id, id),
	CONSTRAINT ck_leave_dates CHECK (end_date >= start_date),
	CONSTRAINT ck_leave_days CHECK (days > 0),
	FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(decided_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_leave_requests_employee_id ON leave_requests (employee_id);
CREATE INDEX ix_leave_requests_organization_id ON leave_requests (organization_id);
CREATE INDEX ix_leave_requests_status ON leave_requests (status);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['hr_policies', 'holidays', 'departments', 'employees', 'leave_types', 'leave_requests']
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
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON hr_policies, departments, employees, leave_types, leave_requests TO %I',
                   app_role);
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON holidays TO %I', app_role);
END $$;
