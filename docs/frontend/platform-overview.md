# Platform Overview

## Purpose

Olivet is a multi-tenant academic records platform for Nigerian secondary schools. The pilot tenant is Olivet Baptist High School, Oyo. The platform is designed for junior and senior secondary schools and can be configured for other schools without changing application code.

The frontend should feel like a familiar school records system while remaining driven by the school's configuration: sessions, terms, levels, departments, arms, subjects, assessment weights, grading scales, and permissions are data, not hardcoded UI assumptions.

## What the platform does

Live today:

- Onboard a school tenant and its first administrator.
- Invite staff, accept invitations, manage delegated permissions, and deactivate staff.
- Configure academic sessions, terms, levels, departments, class arms, and class groups.
- Register students, assign them to class groups, enroll them per session, and transition their lifecycle status.
- Bulk import students and staff asynchronously from Excel or CSV.
- Maintain subjects, subject offerings, class subjects, teacher assignments, and elective/trade registrations.
- Define assessments, enter scores, submit score sheets, approve them, and unlock them with a reason.

Planned next:

- Compute term and cumulative annual results.
- Rank students and produce broadsheets.
- Publish term results, create immutable snapshots, and issue expiring result links.
- Deliver report-card JSON payloads for client-side PDF rendering.
- Provide audit log access, SQLAdmin operations, and production hardening.

## Architecture at a glance

```mermaid
flowchart LR
    Staff[School staff browser] -->|HTTPS JSON| API[FastAPI /api/v1]
    API -->|tenant session| DB[(PostgreSQL 16 with RLS)]
    API -->|cache and job state| Redis[(Redis 7)]
    API -->|dispatch work| Celery[Celery workers]
    Celery -->|bulk imports and notifications| DB
    Celery --> Redis
    Staff -->|render verified JSON| PDF[Client-side PDF]
```

Key properties:

- FastAPI with async SQLAlchemy 2.0 and `asyncpg`.
- PostgreSQL Row-Level Security for tenant isolation.
- UUIDv7 primary keys for time-ordered indexes and non-enumerable identifiers.
- Celery and Redis for bulk imports and notifications.
- No server-side report-card PDF generation or storage.
- Immutable published academic records in the planned M5/M6 design.

## Tenancy and security model

Every tenant table carries:

- `school_id UUID NOT NULL REFERENCES schools(id)`.
- Request-scoped database sessions that run `SET LOCAL app.current_school_id = :school_id`.
- Queries that still filter by `school_id` as defense in depth.

Frontend implications:

- A school user belongs to exactly one school tenant in normal operation. Platform super admins may lack a school context.
- Cross-tenant identifiers must return a not-found response rather than leaking that a record exists elsewhere.
- Never construct API URLs from a `school_id` supplied by the browser. The backend derives the tenant from the authenticated user.
- School onboarding (`POST /api/v1/identity/schools`) is the public entry point for creating a tenant and first administrator.

Additional constraints:

- Historical records are soft-deleted with `deleted_at`; academic and staff history is not hard-deleted.
- Users are deactivated rather than deleted.
- Sensitive academic records become frozen on publication in the planned design.
- Access tokens are short-lived and revocable by `jti`.

## Domain model

```mermaid
flowchart TD
    School --> Config[SchoolConfiguration]
    School --> Session[AcademicSession]
    Session --> Term[AcademicTerm]
    School --> Level[AcademicLevel]
    School --> Dept[Department]
    School --> Arm[ClassArm]
    Session --> Group[ClassGroup]
    Level --> Group
    Dept --> Group
    Arm --> Group
    Group --> Enrollment[StudentEnrollment]
    Student --> Enrollment
    Session --> Offering[SubjectOffering]
    Subject --> Offering
    Offering --> ClassSubject[ClassSubject]
    Group --> ClassSubject
    ClassSubject --> Assessment[AssessmentDefinition]
    ClassSubject --> Registration[StudentSubjectRegistration]
    Enrollment --> Registration
    Assessment --> Score[AssessmentScore]
    Enrollment --> Score
    ClassSubject --> TeacherAssignment[TeacherAssignment]
    User --> TeacherAssignment
```

### Structure

- **Academic session**: a school year, with start and end dates. One session can be marked current per school; marking a new session current unsets the previous one.
- **Academic term**: a date range inside a session, ordered by `term_order`. One term can be current per school. A term can be locked for scoring; while locked, term details cannot be changed and the term cannot be retired.
- **Academic level**: a level such as JSS1 or SS1, with a category of `JUNIOR_SECONDARY` or `SENIOR_SECONDARY` and a sort order.
- **Department**: senior-secondary department such as Science, Arts, or Commercial. Junior-secondary classes do not use a department.
- **Class arm**: the arm letter or label for a class, such as `O`, `L`, `I`, `V`, `E`, `T`, `B`, `H`, `S`, or `Y`.
- **Class group**: the join of session, level, optional department, and arm. It may have one class teacher. Its display name is either supplied or composed as `{level.code} {department.name} {arm.name}`; junior classes omit the department.

### Students and enrollment

- A **student** is a person with a school-wide admission number, demographics, guardian information, and lifecycle status.
- An **enrollment** links a student to a class group for one academic session.
- Enrollment is the unit that owns subject registrations, scores, and eventually report cards.
- A student can have only one enrollment per session.
- Enrolling an `ADMITTED` student changes them to `ACTIVE`.
- Enrollment automatically registers compulsory subjects. Trade and free electives require explicit registration.

### Subjects and teaching

- A **subject** is the school-wide catalog entry, for example Chemistry or Data Processing.
- A **subject** carries `is_elective` and `is_trade_subject` flags. A **subject offering** connects that subject to a session, level, and optional department.
- A **class subject** connects an offering to a specific class group. It is the unit teachers are assigned to and the unit results are computed for.
- A **teacher assignment** links a staff user to a class subject.
- A **student subject registration** links an enrollment to the class subject the student actually takes.

This distinction matters for electives: not every student in an arm takes every class subject. Score sheets and result totals must use the active registrations for that class subject, never the whole class roster.

### Assessments and scoring

- An **assessment definition** describes one score component for a class subject in a term, such as Test 1, Test 2, Assignments/Attendance, or Terminal Examination.
- Creating a class subject clones the school's assessment weight schema into definitions for every non-retired term in that session.
- An **assessment score** stores a student's raw score and status: `SCORED`, `ABSENT`, `EXEMPT`, or `PENDING`.
- A **score sheet status** tracks the class subject's lifecycle in a term: `DRAFT`, `SUBMITTED`, then `APPROVED`.
- Submission requires every active registered student to have a non-`PENDING` status for every assessment definition.
- Approval requires prior submission.
- Unlocking returns a submitted or approved sheet to `DRAFT`, recording who unlocked it, when, and why. Already-draft sheets cannot be unlocked.

## State machines

### Student lifecycle

```mermaid
stateDiagram-v2
    [*] --> ADMITTED
    ADMITTED --> ACTIVE
    ADMITTED --> WITHDRAWN
    ACTIVE --> SUSPENDED
    ACTIVE --> WITHDRAWN
    ACTIVE --> TRANSFERRED
    ACTIVE --> GRADUATED
    SUSPENDED --> ACTIVE
    SUSPENDED --> WITHDRAWN
    SUSPENDED --> TRANSFERRED
    WITHDRAWN --> [*]
    TRANSFERRED --> [*]
    GRADUATED --> [*]
```

`WITHDRAWN`, `TRANSFERRED`, and `GRADUATED` are terminal. Only `ADMITTED` and `ACTIVE` students can be enrolled.

### Enrollment lifecycle

```mermaid
stateDiagram-v2
    [*] --> ENROLLED
    ENROLLED --> PROMOTED
    ENROLLED --> REPEATING
    ENROLLED --> TRANSFERRED
    ENROLLED --> WITHDRAWN
    PROMOTED --> [*]
    REPEATING --> [*]
    TRANSFERRED --> [*]
    WITHDRAWN --> [*]
```

### Score sheet lifecycle

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> SUBMITTED: submit when complete
    SUBMITTED --> APPROVED: approve
    SUBMITTED --> DRAFT: unlock with reason
    APPROVED --> DRAFT: unlock with reason
```

## Olivet pilot defaults

Onboarding stores the following school configuration:

- Grading scale:

| Grade | Range | Remark |
| --- | --- | --- |
| A1 | 75.00-100.00 | Excellent |
| B2 | 70.00-74.99 | Very Good |
| B3 | 65.00-69.99 | Good |
| C4 | 60.00-64.99 | Credit |
| C5 | 55.00-59.99 | Credit |
| C6 | 50.00-54.99 | Credit |
| D7 | 45.00-49.99 | Pass |
| E8 | 40.00-44.99 | Pass |
| F9 | 0.00-39.99 | Fail |

- Assessment weights:

| Code | Name | Max score | Weight |
| --- | --- | --- | --- |
| `TEST1` | Test 1 | 10 | 10 |
| `TEST2` | Test 2 | 10 | 10 |
| `ASSIGN` | Assignments/Attendance | 10 | 10 |
| `EXAM` | Terminal Examination | 70 | 70 |

- Default term definitions named First Term, Second Term, and Third Term. These are configuration values; academic term rows are created explicitly during structure setup.
- Timezone: `Africa/Lagos`.
- Currency: `NGN`.
- Report-card template identifier: `default_v1`.

The target Olivet blueprint in `ARCHITECTURE.md` includes JSS1-JSS3 and SS1-SS3, Science/Arts/Commercial departments, and arm letters `O, L, I, V, E, T, B, H, S, Y`. These values are seed defaults, not hardcoded business logic.

## API conventions

- All application routes live under `/api/v1`.
- Allowed domains: `identity`, `structure`, `students`, `enrollments`, `subjects`, `class-subjects`, `assessments`, `computation`, `publication`, `results`, `audit`.
- Resource names are plural and kebab-case.
- Path parameters use named identifiers such as `{student_id}`.
- Verbs are reserved for workflow or bulk actions: `submit`, `approve`, `unlock`, `publish-term`, `bulk-import`, `bulk-entry`, `bulk`.
- Success responses always use the success envelope.
- Failure responses always use the error envelope.
- Paginated collections use `items` plus `meta`.

### Date and time handling

- Dates are ISO `YYYY-MM-DD`.
- Timestamps are ISO 8601 with timezone.
- Student bulk import also accepts `DD/MM/YYYY`, `DD-MM-YYYY`, and `DD.MM.YYYY`.
- Display dates in the school's timezone, currently `Africa/Lagos`.

### Authentication

```mermaid
sequenceDiagram
    participant Browser
    participant API
    Browser->>API: POST /api/v1/identity/login
    API-->>Browser: access token + refresh token; refresh cookie set
    Browser->>API: GET /api/v1/identity/me with bearer token
    API-->>Browser: user, school, and effective permission codes
    Browser->>API: POST /api/v1/identity/refresh when access expires
    API-->>Browser: rotated token pair
    Browser->>API: POST /api/v1/identity/logout
    API-->>Browser: tokens revoked and cookie cleared
```

## Live and planned capability matrix

| Capability | Status | Notes |
| --- | --- | --- |
| School onboarding | **LIVE** | Public tenant creation with first school administrator. |
| Login, refresh, logout | **LIVE** | JWT access token, HTTP-only refresh cookie, Redis revocation. |
| Current user and effective permissions | **LIVE** | `GET /identity/me`. |
| Staff invitations | **LIVE** | 72-hour invitation token. |
| Staff permission grants | **PARTIAL** | Grant create/revoke is live and `DEPARTMENT`/`ACADEMIC_LEVEL`/`CLASS_GROUP` scoping is enforced for structure, subjects, teacher assignments, enrollments, and scores, but there is still no grant-list endpoint. |
| Staff deactivation | **LIVE** | Deactivated users cannot authenticate. |
| Structure configuration | **LIVE** | Sessions, terms, levels, departments, arms, class groups. |
| Student registration and lifecycle | **LIVE** | Create, update, status transitions. |
| Student enrollment | **LIVE** | One enrollment per student per session; auto-registers compulsory subjects. |
| Student bulk import | **LIVE** | Asynchronous with polling. |
| Staff bulk import | **LIVE** | Asynchronous with polling. |
| Subject catalog and offerings | **LIVE** | Junior offerings reject departments. |
| Class subjects and teacher assignments | **LIVE** | Per class subject and bulk assignment. |
| Elective and trade registration | **LIVE** | Explicit registration for non-compulsory subjects. |
| Assessment definitions | **LIVE** | Auto-seeded per class subject and term. |
| Score entry | **LIVE** | Bulk-shaped entry endpoint, including one-row saves. |
| Score sheet submit, approve, unlock | **LIVE** | Completeness and lifecycle enforced. |
| Result computation and broadsheets | **PLANNED** | Models exist; no routes or engine. |
| Cumulative annual results and promotion | **PLANNED** | Fields exist on planned models; no service. |
| Term publication and frozen snapshots | **PLANNED** | Models exist; no routes or publication service. |
| Result access tokens and magic links | **PLANNED** | Token model exists; no delivery or verification route. |
| Report-card JSON payload | **PLANNED** | Contract described in `BACKEND_DEV_PLAN.md`; not live. |
| Email/SMS notifications | **PLANNED** | Celery notification queue exists; result notifications are not live. |
| Audit log read API | **PLANNED** | Writes are recorded; no read endpoint. |
| SQLAdmin dashboard | **PLANNED** | Not mounted in `app/main.py`. |
| Olivet seed script | **PLANNED** | Only runtime role SQL exists today. |

## Planned computation and publication design

The M5/M6 design introduces:

- `SubjectResult`: frozen subject totals, grades, remarks, ranks, class statistics, and annual cumulative fields.
- `TermReportCard`: frozen term totals, position, attendance, comments, promotion decision, publication status, and a required `frozen_config_snapshot`.
- `ResultAccessToken`: hashed time-limited token with access count, maximum access count, revocation, expiry, and last-access metadata.

Rules the frontend must preserve when these become live:

- Published results are immutable. Never recalculate historical scores on read.
- Report cards are assembled from verified JSON payloads, not live queries against mutable scores.
- Result links expire, can be revoked, and have an access limit.
- The frontend renders PDFs locally from the payload.
- `PENDING` scores must block submission, approval, and publication.
- `ABSENT` contributes `0.00` and is displayed as `ABS`.
- `EXEMPT` omits that assessment and scales the attempted work proportionally.
- Total obtainable marks depend on each student's active subject registrations.

## Frontend design implications

- Build navigation and actions from effective permissions, not role names alone.
- Fetch `/identity/me` after login and after permission changes.
- Treat `202 Accepted` imports as background jobs with progress polling.
- Preserve server ids and timestamps. Do not generate ids client-side.
- Use server-provided display names for class groups, but allow custom display names.
- Distinguish subjects, offerings, class subjects, and registrations in every screen and selector.
- Do not assume every student in a class takes every subject.
- Do not assume `/identity/me` proves class-level visibility. It returns permission codes only, not which department/level/class-group a scoped grant covers -- the backend enforces that scope on each resource, but the frontend still can't read it back from `/me`.
- Do not build result publication or PDF export against live routes yet.
- Display human-friendly `error.message` text and use `error.code` only for branching.
- Support empty states for schools before sessions, terms, class groups, subjects, and enrollments exist.
