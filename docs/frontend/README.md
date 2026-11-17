# Olivet Frontend Handoff

This directory is the frontend contract for the Olivet Academic Records Management Platform. It explains what the backend exposes today, what is planned next, and how school staff, administrators, and result recipients move through the product.

## Who this is for

- Frontend engineers building the staff web application.
- Product and design partners mapping screens to real backend behavior.
- QA engineers writing end-to-end journeys and permission tests.
- Future integrators who need the canonical API vocabulary.

The backend is the source of truth. When this handoff and the code disagree, inspect the implementation and update this documentation rather than inventing a frontend-only contract.

## Document map

| Document | Use it for |
| --- | --- |
| [platform-overview.md](./platform-overview.md) | Product scope, domain model, tenancy, architecture, API conventions, and live versus planned capabilities. |
| [personas-and-permissions.md](./personas-and-permissions.md) | User personas, system roles, permission codes, scoped grants, and UI authorization rules. |
| [user-journeys.md](./user-journeys.md) | End-to-end flows for onboarding, structure setup, students, subjects, assessments, and planned computation/publication. |

## Status labels

Every material capability in these documents is labelled:

- **LIVE**: implemented in the backend and exposed through FastAPI routes.
- **PLANNED**: designed in `ARCHITECTURE.md`, `BACKEND_DEV_PLAN.md`, or persisted only as database models. No frontend should assume the endpoint exists yet.
- **PARTIAL**: a model or supporting service exists, but the complete user-facing workflow is not live.

Current milestone status:

| Milestone | Scope | Status |
| --- | --- | --- |
| M1 | Identity, school onboarding, staff invitations, permissions, deactivation | **LIVE** |
| M2 | Academic structure, students, enrollments, bulk imports | **LIVE** |
| M3 | Subjects, offerings, class subjects, teacher assignments, elective registration | **LIVE** |
| M4 | Assessment definitions, score entry, submission, approval, unlock | **LIVE** |
| M5 | Result computation, broadsheets, cumulative results, promotion | **PLANNED** |
| M6 | Term publication, report-card payloads, result access tokens, notifications | **PLANNED** |
| M7 | Admin dashboard, Olivet seed script, security hardening, performance verification | **PLANNED** |

Milestone `LIVE` means the main workflow exists. Scoped permission enforcement remains **PARTIAL** within M1 only because `/identity/me` does not return grant scopes and there is no grant-list endpoint; actual enforcement -- including list filtering -- is live across structure/students/subjects/assessments. See [personas-and-permissions.md](./personas-and-permissions.md) for exactly which routes are scope-aware.

## API quick reference

- Base URL in local development: `http://localhost:8000`
- API prefix: `/api/v1`
- Interactive OpenAPI: `/docs`
- Health check: `/api/v1/health`
- Authentication: bearer access token in the `Authorization` header.
- Login: `POST /api/v1/identity/login`
- Current user and effective permissions: `GET /api/v1/identity/me`

### Success envelope

```json
{
  "success": true,
  "data": {}
}
```

### Error envelope

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please review the highlighted fields and try again.",
    "details": [],
    "path": "/api/v1/students"
  }
}
```

Error messages are written for school staff, not engineers. Frontends should display `error.message` directly and use `error.code` for programmatic branching.

### Pagination

Paginated endpoints return:

```json
{
  "success": true,
  "data": {
    "items": [],
    "meta": {
      "page": 1,
      "page_size": 20,
      "total": 0,
      "total_pages": 0
    }
  }
}
```

## Authentication and session behavior

- Access tokens expire after **15 minutes**.
- Refresh tokens expire after **7 days**.
- Login returns both tokens. The refresh token is also set as an HTTP-only cookie named `refresh_token`, scoped to `/api/v1/identity`.
- Refreshing rotates the token pair.
- Logout revokes the access and refresh token `jti` values through the Redis-backed revocation store and clears the cookie.
- Always refresh proactively before the access token expires. Do not store refresh tokens in browser-accessible storage.
- A user whose account is deactivated must be treated as signed out.

## What frontend teams must not assume

- There are no parent or student login accounts. Result access is planned through expiring magic links.
- There are no live computation, publication, report-card export, audit-log, or SQLAdmin endpoints yet.
- The backend does not generate or store report-card PDFs. The frontend will render and export PDFs from a verified JSON payload once M5/M6 are live.
- `Form Master` is not a role. It is the teacher assigned to `ClassGroup.class_teacher_id`.
- An `SS1 Coordinator` is not a role. It is a user granted a permission with `scope_type=ACADEMIC_LEVEL`.
- There is no teacher-facing "my class subjects" or "my assignments" discovery endpoint yet.
- MFA flags exist on users, but no MFA enrollment or challenge flow is live.
- School onboarding is currently a public endpoint. Do not expose it in normal staff navigation without an explicit product decision.
- A scoped grant now filters list endpoints (e.g. `GET /structure/class-groups`) down to the caller's department/level/class-group, not an all-or-nothing `403`/everything. The exceptions are the subject catalog (`GET /subjects`) and staff/user management, which have no scope dimension to filter by. See [personas-and-permissions.md](./personas-and-permissions.md) for the full breakdown.
- Bulk imports are asynchronous. A `202 Accepted` response is not a completed import.

## Recommended reading order

1. Read [platform-overview.md](./platform-overview.md) to understand the domain and the current implementation boundary.
2. Read [personas-and-permissions.md](./personas-and-permissions.md) before building navigation, buttons, or route guards.
3. Read [user-journeys.md](./user-journeys.md) before building end-to-end screens.
4. Use `/docs` and the backend tests as the final contract check during implementation.

## Authoritative references

- [ARCHITECTURE.md](../../ARCHITECTURE.md): product architecture, computation rules, security invariants, and target endpoint catalog.
- [BACKEND_DEV_PLAN.md](../../BACKEND_DEV_PLAN.md): milestone scope, implementation status, and frontend handoff expectations.
- [README.md](../../README.md): project overview and local setup.
- [docs/managed-deployment.md](../managed-deployment.md): deployment operations.
