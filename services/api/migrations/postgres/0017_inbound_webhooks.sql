-- 0017: inbound webhooks start workflows; hourly schedules need no schema change.

-- Trigger "webhook.received": the secret in the workflow's address (encrypted) and its SHA-256.
ALTER TABLE workflows ADD COLUMN hook_secret TEXT;
ALTER TABLE workflows ADD COLUMN hook_hash VARCHAR(64) UNIQUE;

-- A call carries only the address, so the API can't see the workflow before it knows the
-- organization. This answers just that: which organization has an enabled workflow behind it.
CREATE FUNCTION workflow_hook_org(p_hook_hash text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM workflows
         WHERE hook_hash = p_hook_hash AND trigger = 'webhook.received' AND enabled
    $$;
REVOKE ALL ON FUNCTION workflow_hook_org(text) FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT EXECUTE ON FUNCTION workflow_hook_org(text) TO %I', app_role);
END $$;
