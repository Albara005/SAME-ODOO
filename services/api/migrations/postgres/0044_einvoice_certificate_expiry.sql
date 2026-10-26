-- 0044: reminders before the e-invoicing certificate expires (novara/einvoice/service.py remind_expiry). The worker
-- goes through the organizations with an onboarded device; this lists them (organization ids only).

CREATE FUNCTION app_orgs_with_einvoicing() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM einvoice_settings WHERE status = 'onboarded'
    $$;
REVOKE ALL ON FUNCTION app_orgs_with_einvoicing() FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_orgs_with_einvoicing() TO %I', app_role);
END $$;
