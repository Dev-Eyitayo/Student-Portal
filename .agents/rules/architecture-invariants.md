---
description: Non-negotiable architectural rules for the Olivet Academic Records Platform
always_on: true
---

# Architecture Invariants for Olivet Academic Records Platform

Every agent working in this repository must strictly adhere to the following invariants:

1. **Multi-Tenancy Defense-in-Depth:**
   - Every tenant table has `school_id: UUID NOT NULL REFERENCES schools(id)`.
   - Never query tenant data without the active `get_tenant_db` dependency which enforces `SET LOCAL app.current_school_id = :school_id`.
   - Never expose auto-increment integer IDs. Use time-ordered UUIDv7.

2. **Immutable Academic Snapshots:**
   - Published results are relationally frozen.
   - `term_report_cards` and `subject_results` store computed grades and ranks alongside a `frozen_config_snapshot` JSON blob.
   - Never recalculate published historical report cards on read.

3. **Hybrid Access Control (RBAC + Scoped ABAC):**
   - Teachers can be granted administrative permissions (e.g. Form Master, HOD, Level Coordinator) via `user_permission_grants`.
   - Use `require_permission(permission_code, scope_extractor)` for authorization checks.

4. **Trade Subjects & Electives:**
   - Senior secondary trade subjects are registered individually per student via `student_subject_registrations`.
   - Score sheets for trade subjects must filter exclusively by registered students.
   - Total obtainable marks on report cards must be computed dynamically from active registrations.

5. **Pure Computation Engine:**
   - `app/modules/computation/engine.py` must contain pure calculation functions with zero database, ORM, or HTTP imports.
   - Use `Decimal` with `ROUND_HALF_UP` for all mark computations.
