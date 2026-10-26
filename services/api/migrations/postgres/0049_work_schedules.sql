-- 0049: work schedules and reduced hours (novara/modules/hr/attendance.py). A work schedule is a working week (the
-- minutes of each weekday, 0 for a day off) that overtime is counted against and leave days follow; people without
-- one follow the HR policy. A reduced-hours period (Ramadan) caps the working day for the people it applies to.

CREATE TABLE work_schedules (
	name VARCHAR(80) NOT NULL,
	minutes JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_work_schedule_name UNIQUE (organization_id, name),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_work_schedules_organization_id ON work_schedules (organization_id);

CREATE TABLE reduced_hours (
	name VARCHAR(120) NOT NULL,
	start_date DATE NOT NULL,
	end_date DATE NOT NULL,
	daily_minutes INTEGER NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_reduced_hours_dates CHECK (end_date >= start_date),
	CONSTRAINT ck_reduced_hours_minutes CHECK (daily_minutes BETWEEN 60 AND 960),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);
CREATE INDEX ix_reduced_hours_organization_id ON reduced_hours (organization_id);

ALTER TABLE employees ADD COLUMN schedule_id VARCHAR(36);
ALTER TABLE employees ADD COLUMN reduced_hours BOOLEAN DEFAULT true NOT NULL;
ALTER TABLE employees ADD FOREIGN KEY(organization_id, schedule_id) REFERENCES work_schedules (organization_id, id);
CREATE INDEX ix_employees_schedule_id ON employees (schedule_id);

ALTER TABLE work_schedules ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON work_schedules USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());
ALTER TABLE reduced_hours ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON reduced_hours USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON work_schedules TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON reduced_hours TO %I', app_role);
END $$;
