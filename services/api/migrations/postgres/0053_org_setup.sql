-- 0053: the first-run setup (novara/modules/organizations/setup.py). Organizations keep what the business does,
-- the apps its menu shows (NULL: all of them) and when the setup was finished or skipped. Organizations that
-- already hold records count as set up and never see the wizard; empty ones are offered it like new ones.

ALTER TABLE organizations ADD COLUMN business_type VARCHAR(30) DEFAULT '' NOT NULL;
ALTER TABLE organizations ADD COLUMN apps JSON;
ALTER TABLE organizations ADD COLUMN setup_completed_at TIMESTAMP WITH TIME ZONE;

UPDATE organizations o SET setup_completed_at = o.created_at
WHERE EXISTS (SELECT 1 FROM customers c WHERE c.organization_id = o.id)
   OR EXISTS (SELECT 1 FROM products p WHERE p.organization_id = o.id)
   OR EXISTS (SELECT 1 FROM journal_entries j WHERE j.organization_id = o.id);
