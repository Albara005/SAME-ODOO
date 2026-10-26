-- 0007: AI assistant: settings, conversations, append-only messages, tool calls, usage.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE ai_conversations (
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_conversations_organization_id ON ai_conversations (organization_id);
CREATE INDEX ix_ai_conversations_user_id ON ai_conversations (user_id);

CREATE TABLE ai_settings (
	enabled BOOLEAN NOT NULL,
	monthly_budget_micros BIGINT NOT NULL,
	routes JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_ai_settings_org UNIQUE (organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_settings_organization_id ON ai_settings (organization_id);

CREATE TABLE ai_usage (
	user_id VARCHAR(36),
	conversation_id VARCHAR(36),
	route VARCHAR(40) NOT NULL,
	model VARCHAR(60) NOT NULL,
	input_tokens INTEGER NOT NULL,
	output_tokens INTEGER NOT NULL,
	cache_read_tokens INTEGER NOT NULL,
	cache_write_tokens INTEGER NOT NULL,
	cost_micros BIGINT NOT NULL,
	latency_ms INTEGER NOT NULL,
	stop_reason VARCHAR(30) NOT NULL,
	ok BOOLEAN NOT NULL,
	error TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_usage_conversation_id ON ai_usage (conversation_id);
CREATE INDEX ix_ai_usage_organization_id ON ai_usage (organization_id);
CREATE INDEX ix_ai_usage_user_id ON ai_usage (user_id);

CREATE TABLE ai_messages (
	conversation_id VARCHAR(36) NOT NULL,
	seq INTEGER NOT NULL,
	role VARCHAR(20) NOT NULL,
	content JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, conversation_id) REFERENCES ai_conversations (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_ai_message_seq UNIQUE (organization_id, conversation_id, seq),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_messages_conversation_id ON ai_messages (conversation_id);
CREATE INDEX ix_ai_messages_organization_id ON ai_messages (organization_id);

CREATE TABLE ai_tool_calls (
	conversation_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	tool_use_id VARCHAR(100) NOT NULL,
	name VARCHAR(80) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	arguments JSON NOT NULL,
	status VARCHAR(20) NOT NULL,
	result JSON,
	decided_at TIMESTAMP WITH TIME ZONE,
	reported BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, conversation_id) REFERENCES ai_conversations (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_tool_calls_conversation_id ON ai_tool_calls (conversation_id);
CREATE INDEX ix_ai_tool_calls_organization_id ON ai_tool_calls (organization_id);
CREATE INDEX ix_ai_tool_calls_status ON ai_tool_calls (status);
CREATE INDEX ix_ai_tool_calls_user_id ON ai_tool_calls (user_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ai_settings', 'ai_conversations', 'ai_messages', 'ai_tool_calls', 'ai_usage']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- Conversation messages and usage records are history: insert-only for the API.
DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON ai_settings, ai_tool_calls TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, DELETE ON ai_conversations TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON ai_messages, ai_usage TO %I', app_role);
END $$;
