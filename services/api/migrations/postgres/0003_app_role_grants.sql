-- 0003: privileges for the API's runtime role.
--
-- The runtime role is created by the operator (infrastructure/deployment/postgres-roles.sql)
-- and its name is passed to the migration runner with --app-role (setting novara.app_role).
-- The audit log is append-only at the database level: no UPDATE or DELETE.

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON users, user_sessions, organizations, roles, memberships, files TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON audit_logs TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_current_org(), app_current_user(), app_user_org_ids() TO %I', app_role);
END $$;
