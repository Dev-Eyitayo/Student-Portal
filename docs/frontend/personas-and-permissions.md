# Personas and Permissions

## Purpose

This document explains who uses Olivet, which system roles exist, what each permission means, and how the frontend should decide what a user can see and do.

Authorization is a backend responsibility. The frontend uses permissions to shape navigation, hide impossible actions, and explain access limits. It must still handle `403` responses gracefully because backend rules can be stricter than a visible button.

## Personas

### Platform operator

- **System role:** `SUPER_ADMIN`
- **Live behavior:** bypasses permission checks.
- **Meaning:** platform-level administration.
- **Frontend caveat:** the current application has no separate platform-admin workspace, no cross-school switcher, and no admin dashboard. Do not build a school selector from this role.

### School administrator or principal

- **System role:** `SCHOOL_ADMIN`
- **Live behavior:** bypasses permission checks within the school.
- **Typical work:** onboard the school, invite staff, configure structure, manage students and subjects, oversee score approval and publication.
- **Frontend caveat:** `SCHOOL_ADMIN` can be assigned as a class teacher. The role bypass does not replace the class-teacher relationship.

### Academic officer

- **System role:** `ACADEMIC_OFFICER`
- **Live role defaults:** score entry, modification, submission, approval, unlock; student registration, bulk import, enrollment, status changes; elective registration; structure management; subject management; teacher assignment; staff management.
- **Not granted by default:** `permissions:delegate`, `reports:publish`, `audit:view`.
- **Typical work:** day-to-day academic administration across the school.
- **Frontend caveat:** `ACADEMIC_OFFICER` can manage staff and grant permissions through `users:manage`, even though `permissions:delegate` is not one of its defaults.

### Teacher or subject teacher

- **System role:** `TEACHER`
- **Live role defaults:** `scores:enter`, `scores:modify`, `scores:submit`.
- **Typical work:** view rosters for assigned class subjects, enter scores, submit score sheets.
- **Frontend caveat:** approval, unlock, student management, structure management, and subject management are not default teacher capabilities.
- **Partial limitation:** the backend does not yet expose a teacher-facing "my assignments" discovery endpoint. A teacher can be authorized for a class subject, but the frontend must obtain the class subject and assessment definition ids from another part of the product or wait for a backend addition.

### Form master or class teacher

- **Not a role.**
- A form master is the staff user stored in `ClassGroup.class_teacher_id`.
- One teacher can be class teacher for multiple class groups.
- Assignment accepts `TEACHER`, `ACADEMIC_OFFICER`, or `SCHOOL_ADMIN`, and requires `structure:manage`.
- Form-master responsibilities such as conduct or comments are not live. There is no seeded `classes:enter_remarks` permission and no remarks endpoint.
- A form master is not automatically granted approval, publication, or student-management permissions.

### Head of department

- **Not a role.**
- Representable as a permission grant scoped to `DEPARTMENT`, for example `scores:approve` for Science.
- The grant model supports this persona, but scoped enforcement is only wired into assessment service checks. Most route-level guards are unscoped.
- Do not present department-scoped delegation as fully effective across the product until the backend applies scope extractors consistently.

### Academic-level coordinator

- **Not a role.**
- Representable as a permission grant scoped to `ACADEMIC_LEVEL`, for example an SS1 coordinator with level-scoped approval.
- The same enforcement limitation as a head of department applies.
- There is no coordinator field on `AcademicLevel`.

### Parent, guardian, or student

- **No login accounts.**
- Result access is planned as an expiring, revocable magic link with an access limit.
- Do not design a parent dashboard or student portal against live endpoints.
- The planned public verification surface is described as `PLANNED` in [user-journeys.md](./user-journeys.md).

## System roles and default permissions

Only four system roles exist:

| Role | Default permission behavior |
| --- | --- |
| `SUPER_ADMIN` | Bypass; effective permission response returns every seeded permission code. |
| `SCHOOL_ADMIN` | Bypass within the school; effective permission response returns every seeded permission code. |
| `ACADEMIC_OFFICER` | Seed defaults below, plus any direct grants. |
| `TEACHER` | Seed defaults below, plus any direct grants. |

### Default matrix

| Permission | Teacher | Academic officer |
| --- | --- | --- |
| `scores:enter` | Yes | Yes |
| `scores:modify` | Yes | Yes |
| `scores:submit` | Yes | Yes |
| `scores:approve` | No | Yes |
| `scores:unlock` | No | Yes |
| `students:register` | No | Yes |
| `students:bulk_import` | No | Yes |
| `students:enroll` | No | Yes |
| `students:status` | No | Yes |
| `subjects:register_electives` | No | Yes |
| `structure:manage` | No | Yes |
| `subjects:manage` | No | Yes |
| `teachers:assign` | No | Yes |
| `users:manage` | No | Yes |
| `permissions:delegate` | No | No |
| `reports:publish` | No | No |
| `audit:view` | No | No |

`SCHOOL_ADMIN` and `SUPER_ADMIN` bypass all checks, so they effectively have every live permission.

## Permission catalog

| Code | Category | Friendly meaning | Live route usage |
| --- | --- | --- | --- |
| `scores:enter` | Assessment | Enter or edit scores | Read score sheet and bulk score entry. |
| `scores:modify` | Assessment | Modify unsubmitted scores | No route currently checks this code directly. |
| `scores:submit` | Assessment | Submit scores for review | Submit score sheet. |
| `scores:approve` | Assessment | Approve scores | Approve score sheet. |
| `scores:unlock` | Assessment | Unlock a locked score sheet | Unlock score sheet. |
| `students:register` | Student | Register students | List, create, get, and update students. |
| `students:bulk_import` | Student | Bulk import students | Start and poll student bulk import. |
| `students:enroll` | Student | Enroll students | List, create, get, and update enrollments. |
| `students:status` | Student | Update student status | Change student lifecycle status. |
| `subjects:register_electives` | Student | Register elective or trade subjects | List, create, and deactivate subject registrations. |
| `structure:manage` | Structure | Manage sessions, terms, levels, departments, arms, class groups | All `/structure` routes. |
| `subjects:manage` | Structure | Manage subjects, offerings, class subjects, assessment definitions | `/subjects` and class-subject management; assessment definition routes. |
| `teachers:assign` | Structure | Assign teachers to class subjects | Teacher-assignment list, create, deactivate, and bulk assignment. |
| `users:manage` | Admin | Manage staff accounts | Invite, bulk import staff, poll staff import, grant/revoke permissions, deactivate users. |
| `permissions:delegate` | Admin | Delegate permissions to staff | No route currently checks this code. Permission grants use `users:manage`. |
| `reports:publish` | Admin | Publish term results | No live publication route yet. |
| `audit:view` | Admin | View audit logs | No audit read route yet. |

`classes:enter_remarks` appears in friendly error translation but is not a seeded permission and has no route.

## Effective permissions in the frontend

`GET /api/v1/identity/me` returns the user's profile and a flat list of effective permission codes:

```json
{
  "success": true,
  "data": {
    "id": "01912345-...",
    "school_id": "01912345-...",
    "school_name": "Olivet Baptist High School",
    "email": "teacher@example.com",
    "first_name": "Adebayo",
    "last_name": "Okafor",
    "phone_number": "+234...",
    "role": "TEACHER",
    "is_active": true,
    "is_mfa_enabled": false,
    "staff_profile": {
      "id": "01912345-...",
      "staff_id_number": "OLV-001",
      "employment_status": "ACTIVE",
      "specialization": "Chemistry",
      "signature_image_url": null
    },
    "permissions": ["scores:enter", "scores:modify", "scores:submit"]
  }
}
```

Important limitations:

- The response contains permission codes only, not the scope dimensions of grants.
- There is no live endpoint listing a user's existing permission grants.
- A code in `permissions` means the code is present through a role default or direct grant; it does not mean every guarded route is reachable, and it does not describe whether a grant is school-wide, departmental, level-specific, or class-group-specific.
- Do not infer scope from the role name. A teacher may hold extra grants.
- Refresh `/me` after login, after a permission change, after an invitation is accepted elsewhere, and when returning to the application after a long absence.

### UI gating pattern

```text
can(permission) = me.permissions.includes(permission)
```

Use this to hide clearly impossible actions and to show helpful empty states. When the backend gives the frontend enough information to know a user's resource scope, respect it; today that is generally not available from `/me`. If the backend returns `403 INSUFFICIENT_PERMISSIONS`, show `error.message` rather than a generic denial.

## Scoped permission grants

### Request shape

`POST /api/v1/identity/users/{user_id}/permissions`

```json
{
  "permission_code": "scores:approve",
  "scope_type": "DEPARTMENT",
  "department_id": "01912345-...",
  "academic_level_id": null,
  "class_group_id": null
}
```

Supported scope types:

| Scope | Required id field | Intended meaning |
| --- | --- | --- |
| `SCHOOL_WIDE` | None | Applies across the school. |
| `DEPARTMENT` | `department_id` | Applies to a senior-secondary department. |
| `ACADEMIC_LEVEL` | `academic_level_id` | Applies to a level such as SS1. |
| `CLASS_GROUP` | `class_group_id` | Applies to one class group. |

### Grant response

```json
{
  "success": true,
  "data": {
    "id": "01912345-...",
    "permission_id": "01912345-...",
    "permission_code": "scores:approve",
    "scope_type": "DEPARTMENT",
    "department_id": "01912345-...",
    "academic_level_id": null,
    "class_group_id": null,
    "created_at": "2026-11-17T09:00:00Z"
  }
}
```

### Revocation

`DELETE /api/v1/identity/users/{user_id}/permissions/{grant_id}` soft-deletes the grant. Revocation requires `users:manage`.

### Current enforcement limits

- Route-level checks call `require_permission` without scope extractors. This means a role default or a `SCHOOL_WIDE` grant is the reliable way to pass a route guard.
- A scoped grant is evaluated against resource scope only in assessment service authorization after the route guard has passed.
- Because of that, a non-admin user with only a `DEPARTMENT`, `ACADEMIC_LEVEL`, or `CLASS_GROUP` grant may not reach an action whose route guard is unscoped, even though `/me` lists the permission code.
- The API does not currently validate that the supplied id field matches the selected `scope_type`. The frontend must send exactly the matching id and leave the others `null`.
- Treat department, level, and class-group delegation as **PARTIAL** until route-level scoping is completed.

## Live route permission reference

| Route family | Permission |
| --- | --- |
| `POST /identity/schools`, `POST /identity/login`, `POST /identity/refresh` | Public. |
| `GET /identity/me` | Authenticated user. |
| `POST /identity/logout` | Authenticated user. |
| `GET /identity/staff/verify-invite`, `POST /identity/staff/accept-invitation` | Public invitation flow. |
| `POST /identity/staff/invite` | `users:manage` |
| `POST /identity/staff/bulk-import` | `users:manage` |
| `GET /identity/staff/bulk-import/{task_id}` | `users:manage` |
| `POST /identity/users/{user_id}/permissions` | `users:manage` |
| `DELETE /identity/users/{user_id}/permissions/{grant_id}` | `users:manage` |
| `POST /identity/users/{user_id}/deactivate` | `users:manage` |
| All `/structure/*` routes | `structure:manage` |
| `GET/POST /students`, `GET/PATCH /students/{student_id}` | `students:register` |
| `POST /students/bulk-import`, `GET /students/bulk-import/{task_id}` | `students:bulk_import` |
| `POST /students/{student_id}/status` | `students:status` |
| `GET/POST /students/{student_id}/enrollments` | `students:enroll` |
| `GET/PATCH /enrollments/{enrollment_id}` | `students:enroll` |
| All base `/subjects` and `/subjects/offerings` routes | `subjects:manage` |
| Class-subject list/create/get/retire | `subjects:manage` |
| Class-subject teacher assignments | `teachers:assign` |
| Bulk subject registration | `subjects:register_electives` |
| Enrollment subject registrations | `subjects:register_electives` |
| Assessment definition list/create/get/update | `subjects:manage` |
| Score sheet read and bulk entry | `scores:enter` |
| Score sheet submit | `scores:submit` |
| Score sheet approve | `scores:approve` |
| Score sheet unlock | `scores:unlock` |

All `/structure` routes require `structure:manage`, including read-only list and detail routes.

## Assessment-specific ownership rules

Assessment authorization is stricter than a permission check alone:

- `SUPER_ADMIN` and `SCHOOL_ADMIN` bypass.
- `ACADEMIC_OFFICER` with the required permission proceeds.
- Score-entry mutation and score-sheet submission require an actively assigned `TEACHER`.
- Score-sheet read (`GET /assessments/scores`) currently checks only the `scores:enter` route permission and does not enforce teacher assignment in the service layer. This is a known backend gap that must be fixed before production.
- Other lifecycle actions fall back to scoped permission evaluation using the class group's department, academic level, and class-group id.
- `scores:approve` and `scores:unlock` are not teacher-ownership permissions. A teacher cannot approve or unlock merely by being assigned.

Frontend implications:

- A teacher may have `scores:enter` in `/me` but still receive `403` when entering scores for a class subject they do not teach.
- The read route is weaker than the write route today. Do not expose arbitrary class-subject and assessment-definition ids, and do not rely on the frontend alone to prevent cross-class roster reads.
- Never show every class subject to every teacher.
- Approval and unlock screens should be hidden unless the user has the relevant code and is known to have school-wide or academic-officer authority.

## Delegation recipes and current suitability

| Scenario | Grant | Suitability |
| --- | --- | --- |
| Teacher enters and submits scores for assigned subjects | No extra grant; role defaults plus teacher assignment. | **LIVE** |
| Academic officer approves across the school | Role default `scores:approve`. | **LIVE** |
| HOD approves Science only | `scores:approve` with `DEPARTMENT` scope. | **PARTIAL** because the route guard is unscoped. |
| SS1 coordinator approves SS1 only | `scores:approve` with `ACADEMIC_LEVEL` scope. | **PARTIAL** for the same reason. |
| Form master for SS1 O | Set `ClassGroup.class_teacher_id`. | **LIVE** as an assignment, but no remarks workflow. |
| Staff manager grants permissions | `users:manage`, not `permissions:delegate`. | **LIVE** |

## Staff deactivation effects

Deactivating a staff user:

- Sets `is_active=false`.
- Sets `token_valid_after`, invalidating existing sessions.
- Marks the staff profile `INACTIVE`.
- Deactivates active teacher assignments.
- Clears class groups where the user was class teacher.
- Does not delete the user or their historical records.

The frontend should remove the user from active staff lists, clear any cached permissions, and refresh class-group data after deactivation.

## Error and edge-case behavior

- Missing permission returns `403` with code `INSUFFICIENT_PERMISSIONS` and a human-friendly message.
- Unknown user or grant returns `404`.
- Duplicate grant with the same permission and exact scope returns `409`.
- A non-school super admin attempting school-scoped actions receives a school-context error.
- Deactivated users cannot authenticate.
- Cross-tenant resources should look missing, not forbidden.

Display `error.message`. Use `error.code` only for deterministic branching such as showing a re-authentication prompt.
