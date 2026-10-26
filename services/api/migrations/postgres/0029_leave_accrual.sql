-- 0029: leave allowances earned monthly or given upfront, prorated for part of a year, carried over
-- up to a limit; and morning or afternoon half days (days become NUMERIC in half-day steps).
ALTER TABLE leave_types ADD COLUMN accrual VARCHAR(10) DEFAULT 'upfront' NOT NULL;
ALTER TABLE leave_types ADD COLUMN prorate BOOLEAN DEFAULT false NOT NULL;
ALTER TABLE leave_types ADD COLUMN carry_over_days INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE leave_types ADD CONSTRAINT ck_leave_type_accrual CHECK (accrual IN ('upfront', 'monthly'));
ALTER TABLE leave_types ADD CONSTRAINT ck_leave_type_carry CHECK (carry_over_days BETWEEN 0 AND 366);

ALTER TABLE leave_requests DROP CONSTRAINT ck_leave_days;
ALTER TABLE leave_requests ALTER COLUMN days TYPE NUMERIC(6, 1);
ALTER TABLE leave_requests ADD CONSTRAINT ck_leave_days CHECK (CAST(days AS NUMERIC) > 0);
ALTER TABLE leave_requests ADD COLUMN half_day VARCHAR(2);
ALTER TABLE leave_requests ADD CONSTRAINT ck_leave_half_day
    CHECK (half_day IS NULL OR (half_day IN ('am', 'pm') AND start_date = end_date));
