-- 0016: an organization can require two-step sign-in from every member.
ALTER TABLE organizations ADD COLUMN require_mfa BOOLEAN NOT NULL DEFAULT false;
