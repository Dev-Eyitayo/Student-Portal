# Olivet Production Deployment Playbook

**Target Architecture (1,000–5,000 Students)**
- **Frontend (`student-portal-frontend`):** Cloudflare Pages (Static SPA build, `$0/mo`)
- **Backend (`Olivet`):** InterServer 1-Slice VPS (`1 vCPU`, `2 GB RAM`, `40 GB SSD`, `$3/mo`) running Docker Compose (`postgres:16-alpine`, `redis:7-alpine`, `api` Uvicorn, `worker` Celery) + Nginx + Let's Encrypt SSL
- **Media Storage & DB Backups:** Cloudflare R2 (`10 GB/mo` free, `$0 egress`) for school logos, stamps, signatures, and daily `pg_dump` backups

---

## 1. DNS Setup (Before Touching the Server)

Point these DNS records in Cloudflare:
- `app.yourdomain.com` → Cloudflare Pages custom domain (proxied)
- `api.yourdomain.com` → `A` record pointing to your InterServer VPS IP (start **DNS-only / grey cloud** until Certbot issues SSL, then enable proxy if desired)

---

## 2. Bootstrap the InterServer VPS (Ubuntu 24.04)

SSH in as `root` and set up a non-root deploy user, firewall, **2 GB swap** (essential on a 2 GB RAM VPS), Docker, and Nginx:

```bash
# 1. System updates & base tools
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg ufw nginx certbot python3-certbot-nginx

# 2. Add 2 GB Swap (prevents OOM kills during traffic or import spikes)
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10
echo 'vm.swappiness=10' >> /etc/sysctl.conf

# 3. Firewall (SSH, HTTP, HTTPS only — never expose 5432 or 6379 publicly)
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# 4. Install Docker + Compose plugin
curl -fsSL https://get.docker.com | sh

# 5. Cap Docker container log sizes so logs don't fill the 40 GB SSD
cat <<'EOF' > /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
systemctl restart docker
```

---

## 3. Prepare Backend Repo, Secrets & RS256 Keys

```bash
mkdir -p /opt/olivet && cd /opt/olivet
git clone https://github.com/Dev-Eyitayo/Student-Portal.git .

# Generate RS256 JWT keypair
mkdir -p config/keys var/storage
openssl genrsa -out config/keys/jwt_rs256.key 2048
openssl rsa -in config/keys/jwt_rs256.key -pubout -out config/keys/jwt_rs256.pub
chmod 600 config/keys/jwt_rs256.key
```

Create `/opt/olivet/.env.prod` (based on `.env.example`):

```env
ENVIRONMENT=production
DEBUG=False
APP_NAME="Olivet Academic Records Platform"
APP_URL=https://api.yourdomain.com

# Generate with: openssl rand -hex 32
SECRET_KEY=<REPLACE_WITH_64_CHAR_HEX>
JWT_PRIVATE_KEY_PATH=config/keys/jwt_rs256.key
JWT_PUBLIC_KEY_PATH=config/keys/jwt_rs256.pub
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# CORS: must match your Cloudflare Pages frontend domain
CORS_ORIGINS=["https://app.yourdomain.com"]

# PostgreSQL 16 (inside Docker network)
POSTGRES_USER=olivet_app
POSTGRES_PASSWORD=<REPLACE_STRONG_ADMIN_PASSWORD>
APP_POSTGRES_USER=olivet_runtime
APP_POSTGRES_PASSWORD=<REPLACE_STRONG_RUNTIME_PASSWORD>
POSTGRES_DB=olivet_records
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Runtime uses NOBYPASSRLS role (olivet_runtime); migrations use olivet_app
DATABASE_URL=postgresql+asyncpg://olivet_runtime:<REPLACE_STRONG_RUNTIME_PASSWORD>@db:5432/olivet_records
MIGRATION_DATABASE_URL=postgresql+asyncpg://olivet_app:<REPLACE_STRONG_ADMIN_PASSWORD>@db:5432/olivet_records
DATABASE_SYNC_URL=postgresql://olivet_app:<REPLACE_STRONG_ADMIN_PASSWORD>@db:5432/olivet_records

DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_RECYCLE_SECONDS=300

# Redis 7 (inside Docker network)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_URL=redis://redis:6379/0
REDIS_BROKER_URL=redis://redis:6379/0
CELERY_TASK_ALWAYS_EAGER=false
RATE_LIMIT_REDIS_URL=redis://redis:6379/1

# Email (Senviok)
MAIL_PROVIDER=senviok
MAIL_BASE_URL=https://api.senviok.live
MAIL_FROM_NAME="Olivet Academic Records Platform"
DEFAULT_FROM_EMAIL=records@yourdomain.com
MAIL_API_KEY=<REPLACE_MAIL_API_KEY>
MAIL_DOMAIN=yourdomain.com

# Asset Storage (Cloudflare R2 via S3-compatible API — 10 GB free/mo, $0 egress)
# In Cloudflare R2: create bucket "olivet-assets", attach custom domain "files.yourdomain.com",
# and create an R2 API token with Object Read & Write access.
STORAGE_PROVIDER=s3
S3_BUCKET=olivet-assets
S3_REGION=auto
S3_ENDPOINT_URL=https://<YOUR_CLOUDFLARE_ACCOUNT_ID>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=<REPLACE_R2_ACCESS_KEY_ID>
AWS_SECRET_ACCESS_KEY=<REPLACE_R2_SECRET_ACCESS_KEY>
STORAGE_PUBLIC_BASE_URL=https://files.yourdomain.com
BRANDING_UPLOAD_MAX_BYTES=2097152
```

> **Important:** Verify `docker/initdb/` creates `olivet_runtime` as `NOSUPERUSER NOBYPASSRLS` so PostgreSQL Row-Level Security (RLS) is enforced on every tenant query.

---

## 4. Dockerize Backend (`Dockerfile` + `docker-compose.prod.yml`)

Create `/opt/olivet/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

COPY . .

EXPOSE 8000
```

Create `/opt/olivet/docker-compose.prod.yml` (tuned for **2 GB RAM / 1 vCPU**):

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: always
    env_file: .env.prod
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=256MB"
      - "-c"
      - "work_mem=4MB"
      - "-c"
      - "max_connections=50"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/initdb:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-olivet_app} -d ${POSTGRES_DB:-olivet_records}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: always
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "128mb", "--maxmemory-policy", "noeviction"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  api:
    build: .
    restart: always
    env_file: .env.prod
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips="*"
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./config/keys:/app/config/keys:ro
      - ./var/storage:/app/var/storage
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    restart: always
    env_file: .env.prod
    command: celery -A app.core.celery_app worker -Q default,bulk_imports,notifications --concurrency=2 --max-tasks-per-child=100 -l info
    volumes:
      - ./config/keys:/app/config/keys:ro
      - ./var/storage:/app/var/storage
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  postgres_data:
  redis_data:
```

Start the stack and run Alembic migrations:

```bash
cd /opt/olivet
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod exec api alembic upgrade head
```

---

## 5. Nginx Reverse Proxy & Let's Encrypt SSL

Create `/etc/nginx/sites-available/olivet-api`:

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Enable the site and issue SSL:

```bash
ln -s /etc/nginx/sites-available/olivet-api /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d api.yourdomain.com
```

Verify the API health check:

```bash
curl -fsS https://api.yourdomain.com/health
```

---

## 6. Deploy Frontend (`student-portal-frontend`) to Cloudflare Pages

1. In `student-portal-frontend/react-router.config.ts`, set `ssr: false` (our routes fetch client-side with no server `loader`/`action` functions, so static SPA mode works out of the box).
2. Add `public/_redirects` containing:
   ```text
   /* /index.html 200
   ```
3. In **Cloudflare Pages** → **Create application** → connect your `student-portal-frontend` GitHub repo:
   - **Build command:** `pnpm build`
   - **Build output directory:** `build/client`
   - **Environment variables:**
     - `NODE_VERSION`: `20`
     - `VITE_API_BASE_URL`: `https://api.yourdomain.com`
4. Attach custom domain `app.yourdomain.com` in Cloudflare Pages.

---

## 7. Automated Daily Backups (Cron)

Add a daily 2:00 AM backup script `/opt/olivet/backup.sh` (and `chmod +x /opt/olivet/backup.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%F_%H%M)
BACKUP_DIR=/opt/olivet/backups
mkdir -p "$BACKUP_DIR"

docker compose -f /opt/olivet/docker-compose.prod.yml --env-file /opt/olivet/.env.prod exec -T db \
  pg_dump -U olivet_app -Fc olivet_records > "$BACKUP_DIR/olivet_${STAMP}.dump"

# Keep last 7 days locally; sync to Cloudflare R2 / S3 via rclone if configured
find "$BACKUP_DIR" -name "olivet_*.dump" -mtime +7 -delete
# rclone copy "$BACKUP_DIR" r2:olivet-backups/db/
# rclone sync /opt/olivet/var/storage r2:olivet-backups/storage/
```

Add to crontab (`crontab -e`):
```cron
0 2 * * * /opt/olivet/backup.sh >> /var/log/olivet-backup.log 2>&1
```

---

## 8. Release / Update Procedure

```bash
# On the VPS (for backend updates)
cd /opt/olivet
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod exec api alembic upgrade head
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
```

Frontend updates deploy automatically via Cloudflare Pages whenever you push to `master`.

---

## 9. Launch Day Smoke-Test Checklist

- [ ] `https://api.yourdomain.com/health` returns `200 OK`
- [ ] `https://app.yourdomain.com/onboarding` loads and registers a test school (verify `PUBLIC` vs `PRIVATE` selector)
- [ ] Email delivery (`MAIL_PROVIDER=senviok`) sends staff invite / password-reset emails
- [ ] School logo / student photo upload persists in `/opt/olivet/var/storage` and loads via `https://api.yourdomain.com/storage/...`
- [ ] Celery worker processes a bulk CSV/Excel student import (`docker compose ... logs worker`)
- [ ] Rate limiting honors real client IPs (`X-Forwarded-For` from Nginx)
- [ ] Test restoring the latest `.dump` file (`pg_restore`) on a scratch database before onboarding the first pilot school
