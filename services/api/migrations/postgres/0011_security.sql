-- 0011: sign-in limits, one-time email tokens, two-factor sign-in, invitations by email.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

ALTER TABLE users ADD COLUMN mfa_secret TEXT;
ALTER TABLE users ADD COLUMN mfa_pending_secret TEXT;
ALTER TABLE users ADD COLUMN mfa_last_step BIGINT;

CREATE TABLE login_attempts (
	email VARCHAR(320) NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	succeeded BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_login_attempts_email_created ON login_attempts (email, created_at);
CREATE INDEX ix_login_attempts_ip_created ON login_attempts (ip_address, created_at);

CREATE TABLE user_tokens (
	user_id VARCHAR(36) NOT NULL,
	purpose VARCHAR(20) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	used_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (token_hash)
);

CREATE INDEX ix_user_tokens_user_id ON user_tokens (user_id);

CREATE TABLE mfa_recovery_codes (
	user_id VARCHAR(36) NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	used_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_mfa_recovery_codes_user_id ON mfa_recovery_codes (user_id);

CREATE TABLE invitations (
	email VARCHAR(320) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	invited_by VARCHAR(36),
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	accepted_at TIMESTAMP WITH TIME ZONE,
	accepted_by VARCHAR(36),
	revoked_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE,
	UNIQUE (token_hash),
	FOREIGN KEY(invited_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(accepted_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_invitations_email ON invitations (email);
CREATE INDEX ix_invitations_organization_id ON invitations (organization_id);

ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invitations USING (organization_id = app_current_org())
    WITH CHECK (organization_id = app_current_org());

-- Opening an invitation link happens before the visitor belongs to the organization, so the API
-- can't see the row yet. This returns only the organization id of a pending invitation whose token
-- hash matches; the API then binds the session to that organization and reads the row normally.
CREATE FUNCTION app_invitation_org(p_token_hash text) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT organization_id FROM invitations
        WHERE token_hash = p_token_hash AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
    $$;
REVOKE ALL ON FUNCTION app_invitation_org(text) FROM PUBLIC;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON login_attempts TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON user_tokens, mfa_recovery_codes TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON invitations TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_invitation_org(text) TO %I', app_role);
END $$;
