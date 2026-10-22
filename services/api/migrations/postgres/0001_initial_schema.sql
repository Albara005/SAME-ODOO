-- 0001: initial schema (identity, organizations, roles, memberships, audit, files).
-- Generated from the SQLAlchemy models, then reviewed. Keep models and migrations in sync.

CREATE TABLE organizations (
	name VARCHAR(200) NOT NULL,
	slug VARCHAR(80) NOT NULL,
	hosting_mode VARCHAR(32) NOT NULL,
	default_locale VARCHAR(10) NOT NULL,
	default_currency VARCHAR(3) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_organizations_slug ON organizations (slug);

CREATE TABLE users (
	email VARCHAR(320) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	full_name VARCHAR(200) NOT NULL,
	status VARCHAR(20) NOT NULL,
	email_verified BOOLEAN NOT NULL,
	locale VARCHAR(10) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	mfa_enabled BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE audit_logs (
	actor_user_id VARCHAR(36),
	action VARCHAR(100) NOT NULL,
	target_type VARCHAR(64) NOT NULL,
	target_id VARCHAR(64) NOT NULL,
	details JSON NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_audit_logs_action ON audit_logs (action);
CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id);
CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id);

CREATE TABLE files (
	storage_key VARCHAR(300) NOT NULL,
	filename VARCHAR(255) NOT NULL,
	content_type VARCHAR(127) NOT NULL,
	size_bytes BIGINT NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	created_by VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (storage_key),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_files_organization_id ON files (organization_id);

CREATE TABLE roles (
	key VARCHAR(64) NOT NULL,
	name VARCHAR(120) NOT NULL,
	permissions JSON NOT NULL,
	is_system BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_role_org_key UNIQUE (organization_id, key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_roles_organization_id ON roles (organization_id);

CREATE TABLE user_sessions (
	user_id VARCHAR(36) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	user_agent VARCHAR(500) NOT NULL,
	ip_address VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);

CREATE TABLE memberships (
	user_id VARCHAR(36) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	organization_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_membership_org_user UNIQUE (organization_id, user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE RESTRICT,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_memberships_organization_id ON memberships (organization_id);
CREATE INDEX ix_memberships_user_id ON memberships (user_id);
