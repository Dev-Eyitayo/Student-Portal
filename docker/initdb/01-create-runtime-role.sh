#!/bin/sh
# Local Docker development only. Managed PostgreSQL providers (for example,
# Neon) do not run container init scripts; provision the runtime role there
# with scripts/provision_runtime_role.sql instead.
set -e

DB_NAME="${POSTGRES_DB:-$POSTGRES_USER}"
APP_USER="${APP_POSTGRES_USER:-olivet_runtime}"
APP_PASSWORD="${APP_POSTGRES_PASSWORD:?APP_POSTGRES_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$DB_NAME" \
  --set=app_user="$APP_USER" \
  --set=app_password="$APP_PASSWORD" \
  --set=admin_user="$POSTGRES_USER" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
  :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

GRANT CONNECT ON DATABASE :"DBNAME" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
EOSQL
