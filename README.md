# Academic Records Management Platform
**Multi-Tenant SaaS for Primary & Secondary Academic Records, Grading & Result Distribution**
**Target Pilot:** Olivet Baptist High School, Oyo, Nigeria
**Primary Support:** M9 (Nigerian Basic 1–6) — LIVE

---

## 📌 Overview

The **Academic Records Management Platform** is a secure, high-performance, multi-tenant SaaS designed for Nigerian and international primary and secondary schools. It replaces error-prone spreadsheets and fragmented result compilation with an automated, auditable system of record for:
- Pupil/student admissions, session enrollments, class arms, and (secondary only) departments.
- Subject offerings and granular teacher-arm assignments.
- Configurable Continuous Assessment (CA) and examination score entry.
- Dynamic result computation (grades, standard competition ranking, subject/class averages).
- Cryptographic snapshotting and tamper-resistant digital report cards.
- Secure, time-limited magic link result delivery to parents without unencrypted email attachments.

The platform is designed around the core principle: **"Configurable by architecture, familiar by default"**. Olivet's unique traditions (historic arm letters `O`, `L`, `I`, `V`, `E`, `T`, `B`, `H`, `S`, `Y`, department structures, and the 10/10/10/70 assessment structure — Test 1, Test 2, Assignments/Attendance, and Terminal Examination) serve as the pre-configured seed template without hardcoding business rules into the codebase. M9 (LIVE) adds a Nigerian three-term primary template: Basic 1–6, arms A–D, no departments, compulsory core subjects only, an A–F grading scale, and the same 10/10/10/70 assessment split.

---

## 🏛️ System Architecture & Specifications

The authoritative technical architecture, schema definitions, mathematical computation engine, and security model are maintained in:

👉 **[ARCHITECTURE.md](./ARCHITECTURE.md)** (Source of Truth)

For autonomous agents and developers working on this codebase:

👉 **[AGENTS.md](./AGENTS.md)** (Operational Guidelines, Invariants & Conventions)

Delivery status: M1–M5, M8, and M9 are live; M5A attendance, M6 verification/delivery, and M7 hardening are still planned or partial. M9 Nigerian Primary Basic 1–6 support is **LIVE** and specified in `ARCHITECTURE.md` §17 and `BACKEND_DEV_PLAN.md` Milestone 9.

---

## 💻 Technology Stack

| Layer | Technology | Key Capabilities |
|---|---|---|
| **Backend Framework** | **FastAPI** (Python 3.12+) | Async I/O, Pydantic v2 validation, OpenAPI 3.1 client generation |
| **ORM & Migrations** | **SQLAlchemy 2.0 (Async)** + **Alembic** | AsyncSession via `asyncpg`, declarative mappings, migration tracking |
| **Database** | **PostgreSQL 16** | Row-Level Security (RLS), JSONB, composite constraints, UUIDv7 |
| **Task Queue & Broker** | **Celery 5.4** + **Redis 7** | Dedicated worker queues for bulk student imports and email/SMS |
| **Document Delivery** | **Client-Side Document Assembly** | Report cards assembled and printed/downloaded client-side from verified JSON |
| **Administration** | **SQLAdmin** | Starlette-native admin panel mounted at `/admin/` |

---

## 🔑 Key Architectural Invariants

1. **Multi-Tenancy Defense-in-Depth:** Every query is scoped to the tenant at the application level via FastAPI `Depends(get_tenant_db)` and enforced at the database kernel level via PostgreSQL Row-Level Security (`SET LOCAL app.current_school_id = :school_id`).
2. **Immutable Academic Snapshots:** Once a term's results are `PUBLISHED`, the student's scores, grades, positions, and grading scale schema are relationally frozen. Retroactive changes to subsequent terms will never alter past transcripts.
3. **Hybrid Access Control (RBAC + ABAC):** Baseline role defaults (`TEACHER`, `ACADEMIC_OFFICER`) are paired with direct delegated user grants (e.g., an HOD or Form Master receiving scoped administrative rights while remaining a teaching staff).
4. **Student Subject Registrations:** Electives and trade subjects (e.g., Catering Craft, Data Processing) are registered individually per student (`student_subject_registrations`), ensuring teachers only see enrolled students on score sheets and total obtainable marks are dynamically calculated. Primary schools (M9, LIVE) have only compulsory core subjects, so all registrations are automatic.
5. **Canonical API Contract:** Every endpoint uses a domain-namespaced route (`/api/v1/<domain>/...`) and returns either `{"success": true, "data": ...}` or `{"error": {"code", "message", "details", "path"}}`. `ARCHITECTURE.md` §6.2 is authoritative; legacy flat aliases are not supported.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- Docker & Docker Compose
- PostgreSQL 16
- Redis 7

### Local Setup
```bash
# 1. Clone repository and navigate to workspace
cd /home/eyitayo/Projects/Olivet

# 2. Copy environment file
cp .env.example .env

# 3. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 4. Install dependencies, including linting and test tools
pip install -e ".[dev]"

# 5. Run database migrations (uses MIGRATION_DATABASE_URL / admin role)
alembic upgrade head

# 6. Start development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Quality Checks
Run these before opening a pull request. CI enforces the same checks.

```bash
# Lint and formatting
ruff check app tests alembic
ruff format --check app tests alembic

# Tests with coverage
pytest --cov=app --cov-report=term-missing
```

### Database Roles & Row-Level Security
PostgreSQL Row-Level Security only applies to roles that are neither `SUPERUSER` nor `BYPASSRLS`. The application therefore connects with the non-superuser `APP_POSTGRES_USER` (default `olivet_runtime`) while migrations run as `POSTGRES_USER` (`olivet_app`) via `MIGRATION_DATABASE_URL`.

Fresh Docker volumes create and grant the runtime role automatically through `docker/initdb/01-create-runtime-role.sh`. For an existing database volume, run that script once as the admin role before starting the app.

For managed PostgreSQL (Neon) and managed Redis (Upstash), see the deployment runbook: **[docs/managed-deployment.md](./docs/managed-deployment.md)**

---

## 📖 License & Confidentiality
Confidential and Proprietary. All rights reserved.
