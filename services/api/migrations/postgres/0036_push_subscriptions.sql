-- 0036: Web Push (novara/push.py): members' devices that get their notifications.
-- No RLS on purpose, like sessions: a device belongs to a user, not an organization, and gets the user's
-- notifications from every organization they're in. The API only ever touches the signed-in user's rows.
-- Table DDL generated from the SQLAlchemy model, then reviewed. Keep models and migrations in sync.

CREATE TABLE push_subscriptions (
	user_id VARCHAR(36) NOT NULL,
	endpoint TEXT NOT NULL,
	endpoint_hash VARCHAR(64) NOT NULL,
	p256dh VARCHAR(200) NOT NULL,
	auth VARCHAR(64) NOT NULL,
	user_agent VARCHAR(300) NOT NULL,
	last_sent_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (endpoint_hash)
);

CREATE INDEX ix_push_subscriptions_user_id ON push_subscriptions (user_id);

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON push_subscriptions TO %I', app_role);
END $$;
