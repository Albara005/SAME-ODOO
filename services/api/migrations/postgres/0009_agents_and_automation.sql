-- 0009: custom agents and workflow automation.
-- Table DDL generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE agents (
	name VARCHAR(120) NOT NULL,
	description TEXT NOT NULL,
	enabled BOOLEAN NOT NULL,
	published_version INTEGER,
	disabled_tools JSON NOT NULL,
	monthly_budget_micros BIGINT NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agents_organization_id ON agents (organization_id);

CREATE TABLE notifications (
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(300) NOT NULL,
	body TEXT NOT NULL,
	read_at TIMESTAMP WITH TIME ZONE,
	idempotency_key VARCHAR(120),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_idem UNIQUE (organization_id, idempotency_key),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_organization_id ON notifications (organization_id);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE TABLE outbox_events (
	type VARCHAR(60) NOT NULL,
	payload JSON NOT NULL,
	dedup_key VARCHAR(120),
	status VARCHAR(20) NOT NULL,
	processed_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_outbox_dedup UNIQUE (organization_id, dedup_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_outbox_events_organization_id ON outbox_events (organization_id);
CREATE INDEX ix_outbox_events_status ON outbox_events (status);
CREATE INDEX ix_outbox_events_type ON outbox_events (type);

CREATE TABLE tasks (
	title VARCHAR(300) NOT NULL,
	description TEXT NOT NULL,
	assignee_user_id VARCHAR(36),
	due_date DATE,
	status VARCHAR(20) NOT NULL,
	related_type VARCHAR(40) NOT NULL,
	related_id VARCHAR(64) NOT NULL,
	source VARCHAR(80) NOT NULL,
	idempotency_key VARCHAR(120),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	CONSTRAINT uq_task_idem UNIQUE (organization_id, idempotency_key),
	FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_tasks_assignee_user_id ON tasks (assignee_user_id);
CREATE INDEX ix_tasks_organization_id ON tasks (organization_id);
CREATE INDEX ix_tasks_status ON tasks (status);

CREATE TABLE workflows (
	name VARCHAR(120) NOT NULL,
	trigger VARCHAR(60) NOT NULL,
	conditions JSON NOT NULL,
	actions JSON NOT NULL,
	enabled BOOLEAN NOT NULL,
	notify_owner_on_failure BOOLEAN NOT NULL,
	owner_user_id VARCHAR(36) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_workflows_organization_id ON workflows (organization_id);
CREATE INDEX ix_workflows_trigger ON workflows (trigger);

CREATE TABLE agent_runs (
	agent_id VARCHAR(36) NOT NULL,
	version_number INTEGER NOT NULL,
	trigger VARCHAR(20) NOT NULL,
	user_id VARCHAR(36),
	conversation_id VARCHAR(36),
	test_case_id VARCHAR(36),
	input TEXT NOT NULL,
	reply TEXT NOT NULL,
	stop VARCHAR(30) NOT NULL,
	tool_calls JSON NOT NULL,
	cost_micros BIGINT NOT NULL,
	passed BOOLEAN,
	failures JSON NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, agent_id) REFERENCES agents (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_runs_agent_id ON agent_runs (agent_id);
CREATE INDEX ix_agent_runs_organization_id ON agent_runs (organization_id);
CREATE INDEX ix_agent_runs_test_case_id ON agent_runs (test_case_id);
CREATE INDEX ix_agent_runs_user_id ON agent_runs (user_id);

CREATE TABLE agent_test_cases (
	agent_id VARCHAR(36) NOT NULL,
	name VARCHAR(120) NOT NULL,
	input TEXT NOT NULL,
	expect_tools JSON NOT NULL,
	forbid_tools JSON NOT NULL,
	reply_contains JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, agent_id) REFERENCES agents (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_test_cases_agent_id ON agent_test_cases (agent_id);
CREATE INDEX ix_agent_test_cases_organization_id ON agent_test_cases (organization_id);

CREATE TABLE agent_versions (
	agent_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	instructions TEXT NOT NULL,
	tools JSON NOT NULL,
	route VARCHAR(40) NOT NULL,
	max_steps INTEGER NOT NULL,
	max_cost_micros BIGINT NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, agent_id) REFERENCES agents (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_agent_version_number UNIQUE (organization_id, agent_id, number),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_versions_agent_id ON agent_versions (agent_id);
CREATE INDEX ix_agent_versions_organization_id ON agent_versions (organization_id);

CREATE TABLE workflow_runs (
	workflow_id VARCHAR(36) NOT NULL,
	event_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	definition JSON NOT NULL,
	current_step INTEGER NOT NULL,
	attempts INTEGER NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	error TEXT NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (organization_id, id),
	FOREIGN KEY(organization_id, workflow_id) REFERENCES workflows (organization_id, id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id, event_id) REFERENCES outbox_events (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_workflow_run_once_per_event UNIQUE (organization_id, workflow_id, event_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_workflow_runs_organization_id ON workflow_runs (organization_id);
CREATE INDEX ix_workflow_runs_status ON workflow_runs (status);
CREATE INDEX ix_workflow_runs_workflow_id ON workflow_runs (workflow_id);

CREATE TABLE workflow_steps (
	run_id VARCHAR(36) NOT NULL,
	index INTEGER NOT NULL,
	type VARCHAR(40) NOT NULL,
	status VARCHAR(20) NOT NULL,
	result JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id, run_id) REFERENCES workflow_runs (organization_id, id) ON DELETE CASCADE,
	CONSTRAINT uq_workflow_step_once UNIQUE (organization_id, run_id, index),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_workflow_steps_organization_id ON workflow_steps (organization_id);
CREATE INDEX ix_workflow_steps_run_id ON workflow_steps (run_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['agents', 'agent_versions', 'agent_test_cases', 'agent_runs', 'outbox_events',
                             'workflows', 'workflow_runs', 'workflow_steps', 'tasks', 'notifications']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- A frozen agent version (tested or published) never changes; edits go to a new draft.
CREATE FUNCTION agent_version_is_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'frozen' AND (NEW.status, NEW.instructions, NEW.tools, NEW.route, NEW.max_steps,
                                  NEW.max_cost_micros)::text
                                 IS DISTINCT FROM (OLD.status, OLD.instructions, OLD.tools, OLD.route,
                                                   OLD.max_steps, OLD.max_cost_micros)::text THEN
        RAISE EXCEPTION 'agent version % is frozen', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER agent_version_frozen BEFORE UPDATE ON agent_versions
    FOR EACH ROW EXECUTE FUNCTION agent_version_is_frozen();

-- The automation worker finds which organizations to process without seeing their data:
-- it gets ids only, then processes each one in a session bound to that organization.
CREATE FUNCTION app_orgs_with_enabled_workflows() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
        SELECT DISTINCT organization_id FROM workflows WHERE enabled
    $$;

DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON agents, agent_versions, outbox_events, workflows, '
                   'workflow_runs, workflow_steps, tasks, notifications TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON agent_test_cases TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON agent_runs TO %I', app_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION app_orgs_with_enabled_workflows() TO %I', app_role);
END $$;
