-- Provision the least-privilege runtime role used by the application.
--
-- Run as the database owner on the DIRECT (non-pooler) connection:
--
--   psql "$DATABASE_SYNC_URL" -v ON_ERROR_STOP=1 \
--     -v runtime_user=olivet_runtime \
--     -v runtime_password='replace-me' \
--     -v admin_user=neondb_owner \
--     -f scripts/provision_runtime_role.sql
--
-- psql needs the libpq URL form (postgresql://), not the SQLAlchemy async
-- form (postgresql+asyncpg://).
--
-- On Neon, roles created through the Console/API/CLI inherit neon_superuser,
-- which includes BYPASSRLS and therefore ignores Row-Level Security. Creating
-- the role with plain SQL avoids that.
--
-- The role is idempotent: it is only created if it does not already exist.
-- Grants are safe to re-run.

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L '
    'NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
    :'runtime_user', :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user')
\gexec

GRANT CONNECT ON DATABASE :"DBNAME" TO :"runtime_user";
GRANT USAGE ON SCHEMA public TO :"runtime_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO :"runtime_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_user";

SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname = :'runtime_user';
