-- One-time database bootstrap, run by a PostgreSQL administrator.
-- Replace the passwords (or use your platform's IAM auth) before running.
--
--   novara_owner : owns the schema, runs migrations. Never used by the running API.
--   novara_app   : the API's runtime role. Not owner, not superuser, NOBYPASSRLS,
--                  so row-level security always applies to it.

CREATE ROLE novara_owner LOGIN PASSWORD 'change-me-owner';
CREATE ROLE novara_app LOGIN PASSWORD 'change-me-app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE novara OWNER novara_owner;
\connect novara
ALTER SCHEMA public OWNER TO novara_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
