-- 0002: PostgreSQL row-level security as a second isolation layer.
--
-- The API already filters every tenant-scoped query in the ORM (novara/tenancy.py).
-- These policies make the database itself refuse cross-organization access, so a
-- bug in application code, a raw SQL query or a leaked app credential still only
-- reaches the organization bound to the current transaction.
--
-- Requirements:
--   * the API connects as a role that does NOT own these tables and is not a
--     superuser (owners and superusers bypass RLS); see infrastructure/deployment;
--   * the API sets app.current_org / app.current_user per transaction
--     (done automatically by novara.tenancy).

CREATE FUNCTION app_current_org() RETURNS text
    LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.current_org', true), '') $$;

CREATE FUNCTION app_current_user() RETURNS text
    LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.current_user', true), '') $$;

-- Organization ids where the current user has an active membership. SECURITY DEFINER
-- so it can read memberships without recursing into the memberships policy.
CREATE FUNCTION app_user_org_ids() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM memberships
        WHERE user_id = app_current_user() AND status = 'active'
    $$;

ALTER TABLE roles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE files       ENABLE ROW LEVEL SECURITY;

-- Rows of the organization bound to this transaction: full access (subject to grants).
CREATE POLICY tenant_isolation ON roles
    USING (organization_id = app_current_org()) WITH CHECK (organization_id = app_current_org());
CREATE POLICY tenant_isolation ON memberships
    USING (organization_id = app_current_org()) WITH CHECK (organization_id = app_current_org());
CREATE POLICY tenant_isolation ON audit_logs
    USING (organization_id = app_current_org()) WITH CHECK (organization_id = app_current_org());
CREATE POLICY tenant_isolation ON files
    USING (organization_id = app_current_org()) WITH CHECK (organization_id = app_current_org());

-- "Which organizations do I belong to?" — read-only, limited to the caller's own rows.
CREATE POLICY own_memberships ON memberships FOR SELECT
    USING (user_id = app_current_user());
CREATE POLICY roles_of_own_memberships ON roles FOR SELECT
    USING (organization_id IN (SELECT app_user_org_ids()));
