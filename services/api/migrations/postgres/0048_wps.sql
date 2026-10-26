-- 0048: the UAE wage protection system's salary file (novara/modules/payroll/wps.py): the employer's establishment
-- id and bank routing code, and each employee's person number and bank routing code.

ALTER TABLE payroll_settings ADD COLUMN wps_employer_id VARCHAR(13) DEFAULT '' NOT NULL;
ALTER TABLE payroll_settings ADD COLUMN wps_bank_code VARCHAR(9) DEFAULT '' NOT NULL;
ALTER TABLE employee_pay ADD COLUMN wps_person_id VARCHAR(14) DEFAULT '' NOT NULL;
ALTER TABLE employee_pay ADD COLUMN wps_routing_code VARCHAR(9) DEFAULT '' NOT NULL;
