-- 0023: passkeys (WebAuthn credentials) and the one-time challenges of their ceremonies.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.
-- Account-level tables like users and mfa_recovery_codes: no organization, so no row-level policy.

CREATE TABLE passkeys (
	user_id VARCHAR(36) NOT NULL,
	credential_id VARCHAR(512) NOT NULL,
	public_key TEXT NOT NULL,
	sign_count BIGINT NOT NULL,
	transports JSON NOT NULL,
	name VARCHAR(80) NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (credential_id)
);

CREATE INDEX ix_passkeys_user_id ON passkeys (user_id);

CREATE TABLE passkey_challenges (
	user_id VARCHAR(36),
	purpose VARCHAR(10) NOT NULL,
	challenge VARCHAR(128) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_passkey_challenges_expires_at ON passkey_challenges (expires_at);

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON passkeys, passkey_challenges TO %I', app_role);
END $$;
