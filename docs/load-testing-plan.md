# Olivet Backend k6 Load Testing Plan

**Target Repository:** `Olivet` (FastAPI + SQLAlchemy 2.0 Async + PostgreSQL 16 with RLS + Redis + Celery)  
**Primary Load Tool:** [k6](https://k6.io/)  
**Status:** Executed & Benchmarked — see [load-testing-results.md](./load-testing-results.md) for measured results, optimizations, and 4 GB VPS capacity analysis.

---

## 1. Objectives & Why k6

This plan defines how to load-test the Olivet Academic Records Management Platform backend before pilot and multi-school production rollout. Its goals are to:

1. **Establish baseline latency and throughput SLOs** across daily classroom operations, end-of-term score computation, result publication, and background Celery workloads.
2. **Uncover concurrency bottlenecks** in connection pooling (`asyncpg` / PgBouncer), Row-Level Security (RLS) transaction setup, Redis connection limits, Argon2 CPU/thread saturation, and known N+1 query paths.
3. **Verify multi-tenant isolation under load**—confirming that concurrent requests across multiple schools never leak cross-tenant data when pooled PostgreSQL connections are reused.

**Why k6:**
- Low CPU/memory overhead per virtual user (VU), enabling high arrival-rate spikes (e.g., parent result-day traffic) from a single runner.
- Native `ramping-arrival-rate` and `constant-arrival-rate` executors (essential to avoid coordinated omission), `SharedArray` for large multi-tenant CSV/JSON fixtures, per-endpoint `tags`, and built-in `thresholds` for CI/CD gating.

---

## 2. Codebase-Specific Bottlenecks & Architectural Risks

Every test scenario in this plan targets real constraints observed in the current backend implementation:

### 2.1 Database Pooling, RLS Hook & Double Session per Request
- **Small per-worker pool (`app/core/config.py`, `app/core/database.py`):** Defaults are `DB_POOL_SIZE = 5`, `DB_MAX_OVERFLOW = 5` (max **10 connections per Uvicorn worker**), `DB_POOL_RECYCLE_SECONDS = 300`, `pool_pre_ping = True`. Prepared statement caching is auto-disabled (`0`) when `"-pooler"` is in `DATABASE_URL` (PgBouncer transaction mode) and `100` otherwise.
- **Per-transaction RLS `begin` hook (`app/core/database.py`):** `@event.listens_for(async_engine.sync_engine, "begin")` runs `SELECT set_config('app.current_school_id', :school_id, true), set_config('app.is_super_admin', :is_super_admin, true)` on **every** transaction begin.
- **Two DB sessions per authenticated request (`app/core/dependencies.py`):** `get_current_authenticated_user` opens and closes its own session (`set_config` + `SELECT User` with `selectinload(staff_profile, school)`), and `get_tenant_db` opens a **second** session (`set_config` again) for the route handler. Under high concurrency, slow queries in `get_tenant_db` can quickly exhaust the 10-connection worker pool.

### 2.2 Redis Connection Pool & Rate-Limit Behavior
- **Shared Redis pool (`app/core/redis.py`, `app/core/config.py`):** A single process-wide `aioredis.Redis` client is capped at `REDIS_MAX_CONNECTIONS = 20` (`socket_timeout = 5.0s`, `socket_connect_timeout = 5.0s`). It handles JWT revocation checks (`EXISTS token:revoked:{jti}` on **every** authenticated request), rate limiting, password-reset tokens, and bulk-import job status.
- **IP-based rate limits (`app/modules/identity/services.py`, `app/modules/distribution/services.py`):**
  - `POST /api/v1/identity/forgot-password`: `rl:pwd_reset:ip:{client_ip}` (`> 10` per `900s`), `rl:pwd_reset:cooldown:{email_hash}` (`1` per `60s`), `rl:pwd_reset:hour:{email_hash}` (`> 5` per `3600s`).
  - `GET /api/v1/identity/reset-password/verify`: `rl:pwd_reset_verify:ip:{client_ip}` (`> 20` per `60s`).
  - `GET /api/v1/results/verify/{token}`: `rl:results_verify:{client_ip}` (`VERIFY_RATE_LIMIT_PER_MINUTE = 20` per `60s`).
  - *Observed quirks to account for:*
    1. `_client_ip()` reads `request.client.host` directly (ignoring `X-Forwarded-For`), so single-origin k6 traffic will share one IP bucket.
    2. `app/modules/distribution/services.py` (`_enforce_verify_rate_limit`) currently calls `await get_redis()` on a synchronous `get_redis()` function and catches `Exception`—meaning rate limiting on `verify/{token}` currently fails open unless fixed. Test both current behavior and post-fix behavior.
  - `POST /api/v1/identity/login` and `POST /api/v1/identity/refresh` have **no** Redis rate limits.

### 2.3 Argon2 Login CPU & Thread-Pool Saturation
- `app/core/security.py` uses full Argon2 parameters whenever `ENVIRONMENT` is not `testing`/`test` and `TESTING != "1"`.
- `app/modules/identity/services.py` offloads `verify_password` / `hash_password` via `asyncio.to_thread(...)` and executes a `User.last_login_at` `UPDATE` + `COMMIT` on every `POST /api/v1/identity/login`.
- **Rule:** Never run load tests with `ENVIRONMENT=testing` or `TESTING=1`, or Argon2 drops to `time_cost=1, memory_cost=512`, masking real login CPU/thread-pool contention.

### 2.4 Known N+1, In-Memory & Synchronous Hotspots

| Area | Endpoint | Observed Bottleneck |
| :--- | :--- | :--- |
| **Score Entry** | `POST /api/v1/assessments/scores/bulk-entry` | `bulk_score_entry` runs a sequential `SELECT` per student in `scores` (~45 queries per class). |
| **Score Overview** | `GET /api/v1/assessments/sheets` | Loads all term `ClassSubject`, `ScoreSheetStatus`, and `TeacherAssignment` rows into Python and paginates in memory. |
| **Daily Attendance** | `POST /api/v1/structure/class-groups/{id}/attendance` | `_sync_daily_summary` executes 2 sequential queries per student (~90 queries for a 45-student class inside one transaction). |
| **Attendance Overview** | `GET /api/v1/structure/attendance/summary` | Loads all daily `AttendanceRecord` rows across the school and aggregates in Python. |
| **Student Directory** | `GET /api/v1/students?q=...` | Uses 4 leading-wildcard `ILIKE '%...%'` clauses plus an extra `IN (student_ids)` class-lookup query. |
| **Timetable Views** | `GET /api/v1/timetable/schedules/{id}/...` | `_build_entry_views` runs 7+ unfiltered school-wide table `SELECT`s even for a single class or teacher view. |
| **Dashboards** | `GET /api/v1/structure/dashboard/admin` & `/issues` | `_collect_term_issues` runs uncached school-wide aggregation across `ClassGroup`, `ClassSubject`, `TeacherAssignment`, and `ScoreSheetStatus`. |
| **Term Computation** | `POST /api/v1/computation/calculate-term` | Runs synchronously in HTTP request (no Celery); 6 input queries + $O(N^2)$ `competition_ranks` + row-by-row ORM upserts (+3 more queries on `term_order == 3`). |
| **Broadsheets** | `GET /api/v1/computation/broadsheet` & `/{class_group_id}` | **Recomputes from raw `AssessmentScore`s in memory on every `GET`** and slices pagination in Python (no SQL `LIMIT`/`OFFSET`, no Redis cache). |
| **Report Cards** | `GET /api/v1/publication/report-cards` | Loads all matching cards into Python memory, computes summary stats, and slices pagination in Python. |
| **Transcripts** | `GET /api/v1/publication/students/{student_id}/transcript` | N+1 loop: calls `_build_verified_report_card_response` per term (~7 sequential DB queries per term). |
| **Public Verification** | `GET /api/v1/results/verify/{token}` | Runs ~14–15 sequential DB queries **plus** an `UPDATE result_access_tokens SET access_count = access_count + 1` + `db.commit()` on a `GET`. Default `max_access_limit = 20`. |

### 2.5 Celery Queue Isolation (`app/core/celery_app.py`)
- Queues: `bulk_imports` (`process_staff_bulk_import`, `process_student_bulk_import`), `notifications` (`send_staff_invitation_email`, `send_password_reset_email`, `send_password_changed_alert_email`, `send_term_result_notifications`), and `default` (`generate_timetable_task`).
- Configured with `worker_prefetch_multiplier=1`, `task_acks_late=True`, `task_reject_on_worker_lost=True`, `visibility_timeout=3600`.
- Async DB tasks call `asyncio.run(...)` and `await async_engine.dispose()` in `finally`. Note: if `CELERY_TASK_ALWAYS_EAGER=true` is accidentally left enabled, `POST /api/v1/timetable/schedules/{id}/generate` will block the FastAPI worker synchronously.

---

## 3. Staging Environment & Multi-Tenant Data Seeding

### 3.1 Environment Parity Requirements
Run all load tests against an isolated **staging environment**—never against production or local `--reload` dev servers:
- `ENVIRONMENT=staging` (ensures production Argon2 cost and `verify_rls_enforcement` startup check).
- `CELERY_TASK_ALWAYS_EAGER=false`, with dedicated Celery worker(s) consuming `default,bulk_imports,notifications`.
- Multi-worker Uvicorn/Gunicorn (e.g., 2–4 workers) sized to match target deployment, plus PgBouncer if used in production (`-pooler` URL).
- Mock/sink SMTP/SMS provider for `notifications` tasks so email/SMS delivery latency does not fail or spam real addresses during `notify-term`.

### 3.2 Extending `scripts/seed_realistic_school.py`
`scripts/seed_realistic_school.py` currently seeds **1 school** (`OBHS-OYO`), 1 session (`2026/2027`), 1 term (`First Term`), 6 levels (`JSS1`–`SS3`), 3 departments (`SCI`, `ART`, `COMM`), ~33–39 class groups, 36 subjects, ~700–850 students with subject registrations, 10/10/10/70 assessment definitions, and timetable constraints.

However, it **does not seed** the data needed for full-lifecycle load testing. Build a dedicated staging seeder (e.g., `scripts/seed_load_test.py`) that:
1. **Clones/creates 10–20 tenant schools** (~700–1,000 students each, ~15,000–20,000 total students) to populate PostgreSQL B-tree indexes realistically and stress RLS across concurrent tenants.
2. **Seeds multi-term history** (at least 2 completed/published terms + 1 active scoring term per school) so `get_student_transcript`, `annual` third-term computation, and `list_report_cards` hit realistic multi-term rows.
3. **Pre-seeds `AssessmentScore` and `ScoreSheetStatus`** (`APPROVED` for completed terms; `DRAFT`/`SUBMITTED` for active term test cohorts) so `calculate-term` and `broadsheet` execute full computations.
4. **Pre-seeds `SubjectResult`, `TermReportCard` (`PUBLISHED`), and `ResultAccessToken`s** with high `max_access_limit` (e.g., `100000` for load-test tokens, or a pool of 5,000+ distinct tokens) so `verify/{token}` does not exhaust the default `max_access_limit = 20`.
5. **Pre-seeds `AttendanceRecord`, `AttendanceSummary`, and `Timetable` drafts** for attendance and timetable scenarios.
6. **Exports a JSON fixture manifest** (`load_tests/k6/fixtures/seed_manifest.json`) containing per-school credentials, `school_id`, `academic_term_id`, `class_group_id`s, `class_subject_id`s, `assessment_definition_id`s, `student_id`s, `enrollment_id`s, and `(token, dob, admission_number, qr_payload)` tuples for k6 `SharedArray`.

---

## 4. k6 Project Structure & Auth / Payload Contracts

### 4.1 Suggested Directory Layout
```text
load_tests/
└── k6/
    ├── config.js                  # BASE_URL, scenario toggles, SLO thresholds
    ├── fixtures/
    │   └── seed_manifest.json     # Generated by staging seeder (gitignored)
    ├── lib/
    │   ├── auth.js                # Login, refresh-before-15m, Bearer header helper
    │   └── checks.js              # Status, SuccessResponse, and tenant-isolation checks
    ├── scenarios/
    │   ├── daily_ops.js           # Scenario A: Teachers + Admins steady state
    │   ├── term_computation.js    # Scenario B: Bulk score entry, submit, calculate, broadsheet
    │   ├── result_day_spike.js    # Scenario C: verify/{token}, verify-qr, transcript, report-cards
    │   ├── background_jobs.js     # Scenario D: Timetable generate, bulk-import, notify-term
    │   └── login_storm.js         # Scenario E: Concurrent Argon2 login benchmark
    └── main.js                    # Combined multi-scenario orchestrator
```

### 4.2 Authentication & Token Lifecycle in k6
- **Login:** `POST /api/v1/identity/login`
  - Body: `{"email": "...", "password": "..."}`
  - Response: `{"success": true, "data": {"access_token": "...", "refresh_token": "...", "token_type": "bearer", "expires_in": 900}}`
  - Header on subsequent requests: `Authorization: Bearer <access_token>` (do **not** send a custom `school_id` header; `get_tenant_db` derives `school_id` from the DB-loaded `User` via JWT `sub`).
- **Refresh:** `POST /api/v1/identity/refresh`
  - Body: `{"refresh_token": "<refresh_token>"}` (reads from JSON body).
  - Refresh in k6 VUs when `Date.now() - lastLoginMs > 12 * 60 * 1000` (before the 15-minute `expires_in: 900` window) to avoid artificial login storms during steady-state tests.

### 4.3 Key Payload Contracts for k6 Scripts
- **Bulk Score Entry (`POST /api/v1/assessments/scores/bulk-entry`):**
  ```json
  {
    "class_subject_id": "<uuid>",
    "assessment_definition_id": "<uuid>",
    "scores": [
      { "student_enrollment_id": "<uuid>", "score_status": "SCORED", "raw_score": 8.5 }
    ]
  }
  ```
  *(Note: `raw_score` must be `null` when `score_status` is `"ABSENT"`, `"EXEMPT"`, or `"PENDING"`.)*
- **Mark Class Attendance (`POST /api/v1/structure/class-groups/{class_group_id}/attendance`):**
  ```json
  {
    "academic_term_id": "<uuid>",
    "attendance_date": "2026-10-15",
    "reason": "Backdated register entry",
    "records": [
      { "student_enrollment_id": "<uuid>", "status": "PRESENT" }
    ]
  }
  ```
  *(Note: `reason` is required if `attendance_date` is before the school's current date.)*
- **Calculate Term (`POST /api/v1/computation/calculate-term`):**
  ```json
  { "academic_term_id": "<uuid>", "class_group_id": "<uuid-or-null>" }
  ```
- **Verify Result Token (`GET /api/v1/results/verify/{token}?dob=YYYY-MM-DD` or `?admission_number=...`):** Public endpoint; no `Authorization` header.

### 4.4 Automated Multi-Tenant Isolation Check
Every authenticated k6 `check()` on tenant-scoped responses (e.g., `GET /api/v1/students`, `GET /api/v1/assessments/scores`, `GET /api/v1/computation/broadsheet/{id}`) should assert:
- `r.json("success") === true`
- Returned `school_id` (where present, e.g., `students.items[].school_id`) or `admission_number` prefix strictly matches the VU's assigned tenant school from `seed_manifest.json`.

---

## 5. Workload Scenarios & Traffic Mix

### Scenario A — Daily Classroom & Admin Operations *(Steady State)*
Simulates normal school-day usage across 10+ schools concurrently:
- **45%** Read flows: `GET /api/v1/structure/dashboard/teacher` or `/admin`, `GET /api/v1/students?page=1&page_size=50` (including `?q=` searches), `GET /api/v1/students/{id}`, `GET /api/v1/assessments/sheets?academic_term_id=...`, `GET /api/v1/timetable/schedules/{id}/teachers/me`.
- **30%** Score sheet reads & saves: `GET /api/v1/assessments/scores` -> `POST /api/v1/assessments/scores/bulk-entry` (30–45 students).
- **20%** Attendance: `GET /api/v1/structure/class-groups/{id}/attendance` -> `POST /api/v1/structure/class-groups/{id}/attendance` (30–45 students).
- **5%** Auth refresh / `/api/v1/identity/me`.

### Scenario B — End-of-Term Computation Rush *(Write + CPU Contention)*
Simulates multiple teachers and academic officers finalizing term results:
- Concurrent `POST /api/v1/assessments/scores/bulk-entry` + `POST /api/v1/assessments/submit` + `POST /api/v1/assessments/approve`.
- Concurrent `POST /api/v1/computation/calculate-term` (both single `class_group_id` and full-school `class_group_id: null` runs).
- Concurrent `GET /api/v1/computation/broadsheet/{class_group_id}` and `GET /api/v1/computation/broadsheet?academic_term_id=...&page_size=50`.
- `POST /api/v1/publication/report-cards/bulk-remarks`.

### Scenario C — Result Publication & Parent Verification Spike *(Burst Read + Write-on-GET)*
Simulates result-day traffic after notifications are dispatched:
- High arrival rate on `GET /api/v1/results/verify/{token}?admission_number=...` and `GET /api/v1/results/verify-qr?payload=...` (using a pool of distinct tokens so `max_access_limit` is not exhausted prematurely).
- Admin/staff reads on `GET /api/v1/publication/report-cards` and `GET /api/v1/publication/students/{student_id}/transcript` (multi-term N+1 check).
- `POST /api/v1/publication/notify-term` (`202 Accepted` Celery handoff).

### Scenario D — Background Worker Contention *(Async Stress)*
Runs alongside Scenario A at ~50% load:
- Trigger `POST /api/v1/students/bulk-import` (`bulk_imports` queue) and `POST /api/v1/timetable/schedules/{id}/generate` (`default` queue), polling `GET /api/v1/timetable/schedules/{id}/jobs/{task_id}`.
- Measure whether Celery DB usage or `generate_timetable_task` CPU degrades Scenario A HTTP `p95` latencies.

### Scenario E — Morning Bell Login Storm *(Argon2 Isolation Benchmark)*
- Dedicated short burst (e.g., 20–100 `POST /api/v1/identity/login` req/s for 2 minutes) to measure Argon2 `asyncio.to_thread` saturation and find the max sustainable logins/sec per Uvicorn worker.

---

## 6. Phased Execution Plan

Execute phases sequentially; do not proceed to the next phase if critical `5xx` errors or RLS isolation failures occur.

| Phase | Executor / Profile | Duration | Purpose |
| :--- | :--- | :--- | :--- |
| **1. Smoke** | `constant-vus`: 5–10 VUs (all scenarios) | 3 min | Validate seed manifest, JWT auth/refresh, payload schemas, and zero `5xx`/`422` errors. |
| **2. Baseline** | Scenario A: 50 VUs (~25–40 RPS) | 15 min | Establish steady-state `p50`/`p95`/`p99` latencies, DB pool utilization, and Redis usage. |
| **3. Term & Result Spike** | Scenario A (50 VUs) + Scenario B (`ramping-vus`: 0 -> 30) + Scenario C (`ramping-arrival-rate`: 10 -> 150 RPS over 60s, hold 5m) | 12 min | Stress DB pool limits (`5+5`), `calculate-term`/`broadsheet`, `verify/{token}` write-on-GET, and `transcript` N+1. |
| **4. Stress / Breakpoint** | `ramping-arrival-rate`: +25 RPS every 3 min up to 300+ RPS | 20–30 min | Identify exact RPS ceiling where `p95` violates SLOs or `QueuePool`/Redis timeouts begin. |
| **5. Soak** | Scenario A + periodic Scenario B/C at ~70% of safe peak RPS | 1–2 hours | Detect memory leaks in Uvicorn/Celery workers, unclosed sessions, or Redis key/connection growth. |

---

## 7. k6 Thresholds (SLOs) & Observability

### 7.1 Suggested k6 `thresholds`
Tag requests in k6 (e.g., `{ endpoint: "bulk_score_entry" }`) and enforce:

- **Global Error Rate:** `http_req_failed{expected_response:true}: ['rate<0.005']` (< 0.5% unexpected failures; tag intentional rate-limit checks separately if testing `429`s).
- **Auth (`POST /api/v1/identity/login`):** `http_req_duration{endpoint:login}: ['p(95)<600', 'p(99)<1200']`
- **Standard Reads (`students`, `scores`, `broadsheet` single class, `dashboard`):** `http_req_duration{group:reads}: ['p(95)<300', 'p(99)<600']`
- **Bulk Writes (`bulk_score_entry`, `mark_class_attendance`):** `http_req_duration{group:bulk_writes}: ['p(95)<800', 'p(99)<1500']` *(Note: tighten to `<400ms` after N+1 loops in `bulk_score_entry` and `_sync_daily_summary` are batched.)*
- **Heavy Computation (`calculate_term`, `broadsheet` full-school, `transcript`):** `http_req_duration{group:heavy_compute}: ['p(95)<2500', 'p(99)<5000']`
- **Public Verification (`verify_token`, `verify_qr`):** `http_req_duration{group:public_verify}: ['p(95)<200', 'p(99)<400']`

### 7.2 Metrics to Capture Alongside k6
- **PostgreSQL:** Active vs. idle-in-transaction connections (`pg_stat_activity`), lock waits (especially on `term_report_cards`, `subject_results`, `result_access_tokens`), and top queries by total execution time (`pg_stat_statements`).
- **Redis:** `connected_clients`, `used_memory_human`, and `SLOWLOG` entries (watch `REDIS_MAX_CONNECTIONS = 20` per worker).
- **Application / Celery:** Uvicorn worker CPU/RSS memory, thread-pool queue depth during logins, and Celery queue backlog (`celery -A app.core.celery_app inspect active` / Redis list lengths for `default`, `bulk_imports`, `notifications`).

---

## 8. Pre-Flight Checklist

- [ ] Target is **staging**, never production, and `ENVIRONMENT` is **not** `testing`/`test` (`TESTING != 1`).
- [ ] `CELERY_TASK_ALWAYS_EAGER=false` and Celery workers are running for `default`, `bulk_imports`, and `notifications`.
- [ ] Multi-school staging seed (`seed_manifest.json`) is populated with scores, published report cards, and high-limit `ResultAccessToken`s.
- [ ] Email/SMS providers are sandboxed/mocked before triggering `notify-term` or `forgot-password`.
- [ ] k6 output is wired to JSON/CSV or Prometheus Remote Write + Grafana (`k6 run --out ... load_tests/k6/main.js`) so latency spikes can be correlated with Postgres and Redis telemetry.
