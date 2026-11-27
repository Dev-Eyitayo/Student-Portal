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

## 4. Launch the Production Stack (Single Command)

`Dockerfile` (multi-stage with `uv`) and `docker-compose.prod.yml` are already in the repository. GitHub Actions (`.github/workflows/docker-publish.yml`) builds and pushes the image to `ghcr.io/dev-eyitayo/olivet-backend:latest` on every push to `main`, so your $3 VPS never compiles Python packages.

If the GHCR package is private, log in once on the VPS with a GitHub PAT (`read:packages`):

```bash
echo "<GITHUB_PAT>" | docker login ghcr.io -u Dev-Eyitayo --password-stdin
```

Start everything with one command:

```bash
cd /opt/olivet
docker compose -f docker-compose.prod.yml --env-file .env.prod pull && \
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Verify all services are healthy:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
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
# On the VPS (for backend updates — pulls pre-built GHCR image & auto-runs migrations)
cd /opt/olivet
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
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
