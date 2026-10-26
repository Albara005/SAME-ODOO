-- 0024: the fiscal year can start in any month (year-end closes fall on the day before it).
ALTER TABLE organizations ADD COLUMN fiscal_year_start_month INTEGER DEFAULT 1 NOT NULL;
ALTER TABLE organizations ADD CONSTRAINT ck_org_fiscal_start CHECK (fiscal_year_start_month BETWEEN 1 AND 12);
