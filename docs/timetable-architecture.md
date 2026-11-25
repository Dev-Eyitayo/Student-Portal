# Timetable Scheduling Module — Requirements & Architecture Specification

**Module:** `app/modules/timetable/`  
**API Prefix:** `/api/v1/timetable`  
**Status:** Proposed Architecture & Requirements Specification  
**Parent Spec:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) & [`AGENTS.md`](../AGENTS.md)

---

## 1. Purpose & Domain Context

The Timetable Scheduling module generates, validates, and publishes conflict-free weekly class timetables for Nigerian Primary (`BASIC 1–6`), Junior Secondary (`JSS 1–3`), and Senior Secondary (`SS 1–3`) schools, as well as international/combined institutions.

It builds directly on Olivet's existing academic foundation:
- **`AcademicLevel.category`** (`PRIMARY`, `JUNIOR_SECONDARY`, `SENIOR_SECONDARY`) defines bell-schedule boundaries (with optional per-level overrides).
- **`ClassGroup`** represents each individual class arm (including both departmentalized and mixed senior secondary arms).
- **`ClassSubject`** and **`TeacherAssignment`** define which subjects each arm takes and which active teacher(s) teach each subject-arm pair.
- **`StudentSubjectRegistration`** identifies elective and trade-subject groupings, preventing student-level double-booking in mixed arms.

Every class arm (`ClassGroup`) receives its own weekly timetable, while every teacher receives a unified personal schedule across all levels, categories, and arms they teach.

---

## 2. Core Architectural Invariants

All rules in [`AGENTS.md`](../AGENTS.md) apply to `app/modules/timetable/`:

1. **Multi-Tenancy Defense-in-Depth:** Every timetable table includes `school_id: UUID NOT NULL REFERENCES schools(id)` with PostgreSQL Row-Level Security (`SET LOCAL app.current_school_id = :school_id`) enforced via `get_tenant_db`. Alembic migrations **must** execute `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and create the `app.current_school_id` policy on every new table.
2. **UUIDv7 Primary Keys:** All entities use `generate_uuid7()`.
3. **Zero Hard Deletions:** All mutable entities use `TimestampMixin` (`created_at`, `updated_at`, `deleted_at`) and `ON DELETE RESTRICT` on historical/structural foreign keys (`ON DELETE SET NULL` only for optional actor metadata like `published_by_id`).
4. **Pure Python Solver Engine (`app/modules/timetable/engine.py`):** Zero SQLAlchemy, DB session, Celery, or HTTP imports. Accepts plain dataclasses/dicts and returns deterministic schedules and conflict diagnostics.
5. **Asynchronous Generation via Celery:** Auto-generation for a school (up to 120+ arms) runs on a dedicated `timetable_generation` Celery queue (`202 Accepted`) with job status polling.
6. **Versioned Draft → Published Lifecycle:** At most one `PUBLISHED` timetable exists per `(school_id, academic_term_id)` (enforced via partial unique index). Published timetables are immutable; changes require cloning into a new `DRAFT` version or explicitly unlocking.
7. **Human-Friendly Error Messaging:** Infeasible constraints or manual-edit conflicts must explain *which teacher, class arm, facility, student group, or time window* conflicted and what to adjust—never raw solver codes.

---

## 3. Functional Requirements & Scheduling Constraints

### 3.1 Bell Schedules & Timeslots (Category + Optional Level Override)
- Each `level_category` (`PRIMARY`, `JUNIOR_SECONDARY`, `SENIOR_SECONDARY`) defines its own **Bell Schedule** for an `academic_session_id`.
- Optional `academic_level_id` override (nullable): supports schools where Lower Primary (`Basic 1–3`) closes earlier than Upper Primary (`Basic 4–6`), or `SS 3` has extra WAEC prep periods.
- A Bell Schedule defines ordered slots per `day_of_week` (`1 = Monday` … `5 = Friday`, extensible to `6 = Saturday`):
  - `start_time: time`, `end_time: time` (`end_time > start_time`)
  - `slot_type`: `TEACHING` | `ASSEMBLY` | `SHORT_BREAK` | `LONG_BREAK` | `CLUBS_SPORTS` | `PREP`
  - `label`: e.g., `"Period 1"`, `"Long Break"`, `"Morning Assembly"`
  - Naturally supports variable teaching slots per day (e.g., shorter Friday schedule for Jummah).
- Only `TEACHING` slots are eligible for subject scheduling. Non-teaching slots (`ASSEMBLY`, `BREAK`, etc.) act as hard barriers—a double period **cannot** span across a non-teaching slot.

### 3.2 Cross-Category Teacher Non-Overlap & Co-Teaching
- A single teacher can teach across multiple `level_category` groups (e.g., `JSS 2 Mathematics` and `SS 1 Physics`, or `Primary 6` and `JSS 1`).
- Because categories/levels may have different period durations and break times, **teacher conflict detection MUST compare concrete time intervals, never `slot_id` equality**:
  $$\text{Overlap}(A, B) \iff A.\text{day} = B.\text{day} \;\land\; A.\text{start\_time} < B.\text{end\_time} \;\land\; A.\text{end\_time} > B.\text{start\_time}$$
- **Co-Teaching & Unassigned Drafts (Zero Friction for Normal Single-Teacher Classes):**
  - `TeacherAssignment` already allows `0`, `1`, or `>1` active teachers per `ClassSubject`.
  - For normal single-teacher subjects (`1` active teacher), behavior is 1:1.
  - If a `ClassSubject` has multiple active teachers (e.g., lead teacher + lab demonstrator), **all** active teachers are checked for interval overlap on that slot.
  - If a `ClassSubject` has `0` active teachers, it may remain unassigned in a `DRAFT` timetable (flagged in warnings), but **must** have at least 1 active teacher before `PUBLISH` (unless explicitly waived).
- **Exception:** Entries belonging to the same `JOINT_CLASS` sync group (see §3.5) count as **one** teacher booking.

### 3.3 Subject Frequency & Double Periods
- Schools do not create separate "Practical" subjects (no standalone "Chemistry Practical" subject). Longer blocks are modeled as **Double Periods** (2 consecutive `TEACHING` slots on the same day):
  - `periods_per_week`: total teaching periods per week (e.g., `5` for Mathematics/English, `4` for Chemistry, `2` for Civic Education).
  - `double_periods_per_week`: number of 2-slot consecutive blocks required per week (e.g., `1` double period + `2` single periods = `4` total).
  - `allow_double_period_split`: default `False` (double periods must be adjacent `TEACHING` slots with no `BREAK` in between).
  - `max_periods_per_day`: default `2` when double periods exist, `1` for single-only subjects.
- **Rule Precedence (Most Specific Wins):**
  `(session, academic_level_id, department_id, subject_id)` > `(session, academic_level_id, NULL, subject_id)` > `(session, NULL, NULL, subject_id)`.
  This lets Nigerian SS 1–3 schools give Biology or Economics different weekly periods/lab rules in `Science` vs. `Arts`/`Commercial`.

### 3.4 Opt-In Shared Facilities / Labs (No Classroom Data Entry Required)
- Schools are **not** required to enter every classroom, auditorium, or hall. By default, every `ClassGroup` uses its own home room (`room_id = NULL`) and zero room constraints apply.
- **Optional Shared Facilities (`rooms`):** Schools only register bottleneck facilities they want to protect from double-booking (e.g., *Chemistry Lab*, *ICT/Computer Room*, *Catering Kitchen*, *School Hall*), with optional `capacity`.
- A scheduling rule can optionally select:
  - `preferred_room_id: UUID | None` (defaults to `None`)
  - `room_only_for_double_periods: bool` (default `True` so single Chemistry periods stay in the home classroom while Chemistry's double period books the **Chemistry Lab**).
- When `preferred_room_id` and `room.capacity` are set, pre-solve validation checks `room.capacity >= active_enrollment_count` (using `StudentEnrollment` / `StudentSubjectRegistration`), preventing an oversized `JOINT_CLASS` from being placed into a small lab.

### 3.5 Unified Sync Groups (`JOINT_CLASS` & `PARALLEL_SPLIT`)
Scoped to `academic_session_id` (so schools don't have to recreate them every term), a single `TimetableSyncGroup` handles two Nigerian school workflows:

1. **`JOINT_CLASS` (Combined Arms — Same Teacher(s), Same Facility, Same Slot):**
   - *Use case:* Two or more arms (e.g., `SS 2 Science O` and `SS 2 Science L`) hold Chemistry double period together in the Chemistry Lab with the same teacher.
   - *Solver & Validation Rules:* All member `ClassSubject`s are forced into the exact same `(day_of_week, start_time, end_time)` and count as **1 teacher booking** and **1 room booking**. Pre-solve checks verify matching `(start_time, end_time)` grids and compatible `periods_per_week` / `double_periods_per_week` / `preferred_room_id` across members.
2. **`PARALLEL_SPLIT` (Simultaneous Electives / Trade Subjects — Same Slot, Different Teachers/Rooms):**
   - *Use case:* `CRS` vs. `IRS`, `Yoruba` vs. `Igbo` vs. `Hausa`, or Trade Subjects (`Data Processing`, `Catering Craft`, `Garment Making`, `Marketing`) run simultaneously so students split without missing core classes.
   - *Solver & Validation Rules:* All member `ClassSubject`s are forced into the exact same `(day_of_week, start_time, end_time)`, while each keeps its own teacher and room checks. Pre-solve checks query `student_subject_registrations` (`is_active = True`) to flag any student enrolled in `>1` `ClassSubject` within the same `PARALLEL_SPLIT` group.

### 3.6 Teacher Load & Availability Constraints
- **Availability Windows (`teacher_availabilities`):** Mark specific days or time ranges as `UNAVAILABLE` (hard constraint for part-time/visiting teachers) or `AVOID` (soft penalty).
- **Load & Pacing Caps:**
  - `max_periods_per_day` (default e.g. `6`)
  - `max_periods_per_week` (optional cap)
  - `max_consecutive_periods` (default e.g. `3` or `4`)
  - `min_gap_minutes_between_categories` (default `0`, configurable to e.g. `5–10` mins when crossing from Primary wing to Senior wing; also clarifies whether a short gap counts as consecutive).

### 3.7 Soft Optimization Goals (Quality Scoring)
1. **Morning Preference (`prefer_morning`):** Core subjects (Mathematics, English, Sciences) are rewarded for placement before `LONG_BREAK`.
2. **Even Weekly Spread:** Spreads `periods_per_week` across Monday–Friday and penalizes placing the same single-period subject on consecutive days when avoidable.
3. **Teacher Daily Balance:** Smooths a teacher's workload evenly across the week.
4. **Compact Student Days:** Pushes any unscheduled free/study periods to the end of the day rather than leaving mid-day gaps.

### 3.8 Pinning, Manual Edits & Versioning
- **Atomic Pinning (`is_pinned = True`):** Pinning or unpinning any slot belonging to a `double_period_pair_id` operates atomically on **both** consecutive slots so a half-double is never orphaned.
- **Manual Drag/Swap Validation:** Moving or swapping an entry in a `DRAFT` timetable runs real-time conflict validation (teacher overlap, room overlap, break boundary, sync-group alignment) before saving.
- **Published Versioning:** Cloning a `PUBLISHED` timetable creates `version + 1` in `DRAFT` status; publishing archives the previous `PUBLISHED` version cleanly.

---

## 4. Data Model (`app/modules/timetable/models.py`)

All tables inherit `Base`, `TenantMixin` (`school_id`), and `TimestampMixin` (`created_at`, `updated_at`, `deleted_at`), use `ON DELETE RESTRICT` on structural/historical FKs, and enforce `WHERE deleted_at IS NULL` partial unique indexes:

```
bell_schedules
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── academic_session_id (FK academic_sessions.id, ON DELETE RESTRICT)
  ├── level_category (VARCHAR(32): PRIMARY | JUNIOR_SECONDARY | SENIOR_SECONDARY)
  ├── academic_level_id (FK academic_levels.id, nullable, ON DELETE RESTRICT)
  ├── name (VARCHAR(100))
  └── is_active (BOOLEAN)

timetable_slots
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── bell_schedule_id (FK bell_schedules.id, ON DELETE RESTRICT)
  ├── day_of_week (SMALLINT: CHECK 1..6)
  ├── period_order (SMALLINT: CHECK >= 1)
  ├── label (VARCHAR(64))
  ├── start_time (TIME), end_time (TIME: CHECK end_time > start_time)
  ├── slot_type (VARCHAR(32): TEACHING | ASSEMBLY | SHORT_BREAK | LONG_BREAK | CLUBS_SPORTS | PREP)
  └── UNIQUE (bell_schedule_id, day_of_week, period_order) WHERE deleted_at IS NULL

rooms (Opt-in shared bottleneck facilities only)
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── name (VARCHAR(100)), code (VARCHAR(24))
  ├── room_type (VARCHAR(32): CLASSROOM | SCIENCE_LAB | ICT_LAB | WORKSHOP | HALL)
  ├── capacity (INTEGER, nullable: CHECK capacity > 0)
  └── UNIQUE (school_id, code) WHERE deleted_at IS NULL

subject_scheduling_rules
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── academic_session_id (FK academic_sessions.id, ON DELETE RESTRICT)
  ├── academic_level_id (FK academic_levels.id, nullable, ON DELETE RESTRICT)
  ├── department_id (FK departments.id, nullable, ON DELETE RESTRICT)
  ├── subject_id (FK subjects.id, ON DELETE RESTRICT)
  ├── periods_per_week (SMALLINT: CHECK >= 1)
  ├── double_periods_per_week (SMALLINT, default 0: CHECK double_periods_per_week * 2 <= periods_per_week)
  ├── allow_double_period_split (BOOLEAN, default False)
  ├── max_periods_per_day (SMALLINT, default 2)
  ├── prefer_morning (BOOLEAN, default False)
  ├── preferred_room_id (FK rooms.id, nullable, ON DELETE RESTRICT)
  ├── room_only_for_double_periods (BOOLEAN, default True)
  └── Split partial unique indexes for (academic_level_id, department_id) NULL / NOT NULL combinations

timetable_sync_groups
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── academic_session_id (FK academic_sessions.id, ON DELETE RESTRICT)
  ├── name (VARCHAR(100))
  └── sync_type (VARCHAR(24): JOINT_CLASS | PARALLEL_SPLIT)

timetable_sync_group_items
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── sync_group_id (FK timetable_sync_groups.id, ON DELETE RESTRICT)
  ├── class_subject_id (FK class_subjects.id, ON DELETE RESTRICT)
  └── UNIQUE (sync_group_id, class_subject_id) WHERE deleted_at IS NULL

teacher_availabilities
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── academic_session_id (FK academic_sessions.id, ON DELETE RESTRICT)
  ├── teacher_id (FK users.id, ON DELETE RESTRICT)
  ├── day_of_week (SMALLINT, nullable)
  ├── start_time (TIME, nullable), end_time (TIME, nullable)
  ├── availability_type (VARCHAR(16): UNAVAILABLE | AVOID)
  ├── max_periods_per_day (SMALLINT, nullable)
  ├── max_periods_per_week (SMALLINT, nullable)
  ├── max_consecutive_periods (SMALLINT, nullable)
  └── min_gap_minutes_between_categories (SMALLINT, default 0)

timetables
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── academic_term_id (FK academic_terms.id, ON DELETE RESTRICT)
  ├── parent_timetable_id (FK timetables.id, nullable, ON DELETE SET NULL)
  ├── version (INTEGER, default 1)
  ├── name (VARCHAR(120))
  ├── status (VARCHAR(24): DRAFT | PUBLISHED | ARCHIVED)
  ├── generation_status (VARCHAR(24): IDLE | QUEUED | RUNNING | COMPLETED | FAILED)
  ├── celery_task_id (VARCHAR(128), nullable)
  ├── generation_summary (JSONB, nullable)
  ├── published_at (TIMESTAMPTZ, nullable)
  ├── published_by_id (FK users.id, nullable, ON DELETE SET NULL)
  └── UNIQUE (school_id, academic_term_id) WHERE status = 'PUBLISHED' AND deleted_at IS NULL

timetable_entries
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── timetable_id (FK timetables.id, ON DELETE RESTRICT)
  ├── class_group_id (FK class_groups.id, ON DELETE RESTRICT)
  ├── class_subject_id (FK class_subjects.id, ON DELETE RESTRICT)
  ├── teacher_id (FK users.id, nullable, ON DELETE RESTRICT)      -- primary teacher snapshot
  ├── timetable_slot_id (FK timetable_slots.id, ON DELETE RESTRICT)
  ├── room_id (FK rooms.id, nullable, ON DELETE RESTRICT)
  ├── sync_group_id (FK timetable_sync_groups.id, nullable, ON DELETE RESTRICT)
  ├── is_double_period_part (BOOLEAN, default False)
  ├── double_period_pair_id (UUID, nullable)
  ├── is_pinned (BOOLEAN, default False)
  └── UNIQUE (timetable_id, class_group_id, timetable_slot_id) WHERE deleted_at IS NULL

timetable_entry_teachers (supports co-teaching / >1 active teacher per entry)
  ├── id (UUIDv7 PK), school_id (FK schools.id)
  ├── timetable_entry_id (FK timetable_entries.id, ON DELETE RESTRICT)
  ├── teacher_id (FK users.id, ON DELETE RESTRICT)
  └── UNIQUE (timetable_entry_id, teacher_id) WHERE deleted_at IS NULL
```

---

## 5. Pure Python Solver Engine (`app/modules/timetable/engine.py`)

### 5.1 Solver Choice & Execution Bounds
- **Solver:** Google OR-Tools CP-SAT (`ortools.sat.python.cp_model`) encapsulated purely inside `app/modules/timetable/engine.py` (zero DB/HTTP/Celery imports).
- **Execution Bounds:** Configurable `max_time_in_seconds` (e.g., `15–30s`), deterministic seed/worker count so 120-arm schools never stall workers.

### 5.2 Decision Variables & Hard Constraints
1. **Separate Single vs. Double Variables:**
   - `single[cs, slot]` for single-period placements.
   - `double_start[cs, slot_pair]` for consecutive 2-slot double periods (never crossing non-teaching slots).
   - Occupancy `occ[cs, slot] = single[cs, slot] + sum(double_start[cs, p] where slot in p)`.
   - This cleanly models conditional room booking (`room_only_for_double_periods = True` applies room cliques **only** to `double_start`, not `single`).
2. **Period Counts & Daily Caps:** Exact `periods_per_week`, exact `double_periods_per_week`, and `sum(occ[cs, s] for s on day d) <= max_periods_per_day`.
3. **Class Arm Exclusivity:** `sum(occ[cs, slot] for cs in arm) <= 1`.
4. **Cross-Category Teacher Non-Overlap (Co-Teaching Aware):**
   - Precompute overlapping slot cliques across all `bell_schedules` where `day` matches and `[s1.start_time, s1.end_time)` overlaps `[s2.start_time, s2.end_time)` (plus `min_gap_minutes_between_categories` when crossing categories).
   - For every teacher `t` (across all `teacher_ids` on `ClassSubject`) and overlapping clique, sum of distinct bookings (using a shared interval variable `b[sync_group, day, start, end]` for `JOINT_CLASS` members) `<= 1`.
5. **Room Exclusivity:** Same interval-overlap clique per `room_id`, grouping `JOINT_CLASS` members into 1 booking.
6. **Student Elective Conflict Avoidance:** For mixed arms, `ClassSubject`s sharing active students in `student_subject_registrations` cannot overlap unless intentionally grouped (and `PARALLEL_SPLIT` pre-checks reject overlapping student registrations inside the same group).
7. **Atomic Pinned Entries:** Pinned `double_period_pair_id` pairs fix `double_start[cs, pair] == 1` atomically.

### 5.3 Pre-Solve Infeasibility Diagnostics
Before running CP-SAT, `engine.py` validates and returns human-friendly diagnostics for:
- Class arm subject periods exceeding available `TEACHING` slots in its schedule.
- Contradictory rules (`double_periods_per_week * 2 > periods_per_week`, or `double_periods_per_week > 0` with `max_periods_per_day < 2`).
- Teacher total weekly periods exceeding available non-overlapping slots (`UNAVAILABLE` windows + `max_periods_per_week`).
- Shared facility bottleneck (`total lab-required periods > available lab slots`) or `room.capacity < active_enrollment_count`.
- `JOINT_CLASS` mismatches (incompatible bell grids or differing period/room rules) and `PARALLEL_SPLIT` student registration overlaps.

---

## 6. API Contract (`/api/v1/timetable`)

*(Note: When implementing, add `timetable` to the approved domain list in `AGENTS.md` §5.2 and `ARCHITECTURE.md` §6.2.)*

All routes use `get_tenant_db`, `require_permission("timetable:manage")` (or `"timetable:publish"` for publishing), `SuccessResponse[T]`, and `Page[T]`:

| Area | Method & Path | Permission | Purpose |
| :--- | :--- | :--- | :--- |
| **Bell Schedules** | `GET / POST /api/v1/timetable/bell-schedules` | `timetable:manage` | List or create bell schedules |
| | `GET / PATCH / DELETE /api/v1/timetable/bell-schedules/{schedule_id}` | `timetable:manage` | View, update, or soft-delete schedule |
| | `PUT /api/v1/timetable/bell-schedules/{schedule_id}/slots` | `timetable:manage` | Bulk replace teaching/break slots |
| **Shared Facilities** | `GET / POST /api/v1/timetable/rooms` | `timetable:manage` | Opt-in list/create shared labs & halls |
| | `PATCH / DELETE /api/v1/timetable/rooms/{room_id}` | `timetable:manage` | Update or soft-delete facility |
| **Rules & Sync** | `GET / PUT /api/v1/timetable/rules` | `timetable:manage` | List or upsert scheduling rules |
| | `DELETE /api/v1/timetable/rules/{rule_id}` | `timetable:manage` | Remove level/department override |
| | `GET / POST /api/v1/timetable/sync-groups` | `timetable:manage` | List or create `JOINT_CLASS` / `PARALLEL_SPLIT` |
| | `PATCH / DELETE /api/v1/timetable/sync-groups/{sync_group_id}` | `timetable:manage` | Edit members or soft-delete sync group |
| | `GET / PUT /api/v1/timetable/teacher-availabilities` | `teachers:assign` | List or upsert teacher constraints |
| | `DELETE /api/v1/timetable/teacher-availabilities/{availability_id}` | `teachers:assign` | Remove teacher availability rule |
| **Timetables** | `GET / POST /api/v1/timetable/schedules` | `timetable:manage` | List or create a term timetable draft |
| | `GET / DELETE /api/v1/timetable/schedules/{timetable_id}` | `timetable:manage` | Inspect or soft-delete draft timetable |
| | `POST /api/v1/timetable/schedules/{timetable_id}/generate` | `timetable:manage` | Dispatch Celery solver job (`202 Accepted`) |
| | `GET /api/v1/timetable/schedules/{timetable_id}/jobs/{task_id}` | `timetable:manage` | Poll solver progress & diagnostics |
| | `POST /api/v1/timetable/schedules/{timetable_id}/entries/{entry_id}/validate` | `timetable:manage` | Real-time conflict check before move |
| | `PATCH / DELETE /api/v1/timetable/schedules/{timetable_id}/entries/{entry_id}` | `timetable:manage` | Move, reassign facility, pin, or unplace |
| | `POST /api/v1/timetable/schedules/{timetable_id}/publish` | `timetable:publish` | Publish timetable for the term |
| | `POST /api/v1/timetable/schedules/{timetable_id}/clone` | `timetable:manage` | Clone published timetable to new draft |
| **Views** | `GET /api/v1/timetable/schedules/{timetable_id}/class-groups/{class_group_id}` | Authenticated | Class arm weekly timetable grid |
| | `GET /api/v1/timetable/schedules/{timetable_id}/teachers/me` | Authenticated | Teacher's cross-level weekly schedule |
| | `GET /api/v1/timetable/schedules/{timetable_id}/master` | `timetable:manage` | School-wide master grid & utilization |
| **NL Assistant** | `POST /api/v1/timetable/assistant/parse` | `timetable:manage` | Parse natural language into previewable constraint patch (no direct DB write) |

---

## 7. Zero-Cost Natural Language Constraint Assistant (Optional Enhancement)

To let school admins type constraints in plain English (e.g., *"Mr. Ade shouldn't teach on Friday"*, *"Put Maths and English in the morning"*, *"SS 2 Science O and L should do Chemistry practical together"*) **without incurring recurring AI API costs**:

1. **Two-Step Safe Workflow (Never Auto-Mutates Blindly):**
   - Step 1: `POST /api/v1/timetable/assistant/parse` translates the user's text into a **structured Pydantic action preview** (`ConstraintPatchPreview`: e.g., `CreateTeacherAvailability` or `CreateSyncGroup` or `UpdateSchedulingRule`), resolving staff, subject, and arm names against the school's DB.
   - Step 2: The UI displays a clear confirmation card (*"I understood: Mark Adewale as Unavailable on Fridays — Apply?"*) and only writes to the DB when the admin clicks **Confirm**, or explains clearly if a name/constraint is ambiguous or impossible.
2. **$0 / Free-Tier Implementation Strategy:**
   - **Tier 1 (100% Free, Offline, <5ms):** A deterministic Python rule/intent parser using normalized regex + fuzzy entity matching against the school's staff, subjects, and class arms. Covers ~85–90% of common timetable requests (*unavailable day/time*, *morning preference*, *max periods/day*, *join two arms*, *sync electives*).
   - **Tier 2 (Optional Free-Tier LLM Fallback):** For complex multi-clause phrasing, optionally call a **free-tier** structured-output LLM endpoint (e.g. Google Gemini API free tier or Groq free tier) *strictly* to emit the JSON `ConstraintPatchPreview` schema—never inside the CP-SAT solver loop. If rate-limited or offline (`AI_PROVIDER=none`), it falls back seamlessly to Tier 1.

---

## 8. Frontend UX, Drag-and-Drop & A3 Client-Side Export

### 8.1 Workspace Layout (`/timetable`)
- **Top Action Bar:** Term selector, Draft/Published status badge, **Generate Timetable** (with Celery progress indicator), **Publish / Clone Draft**, **Maximize View** (fullscreen toggle), and **Export** (`A3 Landscape PDF`, `A4 Class/Teacher PDF`, `A3/A4 DOCX`).
- **Underline Tabs:**
  1. **Timetable Grid (Main Workspace):**
     - **View Modes:** *By Class Arm* (Mon–Fri grid for one arm), *Master School Grid* (all arms in a level/category side-by-side—best used in **Maximize Mode**), and *By Teacher* (unified cross-category schedule showing Junior + Senior periods together).
     - **Break & Assembly Bands:** `SHORT_BREAK`, `LONG_BREAK`, and `ASSEMBLY` slots render as distinct shaded divider bands across the grid so double periods never visually or logically cross a break.
     - **Double Periods & Pinning:** Consecutive double-period slots render as a merged block with a subtle `2×` badge and a **Pin/Lock** toggle (`is_pinned`) that locks/unlocks both halves atomically.
     - **Drag-and-Drop + Click-to-Swap:** Admins can drag any subject block (or click source → target on touch/mobile) onto another `TEACHING` slot. Dropping calls `POST .../entries/{entry_id}/validate` before committing and highlights valid vs. conflicting targets in real time (e.g., showing which teacher or lab is already booked at that time across any category).
  2. **Bell Schedules & Breaks:**
     - Visual timeline editor per `level_category` (and optional `academic_level_id` override) where admins define `Teaching Period`, `Assembly`, `Short Break`, and `Long Break` blocks with start/end times, plus one-click **"Copy to Mon–Thu"** and **"Shorter Friday"** presets.
  3. **Rules, Sync Groups & Facilities:**
     - **Subject Rules Table:** Configure `periods_per_week`, `double_periods_per_week`, `prefer_morning`, and optional `Shared Facility` (`None (Home Class)` by default).
     - **Sync Groups:** Link `JOINT_CLASS` (combined practical arms) or `PARALLEL_SPLIT` (`CRS`/`IRS`, Trade Subjects).
     - **Teacher Availability:** Quick day/time grid for part-time constraints and daily max period caps.
     - **Shared Facilities (Opt-In):** Manage only bottleneck labs/halls (no regular classroom entry required).

### 8.2 Maximize (Fullscreen) Mode
- Clicking **Maximize** expands the timetable workspace into a distraction-free fullscreen overlay (`fixed inset-0 z-50 bg-white` + optional browser Fullscreen API) hiding the sidebar and page chrome, with sticky day/period headers and horizontal/vertical scrolling for dense 20–30+ arm Master Grids.

### 8.3 Client-Side A3 PDF & DOCX Export (`AGENTS.md` §2 #8)
Consistent with Olivet's client-side document assembly model (zero server-side PDF/DOCX rendering):
- **A3 Landscape PDF (`@page { size: A3 landscape; margin: 10mm; }`):** Dedicated print/PDF layout (`TimetablePrintDocument.tsx`) fitting an entire level/category's **Master School Timetable** onto wide A3 landscape sheets with crisp borders, break bands, and school header/logo.
- **A4 Individual Sheets:** Single-page A4 print/PDF views for individual Class Arms and individual Teachers.
- **Client-Side DOCX Export:** Pure-browser `.docx` generation (e.g., via `docx` npm package) configured with A3 landscape page dimensions (`width: 420mm, height: 297mm` for Master Grid, or A4 for single class/teacher) so admins can download an editable Word document directly in the browser.

---

## 9. Phased Rollout Plan

1. **Phase 1 — Core Scheduling & Cross-Category Solver:**
   - `bell_schedules`, `timetable_slots`, `subject_scheduling_rules`, `timetables`, `timetable_entries`, `timetable_entry_teachers`.
   - CP-SAT solver in `engine.py` handling double periods, break barriers, co-teaching, and cross-category teacher interval non-overlap.
   - Celery async generation + Class Arm & Teacher "My Timetable" views + atomic pin/swap + publish/clone.
2. **Phase 2 — Opt-In Shared Facilities, Joint/Split Sync Groups & NL Assistant:**
   - `rooms`, `timetable_sync_groups` (`JOINT_CLASS` + `PARALLEL_SPLIT`), `teacher_availabilities`.
   - Enrollment-aware room capacity checks, student elective overlap checks, and the $0 Natural Language Constraint Assistant (`assistant/parse`).
3. **Phase 3 — Print/PDF Export & Daily Substitutions:**
   - Client-side printable Class & Teacher timetable sheets (matching Olivet's client-side PDF pattern) and optional daily teacher absence cover/substitution tracking.
