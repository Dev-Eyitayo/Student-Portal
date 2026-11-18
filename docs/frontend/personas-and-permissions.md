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
- **Typical work:** onboard the school, invite staff, configure structure, manage students and subjects, oversee score approval and publication, and complete guided setup including defaults, branding, and optional structure templates. Admins also choose the primary or secondary template at onboarding.
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
- **Typical work:** view rosters for assigned class subjects, enter scores, submit score sheets. Primary subject teachers (M9, LIVE) see only compulsory core subjects.
- **Frontend caveat:** approval, unlock, student management, structure management, and subject management are not default teacher capabilities.
- **Partial limitation:** the backend does not yet expose a teacher-facing "my assignments" discovery endpoint. A teacher can be authorized for a class subject, but the frontend must obtain the class subject and assessment definition ids from another part of the product or wait for a backend addition.

### Form master, class teacher, or homeroom teacher

- **Not a role.**
- A form master / homeroom teacher is the staff user stored in `ClassGroup.class_teacher_id`.
- One teacher can be class teacher for multiple class groups.
- Assignment accepts `TEACHER`, `ACADEMIC_OFFICER`, or `SCHOOL_ADMIN`, and requires `structure:manage`.
- There is no seeded `classes:enter_remarks` permission and no remarks endpoint. For primary reports (M9, LIVE), the homeroom/class teacher is the source of the live `class_teacher_comment` payload field.
- A form master is not automatically granted approval, publication, or student-management permissions. For primary schools (M9, LIVE) use `ACADEMIC_LEVEL` or `CLASS_GROUP` grants instead of `DEPARTMENT` grants.

### Head of department

- **Not a role.**
- Representable as a permission grant scoped to `DEPARTMENT`, for example `scores:approve` for Science, or `structure:manage`/`subjects:manage`/`teachers:assign`/`subjects:register_electives`/`students:enroll` scoped to the HOD's department.
- Scoped enforcement is live for all of those: class groups, subject offerings, class subjects, teacher assignments, student subject registrations, score sheets, and enrollments all resolve the target resource's department/level before checking the grant.
- Does not extend to staff/user management (`users:manage`, `permissions:delegate`) or the subject catalog itself (`subjects:manage` on `/subjects` directly) -- neither has a department dimension in the data model, so those remain `SCHOOL_WIDE`-or-role-default only.

### Academic-level coordinator

- **Not a role.**
- Representable as a permission grant scoped to `ACADEMIC_LEVEL`, for example an SS1 coordinator with level-scoped approval, structure management, or enrollment.
- Same live enforcement as a head of department.
- There is no coordinator field on `AcademicLevel`.

### Pupil / student (primary — M9 LIVE)

- **No login accounts.** A pupil is a `Student` record shown with primary-school UI labels.
- Primary pupils belong to Basic 1–6 levels with `department_id = NULL` and take only compulsory core subjects.
- Result access is planned through the same expiring magic link as secondary students; no pupil portal exists.
- Render pupil labels in primary-school navigation; no separate pupil portal exists.

### Parent, guardian, student, or pupil

- **No login accounts.**
- Result access is planned as an expiring, revocable magic link with an access limit.
- Do not design a parent dashboard or student portal against live endpoints.
- For primary recipients (M9, LIVE), render the report variant with `department: null`, letter grades, and a `class_teacher_comment`.
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
| `scores:approve` | Assessment | Approve scores | Approve score sheet, calculate term, and read broadsheet with resource scope. |
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
| `configuration:manage` | Admin | Manage grading and annual result rules | School configuration read and update routes. |
| `reports:publish` | Admin | Publish term results | `POST /publication/publish-term`. |
| `audit:view` | Admin | View audit logs | No audit read route yet. |

`classes:enter_remarks` appears in friendly error translation but is not a seeded permission and has no route.

M9 (LIVE) adds **no new permission codes**: `configuration:manage`, `structure:manage`, and `subjects:manage` cover primary configuration, levels/arms, and core subject offerings/class subjects. Primary delegation cannot use `DEPARTMENT` scope because primary has no departments; use `ACADEMIC_LEVEL` or `CLASS_GROUP`. For primary schools (`school_type == "PRIMARY"`), display **Pupil** (for student), **Class/Homeroom Teacher** (for form master), and **Head Teacher** (for `principal_signature_url` and `principal_comment`).

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

### Current enforcement

- Every mutation and single-resource read on something with a real department/level/class-group dimension -- class groups, subject offerings, class subjects, teacher assignments, student subject registrations, enrollments, assessment definitions, and score sheets -- resolves that resource's own scope and checks it against the caller's grants. A `DEPARTMENT`- or `ACADEMIC_LEVEL`-scoped grant now reliably works for exactly the resources inside that scope, and is correctly rejected outside it.
- List endpoints for those same resource types (`GET /structure/class-groups`, `GET /subjects/offerings`, `GET /class-subjects`, `GET /assessments/definitions`, `GET /students/{id}/enrollments`) are also scope-filtered: a department-scoped delegate calling one of these gets back only the rows inside their granted department/level/class-group(s), not a `403` and not the whole school's rows. A caller with zero applicable permission (no role default, no grant at all) still gets `403` on the list itself, not an empty `200`.
- The subject catalog list (`GET /subjects`) and staff/user management (`users:manage`, `permissions:delegate`) have no department/level/class-group dimension in the data model, so they remain `SCHOOL_WIDE`-or-role-default only -- there is nothing to filter them by.
- The API does not validate that the supplied id field matches the selected `scope_type` on grant creation. The frontend must send exactly the matching id and leave the others `null`.

## Live route permission reference

| Route family | Permission | Scope-aware |
| --- | --- | --- |
| `POST /identity/schools`, `POST /identity/login`, `POST /identity/refresh` | Public. | n/a |
| `GET /identity/me` | Authenticated user. | n/a |
| `POST /identity/logout` | Authenticated user. | n/a |
| `GET /identity/staff/verify-invite`, `POST /identity/staff/accept-invitation` | Public invitation flow. | n/a |
| `POST /identity/staff/invite`, bulk-import, grant/revoke permissions, deactivate | `users:manage` | No (no scope dimension) |
| `GET /structure/sessions`, `/terms`, `/levels`, `/departments`, `/arms` (all CRUD) | `structure:manage` | No (no scope dimension) |
| `GET /structure/class-groups` (list), `POST`, `GET/PATCH/DELETE /{id}` | `structure:manage` | **Yes** -- by each group's own department/level/id; the list is filtered to the caller's scope, not all-or-nothing |
| `GET/PATCH /students/{student_id}`, `POST /students` | `students:register` | No (no scope dimension) |
| `POST /students/bulk-import`, `GET /students/bulk-import/{task_id}` | `students:bulk_import` | No |
| `POST /students/{student_id}/status` | `students:status` | No |
| `GET /students/{student_id}/enrollments` (list), `POST`, `GET/PATCH /enrollments/{id}` | `students:enroll` | **Yes** -- by the enrollment's class group; list filtered the same way |
| `GET /subjects`, `POST/PATCH/DELETE /subjects/{id}` (catalog) | `subjects:manage` | No (no scope dimension) |
| `GET /subjects/offerings` (list), `POST`, `GET/PATCH/DELETE /{id}` | `subjects:manage` | **Yes** -- by the offering's level/department; list filtered the same way |
| `GET /class-subjects` (list), `POST`, `GET/DELETE /{id}` | `subjects:manage` | **Yes** -- by the class group; list filtered the same way |
| Class-subject teacher assignments (list/create/deactivate/bulk) | `teachers:assign` | **Yes** -- by the class group |
| Bulk and enrollment subject registrations (list/create/deactivate/bulk) | `subjects:register_electives` | **Yes** -- by the class group |
| `GET /assessments/definitions` (list), `POST`, `GET/PATCH /{id}` | `subjects:manage` | **Yes** -- by the class subject's class group; list filtered the same way |
| Score sheet read and bulk entry | `scores:enter` | **Yes** -- teacher ownership, or scoped delegation |
| Score sheet submit | `scores:submit` | **Yes** -- teacher ownership, or scoped delegation |
| Score sheet approve, unlock | `scores:approve` / `scores:unlock` | **Yes** -- scoped delegation (never teacher ownership) |

### M8 route permissions (LIVE)

| Route | Intended authorization |
| --- | --- |
| `GET /identity/onboarding/templates` | Public; no auth. |
| `GET`, `PATCH /identity/schools/{school_id}/setup` | Admin-only (`SCHOOL_ADMIN` / `configuration:manage`); tenant must match the `school_id` in the path. |
| `POST /identity/schools/{school_id}/branding/uploads` | Admin-only; tenant namespace must match the school. |
| `POST /structure/templates/apply` | `structure:manage`. |
| Custom domains | Not implemented; post-pilot/PLANNED. |

Do not gate navigation on these routes until the backend is verified. Once live, a `school_id` belonging to another tenant must be denied (404/403) on setup and branding routes, and branding asset reads must stay inside the school's namespace.

### M9 route permissions (LIVE)

| Route | Authorization |
| --- | --- |
| `GET /identity/onboarding/templates` | Public; returns both secondary and primary templates. |
| `POST /identity/schools` | Platform-admin operation; accepts `school_type` and seeds matching defaults. |
| `GET`, `PATCH /identity/schools/{school_id}/setup` | Admin-only; `school_type` and template choice must stay inside the tenant. |
| `POST /structure/templates/apply` | `structure:manage`; primary template creates levels, arms, and core subject offerings/class subjects. |
| Primary score/computation/publication/results routes | Unchanged live permissions; the primary report variant only changes payload fields. |

Render M9 navigation and actions for primary schools; this table is a live capability.

## Assessment-specific ownership rules

Assessment authorization layers teacher ownership on top of the permission check:

- `SUPER_ADMIN` and `SCHOOL_ADMIN` bypass.
- `ACADEMIC_OFFICER` with the role-default permission proceeds unscoped.
- Score-entry mutation, score-sheet submission, **and score-sheet read** require an actively assigned `TEACHER`, or a scoped/`SCHOOL_WIDE` delegation.
- `scores:approve` and `scores:unlock` are not teacher-ownership permissions. A teacher cannot approve or unlock merely by being assigned to the class subject -- only role-default `ACADEMIC_OFFICER`, `SCHOOL_WIDE`, or a scoped delegation reaches those.

Frontend implications:

- A teacher may have `scores:enter` in `/me` but still receive `403` entering or reading scores for a class subject they do not teach -- this now applies to the read route too, not just bulk-entry.
- Never show every class subject to every teacher; the backend will reject it, but don't surface ids the UI shouldn't expose in the first place.
- Approval and unlock screens should be hidden unless the user has the relevant code and either school-wide/academic-officer authority or a scoped delegation covering that class subject's department, level, or class group.

## Delegation recipes and current suitability

| Scenario | Grant | Suitability |
| --- | --- | --- |
| Teacher enters, reads, and submits scores for assigned subjects | No extra grant; role defaults plus teacher assignment. | **LIVE** |
| Academic officer approves across the school | Role default `scores:approve`. | **LIVE** |
| HOD approves Science only | `scores:approve` with `DEPARTMENT` scope. | **LIVE** |
| SS1 coordinator approves SS1 only | `scores:approve` with `ACADEMIC_LEVEL` scope. | **LIVE** |
| HOD manages structure, subjects, teacher assignments, and enrollment for their department | `structure:manage` / `subjects:manage` / `teachers:assign` / `students:enroll` with `DEPARTMENT` scope. | **LIVE**, including list endpoints: the HOD's class-group/offering/class-subject/definition/enrollment lists are filtered to their department automatically. |
| Form master for SS1 O | Set `ClassGroup.class_teacher_id`. | **LIVE** for broadsheet access; remarks and attendance workflows are planned for M5A. |
| Academic officer calculates a term or reads a scoped broadsheet | Role default `scores:approve`, or a matching scoped grant. | **LIVE** |
| Principal publishes a term | Role default/admin grant with `reports:publish`. | **LIVE** |
| School administrator edits grading and annual rules | `configuration:manage`. | **LIVE** |
| School administrator completes guided setup (defaults, branding, optional template) | Admin-only setup/branding routes; `structure:manage` to apply a template. | **LIVE** (M8) |
| School administrator selects the primary template | Admin-only setup with `school_type = PRIMARY`; `structure:manage` to apply it. | **LIVE** (M9) |
| Primary class/homeroom teacher scoped to one Basic level | `ACADEMIC_LEVEL` or `CLASS_GROUP` grant; no `DEPARTMENT` scope exists for primary. | **LIVE** (M9) |
| Staff manager grants permissions | `users:manage`, not `permissions:delegate`. | **LIVE** |

## Staff deactivation effects

Deactivating a staff user:

- Sets `is_active=false`.
- Sets `token_valid_after`, invalidating existing sessions.
- Marks the staff profile `INACTIVE`.
- Deactivates active teacher assignments.
- Clears the active (non-retired) class groups where the user was class teacher; historical retired class groups keep their past class teacher on record.
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
