# AGENTS.md: Developer & Agent Engineering Manual
**Project:** Olivet Academic Records Management Platform  
**Target Pilot:** Olivet Baptist High School, Oyo, Nigeria  
**Architecture Spec:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (Authoritative Source of Truth)

---

## 1. Agent Mission & Purpose

You are working on the **Olivet Academic Records Management Platform**, a production-grade, multi-tenant SaaS application built for Nigerian secondary schools (junior and senior) and extensible internationally.

Before writing code or making architectural adjustments, **always consult `ARCHITECTURE.md`**. Every data model, endpoint contract, computation rule, and security constraint in this repository is explicitly specified there.

---

## 2. Core Architectural Invariants (Non-Negotiables)

When designing, modifying, or implementing code, you must enforce these 8 non-negotiable rules:

1. **Multi-Tenancy Defense-in-Depth:**
   - Every tenant table **must** have `school_id: UUID NOT NULL REFERENCES schools(id)`.
   - Never write a database query that relies solely on application-level filtering without verifying that the database session has executed:
     `SET LOCAL app.current_school_id = :school_id`
   - Use the `get_tenant_db` dependency in all tenant-scoped FastAPI route handlers.
2. **UUIDv7 for Primary Keys:**
   - Use time-ordered UUIDv7 for all primary keys to guarantee optimal B-Tree index performance while preventing integer enumeration attacks across schools.
3. **Immutability of Published Academic Records:**
   - When a term is `PUBLISHED`, results become relationally frozen.
   - `term_report_cards` and `subject_results` store computed grades and ranks alongside a `frozen_config_snapshot` JSON blob.
   - **Never** recalculate historical published scores dynamically on read.
4. **Hybrid Access Control (RBAC + Scoped ABAC):**
   - Do not restrict permissions to rigid role enums.
   - Staff can have a baseline role (e.g., `TEACHER`) plus direct delegated permissions (`user_permission_grants`) scoped to `SCHOOL_WIDE`, `DEPARTMENT`, `ACADEMIC_LEVEL`, or `CLASS_GROUP`.
   - Enforce authorization using `require_permission(code, scope_extractor)` from `app.core.permissions`.
5. **Trade Subjects & Electives Registration:**
   - Senior secondary students choose trade subjects (e.g., Catering Craft, Data Processing).
   - Never assume all students in an arm take every subject in that arm.
   - Query `student_subject_registrations` to get the active roster for an elective subject.
   - Compute total obtainable marks dynamically based on each student's registered subjects.
6. **Configurable by Architecture, Familiar by Default:**
   - Olivet’s structures (JSS1–SS3, Science/Arts/Commercial, arm letters `O`, `L`, `I`, `V`, `E`, `T`, `B`, `H`, `S`, `Y`, and the Olivet-specific 10/10/10/70 assessment structure: Test 1, Test 2, Assignments/Attendance, and Terminal Examination) are seed defaults, **never hardcoded business logic**.
   - Junior secondary levels must function seamlessly with `department_id = NULL`.
7. **Zero Hard Deletions of Academic or Staff History:**
   - When a student or teacher leaves, updates must set their lifecycle status (`WITHDRAWN`, `TRANSFERRED`, `INACTIVE`) and apply soft-deletion (`deleted_at`).
   - Foreign keys to historical records must use `ON DELETE RESTRICT` or `ON DELETE SET NULL`.
8. **Decoupled Heavy Operations (Celery & Redis):**
   - Never process bulk Excel imports or send emails/SMS synchronously in an HTTP request worker. Always dispatch them to Celery.
   - Report cards are assembled and exported to PDF on the client side from verified JSON payloads, avoiding server-side PDF generation bottlenecks and storage costs.

---

## 3. Technology Stack Reference

- **Language:** Python 3.12+ (Strict typing with `typing` and `mypy`)
- **Web Framework:** FastAPI (v0.111+)
- **ORM:** SQLAlchemy 2.0 (`AsyncSession` with `asyncpg`)
- **Data Validation:** Pydantic v2 (`BaseModel`, `Field`, `model_validator`)
- **Database:** PostgreSQL 16 (Row-Level Security enabled)
- **Migrations:** Alembic (Async migration runner)
- **Task Queue:** Celery 5.4 + Redis 7
- **Admin Dashboard:** SQLAdmin (`sqladmin`)
- **Document Model:** Client-Side Document Assembly & Local PDF Export (from verified JSON payload)
- **Testing:** `pytest`, `pytest-asyncio`, `httpx.AsyncClient`, `Hypothesis`

---

## 4. Directory & Module Conventions

All application code resides under `app/`:

```
app/
├── main.py                  # FastAPI app factory, middleware, CORS, routers
├── admin.py                 # SQLAdmin configuration
├── core/                    # Shared infrastructure
│   ├── config.py            # Pydantic Settings
│   ├── database.py          # SQLAlchemy async engine & sessionmaker
│   ├── security.py          # Passlib Argon2, PyJWT RS256, MFA TOTP
│   ├── dependencies.py      # get_tenant_db, get_current_user
│   ├── permissions.py       # Hybrid permission resolver (require_permission)
│   └── exceptions.py        # Standardized Exception handlers
└── modules/                 # Bounded domain contexts
    ├── common/              # Base declarative models, TenantMixin, TimestampMixin
    ├── identity/            # Users, StaffProfiles, Permissions, Auth routes
    ├── academic_structure/  # Sessions, Terms, Levels, Departments, Arms, ClassGroups
    ├── students/            # Students, Demographics, Enrollments
    ├── subjects/            # Subjects, Offerings, ClassSubjects, TeacherAssignments
    ├── assessments/         # AssessmentDefinitions, Scores, Bulk entry
    ├── computation/         # Pure Python Computation Engine & Broadsheets
    ├── distribution/        # Result Tokens, Report JSON Payloads, Notifications
    └── audit/               # Immutable Security Audit Logging
```

### Module Responsibilities:
- `models.py`: SQLAlchemy 2.0 mapped entities and relationships.
- `schemas.py`: Pydantic v2 request/response schemas.
- `services.py`: Business logic, domain state transitions, and database writes.
- `router.py`: FastAPI `APIRouter` with dependency injection and status codes.
- `engine.py` (in `computation/`): Pure calculation functions with **zero database or I/O imports**.

---

## 5. Coding Standards & Idioms

### 5.1 Service Layer Pattern
Never write business logic or database writes inside FastAPI route handlers. Route handlers must strictly:
1. Accept and validate the request via Pydantic schemas.
2. Enforce authentication and permissions via `Depends`.
3. Delegate execution to a function in `services.py`.
4. Return the response model.

### 5.2 Pure Computation Engine
The grading and ranking engine (`app/modules/computation/engine.py`) must remain pure Python:
- Accept plain Python dictionaries, Decimals, and dataclasses.
- Return calculated data structures.
- Use `Decimal` with `ROUND_HALF_UP` for all mark computations.
- Never import SQLAlchemy models, DB sessions, or HTTP request objects into `engine.py`.

### 5.3 Human-Friendly Error Messaging Standard
Every error message returned by the API must read like natural, empathetic, actionable human English. Teachers, school administrators, and parents will read these messages:
- **Never expose raw technical codes** (e.g. `scores:approve`, `IntegrityError`) as the main error message. Technical codes belong in `error.code` or `details`.
- **Always explain what happened and what to do next**:
  - *Bad:* `"Access denied: Resource does not belong to your tenant."`
  - *Good:* `"We couldn't find this record in your school's account. Please check that you're signed in to the right school."`
  - *Bad:* `"Missing required permission: 'scores:approve' for scope 'Science'"`
  - *Good:* `"You don't have permission to approve scores for Science. If you need this access, please ask your school administrator or academic officer."`
  - *Bad:* `"ScoresIncompleteException"`
  - *Good:* `"Scores cannot be submitted yet because 3 student(s) in Chemistry are still missing a score. Please enter their scores, or mark them as absent or exempt before submitting."`

---

## 6. Common Commands for Agents & Developers

### Environment & Migrations
```bash
# Run database migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "add_student_subject_registrations"

# Rollback one migration
alembic downgrade -1
```

### Running the Services
```bash
# Start FastAPI development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker for general, bulk import, and notification queues
celery -A app.core.celery_app worker -Q default,bulk_imports,notifications -l info
```

### Linting & Formatting
```bash
# Check lint rules
ruff check app tests alembic

# Apply safe automatic fixes
ruff check --fix app tests alembic

# Format code
ruff format app tests alembic

# Verify formatting without changing files
ruff format --check app tests alembic
```

### Testing & Verification
```bash
# Run full test suite
pytest

# Run tests with coverage
pytest --cov=app tests/

# Run cross-tenant isolation tests specifically
pytest tests/test_tenant_isolation.py

# Run pure computation engine property tests
pytest tests/test_computation_engine.py
```

---

## 7. Instructions for Subagents & Swarm Collaboration

When multiple agents work on this codebase in parallel:
1. **Never edit files outside your assigned module** without coordinating.
2. If modifying database schemas, you must update `ARCHITECTURE.md` and generate the corresponding Alembic revision.
3. Every new service endpoint must have an accompanying automated test verifying both **success** and **unauthorized cross-tenant access denial**.
