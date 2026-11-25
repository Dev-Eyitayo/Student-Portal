# Olivet Backend Load Testing Results, Optimizations & 4 GB VPS Capacity Report

**Date:** 2026-11-26  
**Benchmark Environment:** 4-worker Uvicorn (`127.0.0.1:8001`, `--workers 4`, `ENVIRONMENT=development`), local PostgreSQL 16 + Redis 7 + Celery worker (`--concurrency=2`)  
**Seeded Dataset:** 5 tenant schools (`LOAD-01`..`LOAD-05`), 100 students/school (`500` total), 2 terms (First Term `PUBLISHED` with `500` `ResultAccessToken`s + Second Term `ACTIVE` with `DRAFT` & `APPROVED` sheets, attendance, and timetable drafts) via [scripts/seed_load_test.py](../scripts/seed_load_test.py).

---

## 1. Optimizations Implemented

### Pass 2 (P0 / P1 Core Fixes)
1. **OWASP Argon2id Tuning ([app/core/security.py](../app/core/security.py)):**
   - Replaced passlib's heavy default Argon2 parameters with OWASP interactive Argon2id settings (`time_cost=2`, `memory_cost=19456` [19 MiB], `parallelism=1`) for non-test environments.
2. **Single Request-Scoped DB Session ([app/core/dependencies.py](../app/core/dependencies.py)):**
   - Introduced `_get_request_db()` shared between `get_current_authenticated_user` and `get_tenant_db`, cutting DB session checkouts and RLS `begin` hooks in half (1 session per authenticated request instead of 2).
3. **Batched Bulk Score Entry ([app/modules/assessments/services.py](../app/modules/assessments/services.py)):**
   - Replaced the per-student `SELECT AssessmentScore` loop in `bulk_score_entry` with a single `IN (...)` batch query.
4. **Batched Attendance Summary Sync ([app/modules/students/attendance_services.py](../app/modules/students/attendance_services.py)):**
   - Added `_sync_daily_summaries_bulk` to `mark_class_attendance`, replacing `2N` sequential queries per class mark with 2 bulk queries (`AttendanceSummary` lookup + `GROUP BY student_enrollment_id, status`).
5. **Distribution Fixes & Caching ([app/modules/distribution/services.py](../app/modules/distribution/services.py)):**
   - Fixed `await get_redis()` → `get_redis()` in `_enforce_verify_rate_limit` (rate limiting now enforces `VERIFY_RATE_LIMIT_PER_MINUTE = 20` instead of failing open).
   - Added 1-hour Redis caching (`report_card:verified:{card.id}`) for immutable `PUBLISHED` report cards in `verify_result_token`.
   - Batched `get_student_transcript` via `_assemble_verified_report_card_response` (7 bulk queries total instead of `7 × N` queries per term).
6. **k6 Scenario Fix ([load_tests/k6/scenarios/background_jobs.js](../load_tests/k6/scenarios/background_jobs.js)):**
   - Updated `POST /api/v1/publication/notify-term` payload to match `NotifyTermResultsRequest` (`academic_term_id` + `result_links`).

### Pass 3 (Read-Path & Pool Tuning)
7. **Redis Role-Permission Cache ([app/core/permissions.py](../app/core/permissions.py)):**
   - Added a 60s Redis cache (`perm:role:{school_id}:{role}:{permission_code}`) in `role_has_permission` (bypassed in `testing` mode), removing 1–2 DB queries per teacher/staff request.
8. **Filtered Timetable Entry Views ([app/modules/timetable/services.py](../app/modules/timetable/services.py)):**
   - Updated `_build_entry_views` so `class_group_id` / `teacher_id` views only fetch referenced rows instead of loading 7 unfiltered school-wide tables.
9. **Removed Redundant Login Refresh ([app/modules/identity/services.py](../app/modules/identity/services.py)):**
   - Removed `await db.refresh(user)` after `db.commit()` in `authenticate_user` (`expire_on_commit=False` is already enabled).
10. **Increased Default Worker DB Pool ([app/core/config.py](../app/core/config.py)):**
    - Raised `DB_POOL_SIZE` from `5` to `10` and `DB_MAX_OVERFLOW` from `5` to `10`.

---

## 2. Benchmark Progression (Pass 1 → Pass 2 → Pass 3)

| Scenario | Metric | Pass 1 (Baseline) | Pass 2 (P0/P1) | Pass 3 (Latest) | Total Gain |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Daily Ops** *(25 VUs, 3m)* | Throughput | `6.98 req/s` (`1,256 reqs`) | `20.71 req/s` (`3,749 reqs`) | **`22.21 req/s`** (`4,024 reqs`) | **3.2x** |
| | Overall `p95` | `4,812.7 ms` | `717.0 ms` | **`588.3 ms`** (`avg 236.3ms`) | **8.2x faster** |
| | `login` `p95` | `5,595.6 ms` | `974.4 ms` | **`720.3 ms`** (`avg 324.0ms`) | **7.8x faster** |
| | `reads` `p95` | `2,336.6 ms` | `628.6 ms` | **`520.9 ms`** (`avg 210.3ms`) | **4.5x faster** |
| | `bulk_writes` `p95` | `5,626.1 ms` | `718.6 ms` | **`641.8 ms`** (`avg 273.9ms`) | **8.8x faster** |
| | Error rate | `0.00%` | `0.00%` | **`0.00%`** | — |
| **Term Computation** *(15 VUs, 3m)* | Throughput | `9.14 req/s` (`1,660 reqs`) | — | **`9.71 req/s`** (`1,758 reqs`) | **+6.2%** |
| | `login` `p95` | `1,224.8 ms` | — | **`538.8 ms`** (`avg 226.3ms`) | **2.3x faster** |
| | `heavy_compute` `p95` | `922.7 ms` | — | **`865.7 ms`** (`avg 409.2ms`) | **Passes `<2.5s` SLO** |
| **Result-Day Spike** *(100 RPS target, 3m)* | Throughput | `15.45 req/s` (`2,876 reqs`) | `26.99 req/s` (`4,859 reqs`) | **`28.03 req/s`** (`5,055 reqs`) | **1.8x** |
| | Overall `p95` | `3,574.2 ms` | `1,667.3 ms` | **`1,545.9 ms`** (`avg 733.5ms`) | **2.3x faster** |
| | `heavy_compute` (`transcript`) `p95` | `3,347.2 ms` | `1,727.9 ms` | **`1,647.4 ms`** (`avg 867.1ms`) | **2.0x faster** |
| **Login Storm** *(25 RPS target, 2m)* | Throughput | `3.38 req/s` (`479 reqs`) | `12.49 req/s` (`1,569 reqs`) | — | **3.7x** |
| | `login` `avg` / `p95` | `8,989.9 ms` / `14,128.9 ms` | `1,725.7 ms` / `3,462.2 ms` | — | **5.2x** / **4.1x faster** |
| **Background Jobs** *(3 VUs × 3)* | `http_req_failed` | `27.27%` | `0.00%` (`34/34` checks) | — | **Fixed** |

---

## 3. Capacity Assessment: 5 Schools × (1,200+ Students, 100+ Staff) on a 4 GB RAM VPS

**Target Scale:** `~6,000+` students, `~500+` staff, `~60,000–75,000` subject registrations, `~250,000–300,000` scores per active term.

### Expected Performance by Workload

| Workload | Realistic Concurrency | Expected Performance on 4 GB VPS | Verdict |
| :--- | :--- | :--- | :--- |
| **Daily School Operations** *(Attendance, score entry, dashboards, student search)* | ~50–80 active staff with 2–5s think time (`~15–22 RPS`) | Matches Pass 3 `daily_ops` (`22.21 RPS`): **`~200–300ms` avg**, **`~600–800ms` p95**, `0%` errors | **Comfortable** |
| **Morning Login Burst** *(100 staff signing in over 2–3 mins)* | `~1–3 logins/sec` | `~320ms` avg, `<750ms` p95 | **Comfortable** *(saturates above ~12 logins/sec)* |
| **End-of-Term Computation** *(Per-class `calculate-term` & `broadsheet`)* | ~5–15 admins/teachers | `~650ms` avg, `~1.4s` p95 for ~40-student arms | **Comfortable per-class** |
| **Full-School `calculate-term`** *(`class_group_id=null` for 1,200 students)* | 1–2 admins | Synchronous `O(N^2)` ranking + row-by-row ORM upserts -> **~8–20s+** blocking one worker | **Risky** *(run per-class or move to Celery)* |
| **Result-Day Parent Spike** *(6,000 parents checking links simultaneously)* | `~30–100+ RPS` if all SMS/emails go out at once | Server ceiling is `~28 RPS`; bursts above that will queue (`p95 > 1.5s`) and shared mobile/NAT IPs may hit the `20/min/IP` limit | **Tight** *(stagger `notify-term` batches by class/level)* |

### 4 GB RAM Budget & Deployment Guardrails

If **PostgreSQL, Redis, FastAPI, and Celery all run on the same 4 GB VPS** (typically 2 vCPUs):
- **OS & System:** `~400 MB`
- **PostgreSQL 16:** `~800 MB – 1.1 GB`
- **Redis 7:** `~100–150 MB`
- **Uvicorn (2–3 workers, NOT 4):** `~200 MB` each = `~450–650 MB`
- **Celery Worker (`--concurrency=2`):** `~300–500 MB` *(spikes during `generate_timetable_task` or bulk Excel import)*
- **Total Baseline:** `~2.1 – 2.8 GB` (leaving `~1.2 GB` headroom for spikes).

**Recommended 4 GB VPS Settings:**
1. **Use 2–3 Uvicorn workers** (matching 2 vCPUs) with `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=4` (keeps max Postgres connections under `~50`, avoiding Postgres memory bloat and `max_connections=100` exhaustion).
2. **Add 2 GB swap** as an OOM safety net when Celery runs `generate_timetable_task` alongside traffic.
3. **Avoid full-school synchronous `calculate-term`** during peak hours—run per `class_group_id` or offload to Celery.
4. **Stagger `notify-term` dispatches** by level/class on result day so parent verification traffic stays under `20–25 RPS`.
5. **Best single upgrade when growing beyond 5 schools:** Move PostgreSQL to a managed instance with PgBouncer (`-pooler`) so the 4 GB VPS only runs FastAPI + Celery + Redis.
