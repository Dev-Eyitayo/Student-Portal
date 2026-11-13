# Olivet Academic Records Management Platform
**Multi-Tenant SaaS for Secondary Academic Records, Grading & Result Distribution**  
**Target Pilot:** Olivet Baptist High School, Oyo, Nigeria

---

## 📌 Overview

The **Olivet Academic Records Management Platform** is a secure, high-performance, multi-tenant SaaS designed for Nigerian and international secondary schools. It replaces error-prone spreadsheets and fragmented result compilation with an automated, auditable system of record for:
- Student admissions, session enrollments, class arms, and departments.
- Subject offerings and granular teacher-arm assignments.
- Configurable Continuous Assessment (CA) and examination score entry.
- Dynamic result computation (grades, standard competition ranking, subject/class averages).
- Cryptographic snapshotting and tamper-resistant digital report cards.
- Secure, time-limited magic link result delivery to parents without unencrypted email attachments.

The platform is designed around the core principle: **"Configurable by architecture, familiar by default"**. Olivet's unique traditions (historic arm letters `O`, `L`, `I`, `V`, `E`, `T`, `B`, `H`, `S`, `Y`, department structures, and 20/20/60 assessment weights) serve as the pre-configured seed template without hardcoding business rules into the codebase.

---

## 🏛️ System Architecture & Specifications

The authoritative technical architecture, schema definitions, mathematical computation engine, and security model are maintained in:

👉 **[ARCHITECTURE.md](./ARCHITECTURE.md)** (Source of Truth)

For autonomous agents and developers working on this codebase:

👉 **[AGENTS.md](./AGENTS.md)** (Operational Guidelines, Invariants & Conventions)

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
4. **Student Subject Registrations:** Electives and trade subjects (e.g., Catering Craft, Data Processing) are registered individually per student (`student_subject_registrations`), ensuring teachers only see enrolled students on score sheets and total obtainable marks are dynamically calculated.

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
pip install -U pip poetry

# 4. Install dependencies
poetry install

# 5. Run database migrations
alembic upgrade head

# 6. Start development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📖 License & Confidentiality
Confidential and Proprietary. All rights reserved.
