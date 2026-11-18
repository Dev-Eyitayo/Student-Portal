# Managed Deployment: Neon + Upstash

This runbook covers production deployment with:
- **PostgreSQL:** Neon
- **Redis / broker:** Upstash
- **Migrations:** Alembic, run as the database owner
- **Runtime:** FastAPI and Celery workers, using a non-superuser role

The application enforces tenant isolation with PostgreSQL Row-Level Security
(RLS). RLS only works when the connecting role is neither `SUPERUSER` nor
`BYPASSRLS`.

---

## 1. PostgreSQL roles on Neon

Neon has no traditional superuser. Instead, roles created through the Console,
API, or CLI inherit `neon_superuser`, which includes `BYPASSRLS`. Those roles
ignore RLS. Roles created with plain SQL do not inherit it.

Use **two roles**:

| Role | Used by | RLS |
|---|---|---|
| `neondb_owner` (or equivalent) | Alembic migrations only | Bypassed (admin) |
| `olivet_runtime` | FastAPI + Celery | Enforced |

### 1.1 Create the runtime role

Run this as the Neon owner on the **direct** connection (no `-pooler`), using
either the Neon SQL Editor or `psql`. `psql` needs the libpq form
(`postgresql://...`), so use `DATABASE_SYNC_URL` or strip `+asyncpg` from the
Alembic URL:

```bash
psql "$DATABASE_SYNC_URL" -v ON_ERROR_STOP=1 \
  -v runtime_user=olivet_runtime \
  -v runtime_password='REPLACE_WITH_A_STRONG_PASSWORD' \
  -v admin_user=neondb_owner \
  -f scripts/provision_runtime_role.sql
```

The script is idempotent and prints the resulting role attributes. Confirm:

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname = 'olivet_runtime';
```

Expected: `rolsuper = false`, `rolbypassrls = false`.

> If the runtime role is a superuser or has `BYPASSRLS`, the application will
> refuse to start in non-development environments.

### 1.2 Connection strings

Neon provides two URLs. The difference is the `-pooler` hostname suffix.

| Purpose | Host | Environment variable |
|---|---|---|
| FastAPI / Celery | pooled (`-pooler`) | `DATABASE_URL` |
| Alembic migrations | direct | `MIGRATION_DATABASE_URL` |

For SQLAlchemy's asyncpg driver, use `?ssl=require`, **not** `sslmode=require`,
and remove `channel_binding`. The settings loader normalizes `sslmode` and drops
`channel_binding` automatically, but keeping the URL clean avoids surprises.

```dotenv
DATABASE_URL=postgresql+asyncpg://olivet_runtime:PASSWORD@ep-xxxx-pooler.region.aws.neon.tech/neondb?ssl=require
MIGRATION_DATABASE_URL=postgresql+asyncpg://neondb_owner:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?ssl=require
```

Neon's pooler runs PgBouncer in transaction mode. The app applies tenant context
with transaction-local `SET LOCAL`, which is compatible. Keep the client-side
pool small:

```dotenv
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_RECYCLE_SECONDS=300
DB_PREPARED_STATEMENT_CACHE_SIZE=0
```

`DB_PREPARED_STATEMENT_CACHE_SIZE` is auto-detected when unset (`-pooler` → `0`,
direct → `100`). Set it explicitly if you see prepared-statement errors.

### 1.3 Apply migrations and RLS

Run migrations as a **release job** with the direct owner URL:

```bash
ENVIRONMENT=production \
MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
alembic upgrade head
```

This creates the RLS policies. Never run migrations at application startup.

Verify:

```sql
SELECT tablename, rowsecurity, forcerowsecurity
FROM pg_tables
JOIN pg_class ON pg_class.relname = pg_tables.tablename
WHERE schemaname = 'public' AND tablename = 'students';
```

---

## 2. Upstash Redis

Create a Redis database in Upstash and use the **TLS URL** (`rediss://`).

```dotenv
REDIS_URL=rediss://default:TOKEN@host.upstash.io:6379?ssl_cert_reqs=required
REDIS_BROKER_URL=rediss://default:TOKEN@host.upstash.io:6379?ssl_cert_reqs=required
CELERY_RESULT_BACKEND=rediss://default:TOKEN@host.upstash.io:6379?ssl_cert_reqs=required
```

Notes:
- Use `rediss://`, not `redis://`. Upstash requires TLS.
- Keep `ssl_cert_reqs=required`.
- Prefer separate Redis databases or key prefixes for:
  - Celery broker
  - Celery result backend
  - token revocation
  - rate limiting / cache
- The application reuses a single async Redis client. It is created at startup
  and closed during shutdown.
- Token revocation falls back to an in-memory set if Redis is unreachable. That
  fallback is per-process, so Redis must be healthy for multi-instance logout
  revocation to work correctly.

### Celery

`app/core/celery_app.py` reads `REDIS_BROKER_URL` and `CELERY_RESULT_BACKEND`.
TLS options are applied automatically for `rediss://` URLs. Run workers with:

```bash
celery -A app.core.celery_app.celery_app worker -Q default,bulk_imports,notifications -l info
```

Recommended worker settings are already configured: JSON serialization,
`task_acks_late`, rejection on worker loss, single-message prefetch, and startup
retry.

---

## 3. Application environment

At minimum, production needs:

```dotenv
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=<strong, high-entropy secret>
APP_URL=https://records.example.edu.ng
CORS_ORIGINS=["https://records.example.edu.ng"]

DATABASE_URL=<Neon pooled runtime URL>
MIGRATION_DATABASE_URL=<Neon direct owner URL>
REDIS_URL=<Upstash rediss URL>
```

- Deliver secrets through the platform's secret manager or runtime environment,
  not a committed `.env` file.
- `MIGRATION_DATABASE_URL` belongs only in the migration job. Do not expose
  admin database credentials to the web or worker processes.
- Rotate database and Redis credentials after any suspected exposure.

Branding uploads (M8, **LIVE**) use `STORAGE_PROVIDER=local` for
the pilot. If the API runs in more than one instance or container, the local
upload directory must be a shared, persistent volume; otherwise wait for the
S3/Cloudinary providers. Assets are namespaced by school slug/id, so a school
must never be able to read another school's files.

Custom domains are **PLANNED post-pilot** and are not supported in the pilot.
Until then, keep `CORS_ORIGINS` explicit (`APP_URL`), do not add wildcard origins
or `Host`-header tenant selection for authenticated APIs, and terminate HTTPS at
the proxy so refresh cookies stay secure.

---

## 4. Deployment checklist

1. Create the Neon project and the SQL runtime role.
2. Store `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `REDIS_URL`, and `SECRET_KEY`
   in the platform secret store.
3. Run `alembic upgrade head` with the direct owner URL.
4. Start the API with `DATABASE_URL` using the runtime role.
5. Start Celery workers with the same runtime role and Upstash broker.
6. Confirm the API is healthy: `GET /api/v1/health`.
7. Confirm the startup log has no RLS enforcement error.
8. Smoke-test tenant isolation (two schools; user A cannot read user B's rows).
9. Test logout followed by refresh: both access and refresh tokens must be
   rejected.
10. Configure Neon backups/branching and Upstash persistence/eviction alerts.
11. Confirm `STORAGE_PROVIDER=local` points to a persistent shared directory and that school A cannot read school B's branding assets.
12. Keep custom domains disabled; verify there is no `Host`-header tenant routing and no wildcard CORS.

---

## 5. Local development note

`docker/initdb/01-create-runtime-role.sh` is **local Docker only**. Managed
providers do not use container init scripts. On Neon, provision the runtime role
with SQL as shown above.

Deleting the local Docker volume is safe while data is disposable: the init
script runs on the new volume, then `alembic upgrade head` applies RLS. The role
provisioning step cannot be skipped, because the stock image's `POSTGRES_USER`
is the immutable bootstrap superuser and would bypass RLS.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Refusing to start: the application database role can bypass ... RLS` | Runtime URL uses the Neon owner / `neon_superuser` role | Create a SQL role and point `DATABASE_URL` at it |
| `unsupported startup parameter: channel_binding` | Neon URL copied verbatim into an asyncpg URL | Use `?ssl=require` and remove `channel_binding` (the loader does this automatically) |
| `prepared statement ... already exists` | asyncpg statement cache with PgBouncer | Set `DB_PREPARED_STATEMENT_CACHE_SIZE=0` |
| SSL connection closed / stale connection | Neon compute suspend or idle connection reuse | Keep `pool_pre_ping=True` and `pool_recycle=300`; use SQLAlchemy ≥ 2.0.33 |
| `SSL: CERTIFICATE_VERIFY_FAILED` (Redis) | Missing TLS parameters | Use `rediss://...?ssl_cert_reqs=required` |
| Token revocation works on one instance only | Redis down or not shared | Check `REDIS_URL` and Upstash connectivity; the in-memory fallback is per-process |
| Branding asset returns 404 for another school | Expected tenant isolation, or wrong `{school_slug}/branding/` namespace | Verify the authenticated tenant matches the asset namespace; do not share paths across schools |
| CORS errors after adding a custom domain | Domain not verified; dynamic CORS is post-pilot | Keep explicit `CORS_ORIGINS`; custom domains are PLANNED, not supported in the pilot |
