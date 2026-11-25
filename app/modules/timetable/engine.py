"""Pure Python Timetable Constraint Solver Engine.

Zero SQLAlchemy, database, Celery, or HTTP imports.
Operates strictly on plain dataclasses, lists, sets, and time intervals.
"""

from __future__ import annotations

import random
import time as time_module
from dataclasses import dataclass, field
from datetime import time
from uuid import UUID, uuid4


@dataclass(frozen=True)
class SlotSpec:
    id: UUID
    bell_schedule_id: UUID
    day_of_week: int
    period_order: int
    label: str
    start_time: time
    end_time: time
    slot_type: str  # TEACHING | ASSEMBLY | SHORT_BREAK | LONG_BREAK | CLUBS_SPORTS | PREP


@dataclass(frozen=True)
class ClassSubjectSpec:
    class_subject_id: UUID
    class_group_id: UUID
    class_group_name: str
    level_category: str
    bell_schedule_id: UUID
    subject_id: UUID
    subject_code: str
    subject_name: str
    teacher_ids: tuple[UUID, ...]
    periods_per_week: int
    double_periods_per_week: int = 0
    allow_double_period_split: bool = False
    max_periods_per_day: int = 2
    prefer_morning: bool = False
    preferred_room_id: UUID | None = None
    room_only_for_double_periods: bool = True
    enrollment_count: int = 0


@dataclass(frozen=True)
class RoomSpec:
    id: UUID
    name: str
    code: str
    capacity: int | None = None


@dataclass(frozen=True)
class SyncGroupSpec:
    id: UUID
    name: str
    sync_type: str  # JOINT_CLASS | PARALLEL_SPLIT
    class_subject_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class TeacherConstraintSpec:
    teacher_id: UUID
    teacher_name: str
    unavailable_windows: tuple[tuple[int | None, time | None, time | None], ...] = ()
    avoid_windows: tuple[tuple[int | None, time | None, time | None], ...] = ()
    max_periods_per_day: int | None = None
    max_periods_per_week: int | None = None
    max_consecutive_periods: int | None = None
    min_gap_minutes_between_categories: int = 0


@dataclass(frozen=True)
class PinnedEntrySpec:
    class_group_id: UUID
    class_subject_id: UUID
    timetable_slot_id: UUID
    room_id: UUID | None = None
    double_period_pair_id: UUID | None = None


@dataclass
class SolvedEntry:
    class_group_id: UUID
    class_subject_id: UUID
    teacher_ids: tuple[UUID, ...]
    timetable_slot_id: UUID
    room_id: UUID | None
    sync_group_id: UUID | None
    is_double_period_part: bool
    double_period_pair_id: UUID | None
    is_pinned: bool


@dataclass
class SolverConflictDiagnostic:
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class TimetableSolveResult:
    success: bool
    entries: list[SolvedEntry] = field(default_factory=list)
    diagnostics: list[SolverConflictDiagnostic] = field(default_factory=list)
    unplaced_count: int = 0
    soft_score: int = 0


def intervals_overlap(
    day_a: int,
    start_a: time,
    end_a: time,
    day_b: int,
    start_b: time,
    end_b: time,
    *,
    gap_minutes: int = 0,
) -> bool:
    if day_a != day_b:
        return False
    a_start = start_a.hour * 60 + start_a.minute
    a_end = end_a.hour * 60 + end_a.minute
    b_start = start_b.hour * 60 + start_b.minute
    b_end = end_b.hour * 60 + end_b.minute
    return a_start < (b_end + gap_minutes) and (a_end + gap_minutes) > b_start


def _slot_midpoint_minutes(slot: SlotSpec) -> int:
    return (
        (slot.start_time.hour * 60 + slot.start_time.minute)
        + (slot.end_time.hour * 60 + slot.end_time.minute)
    ) // 2


def _teacher_unavailable(
    slot: SlotSpec,
    constraint: TeacherConstraintSpec | None,
    *,
    include_avoid: bool = False,
) -> bool:
    if constraint is None:
        return False
    windows = constraint.unavailable_windows + (constraint.avoid_windows if include_avoid else ())
    for w_day, w_start, w_end in windows:
        if w_day is not None and w_day != slot.day_of_week:
            continue
        if w_start is None or w_end is None:
            return True
        if intervals_overlap(
            slot.day_of_week,
            slot.start_time,
            slot.end_time,
            slot.day_of_week,
            w_start,
            w_end,
        ):
            return True
    return False


def validate_manual_move(
    *,
    target_slot: SlotSpec,
    moving_cs: ClassSubjectSpec,
    existing_entries: list[SolvedEntry],
    slots_by_id: dict[UUID, SlotSpec],
    cs_by_id: dict[UUID, ClassSubjectSpec],
    teacher_constraints: dict[UUID, TeacherConstraintSpec],
    sync_groups: list[SyncGroupSpec],
    target_second_slot: SlotSpec | None = None,
    moving_room_id: UUID | None = None,
) -> list[SolverConflictDiagnostic]:
    """Validate whether placing moving_cs into target_slot (and optional target_second_slot) causes conflicts."""
    diagnostics: list[SolverConflictDiagnostic] = []
    target_slots = [target_slot] + ([target_second_slot] if target_second_slot else [])

    for slot in target_slots:
        if slot.slot_type != "TEACHING":
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="NON_TEACHING_SLOT",
                    message=f"'{slot.label}' is a {slot.slot_type.replace('_', ' ').lower()} slot and cannot hold a class.",
                )
            )
        if slot.bell_schedule_id != moving_cs.bell_schedule_id:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="WRONG_BELL_SCHEDULE",
                    message="This slot belongs to a different level category bell schedule.",
                )
            )

    if target_second_slot is not None:
        if target_slot.day_of_week != target_second_slot.day_of_week:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="DOUBLE_PERIOD_SPLIT_DAY",
                    message="A double period must occupy two consecutive slots on the same day.",
                )
            )
        elif target_second_slot.period_order != target_slot.period_order + 1:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="DOUBLE_PERIOD_NOT_ADJACENT",
                    message="A double period must occupy two consecutive teaching periods.",
                )
            )

    joint_group_ids: set[UUID] = {
        sg.id
        for sg in sync_groups
        if sg.sync_type == "JOINT_CLASS" and moving_cs.class_subject_id in sg.class_subject_ids
    }

    for slot in target_slots:
        for tid in moving_cs.teacher_ids:
            tc = teacher_constraints.get(tid)
            if _teacher_unavailable(slot, tc):
                t_name = tc.teacher_name if tc else "Assigned teacher"
                diagnostics.append(
                    SolverConflictDiagnostic(
                        code="TEACHER_UNAVAILABLE",
                        message=f"{t_name} is marked unavailable during {slot.label} on day {slot.day_of_week}.",
                    )
                )

        for entry in existing_entries:
            if (
                entry.class_subject_id == moving_cs.class_subject_id
                and entry.timetable_slot_id == slot.id
            ):
                continue
            other_slot = slots_by_id.get(entry.timetable_slot_id)
            if other_slot is None:
                continue
            if (
                entry.class_group_id == moving_cs.class_group_id
                and entry.timetable_slot_id == slot.id
            ):
                other_cs = cs_by_id.get(entry.class_subject_id)
                diagnostics.append(
                    SolverConflictDiagnostic(
                        code="CLASS_SLOT_OCCUPIED",
                        message=(
                            f"{moving_cs.class_group_name} already has "
                            f"{other_cs.subject_name if other_cs else 'another subject'} in {slot.label}."
                        ),
                    )
                )
                continue

            is_same_joint = bool(entry.sync_group_id and entry.sync_group_id in joint_group_ids)
            if is_same_joint:
                continue

            other_cs = cs_by_id.get(entry.class_subject_id)
            gap = 0
            if other_cs and other_cs.level_category != moving_cs.level_category:
                for tid in set(moving_cs.teacher_ids) & set(entry.teacher_ids):
                    tc = teacher_constraints.get(tid)
                    if tc:
                        gap = max(gap, tc.min_gap_minutes_between_categories)

            if not intervals_overlap(
                slot.day_of_week,
                slot.start_time,
                slot.end_time,
                other_slot.day_of_week,
                other_slot.start_time,
                other_slot.end_time,
                gap_minutes=gap,
            ):
                continue

            shared_teachers = set(moving_cs.teacher_ids) & set(entry.teacher_ids)
            if shared_teachers:
                t_names = ", ".join(
                    teacher_constraints[t].teacher_name if t in teacher_constraints else "Teacher"
                    for t in shared_teachers
                )
                diagnostics.append(
                    SolverConflictDiagnostic(
                        code="TEACHER_TIME_CONFLICT",
                        message=(
                            f"{t_names} is already scheduled in "
                            f"{other_cs.class_group_name if other_cs else 'another class'} "
                            f"({other_cs.subject_code if other_cs else ''}) at an overlapping time."
                        ),
                    )
                )

            if moving_room_id is not None and entry.room_id == moving_room_id:
                diagnostics.append(
                    SolverConflictDiagnostic(
                        code="ROOM_TIME_CONFLICT",
                        message=(
                            f"The selected facility is already booked by "
                            f"{other_cs.class_group_name if other_cs else 'another class'} at that time."
                        ),
                    )
                )

    return diagnostics


@dataclass
class _BlockUnit:
    unit_id: str
    is_double: bool
    members: tuple[ClassSubjectSpec, ...]
    sync_group_id: UUID | None
    sync_type: str | None
    teacher_ids: tuple[UUID, ...]
    room_by_member: dict[UUID, UUID | None]
    prefer_morning: bool


@dataclass(frozen=True)
class _TeacherBooking:
    slot: SlotSpec
    level_category: str
    sync_group_id: UUID | None
    class_group_name: str
    subject_name: str
    class_subject_id: UUID
    subject_code: str


@dataclass(frozen=True)
class _RoomBooking:
    slot: SlotSpec
    sync_group_id: UUID | None
    class_group_name: str
    subject_name: str
    class_subject_id: UUID


_DAY_NAMES = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


def _fmt_slot(s: SlotSpec) -> str:
    day = _DAY_NAMES.get(s.day_of_week, f"Day {s.day_of_week}")
    return f"{day} {s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')} ({s.label})"


def _solve_once(
    *,
    slots: list[SlotSpec],
    class_subjects: list[ClassSubjectSpec],
    rooms: dict[UUID, RoomSpec] | None = None,
    sync_groups: list[SyncGroupSpec] | None = None,
    teacher_constraints: dict[UUID, TeacherConstraintSpec] | None = None,
    pinned_entries: list[PinnedEntrySpec] | None = None,
    disjoint_class_subject_pairs: set[tuple[UUID, UUID]] | None = None,
    seed: int = 42,
    deadline: float | None = None,
) -> TimetableSolveResult:
    """Solve a conflict-free weekly timetable across all level categories.

    ``deadline`` is an absolute `time.monotonic()` value; the local-search
    improvement phase stops early once it is reached so large schools (120+
    arms) can't stall a worker indefinitely.
    """
    if deadline is None:
        deadline = time_module.monotonic() + 20.0
    rooms = rooms or {}
    sync_groups = sync_groups or []
    teacher_constraints = teacher_constraints or {}
    pinned_entries = pinned_entries or []
    disjoint_pairs = disjoint_class_subject_pairs or set()
    # Precomputed once so the per-candidate feasibility check below only ever
    # looks at a class subject's actual disjoint partners (usually zero or a
    # handful, e.g. CRS vs IRS) instead of scanning every class subject placed
    # so far -- without this, construction is O(placed class subjects) per
    # candidate slot per unit, which made even a 30-arm school hang for
    # minutes instead of respecting the time budget.
    disjoint_partners: dict[UUID, set[UUID]] = {}
    for a, b in disjoint_pairs:
        disjoint_partners.setdefault(a, set()).add(b)
        disjoint_partners.setdefault(b, set()).add(a)

    slots_by_id = {s.id: s for s in slots}
    teaching_slots_by_schedule: dict[UUID, list[SlotSpec]] = {}
    all_slots_by_schedule_day: dict[tuple[UUID, int], list[SlotSpec]] = {}
    for s in sorted(slots, key=lambda x: (x.bell_schedule_id, x.day_of_week, x.period_order)):
        all_slots_by_schedule_day.setdefault((s.bell_schedule_id, s.day_of_week), []).append(s)
        if s.slot_type == "TEACHING":
            teaching_slots_by_schedule.setdefault(s.bell_schedule_id, []).append(s)

    cs_by_id = {cs.class_subject_id: cs for cs in class_subjects}
    diagnostics: list[SolverConflictDiagnostic] = []

    # 1. Pre-solve capacity & consistency checks
    periods_by_cg: dict[UUID, int] = {}
    schedule_by_cg: dict[UUID, UUID] = {}
    cg_names: dict[UUID, str] = {}
    teacher_weekly_load: dict[UUID, int] = {}

    seen_parallel_cs: set[UUID] = set()
    for sg in sync_groups:
        if sg.sync_type != "PARALLEL_SPLIT":
            continue
        by_cg: dict[UUID, list[ClassSubjectSpec]] = {}
        for csid in sg.class_subject_ids:
            cs_item = cs_by_id.get(csid)
            if cs_item is not None and cs_item.class_subject_id not in seen_parallel_cs:
                by_cg.setdefault(cs_item.class_group_id, []).append(cs_item)
        for cg_id, members in by_cg.items():
            periods_by_cg[cg_id] = periods_by_cg.get(cg_id, 0) + max(
                m.periods_per_week for m in members
            )
            for m in members:
                seen_parallel_cs.add(m.class_subject_id)

    for cs in class_subjects:
        if cs.class_subject_id not in seen_parallel_cs:
            periods_by_cg[cs.class_group_id] = (
                periods_by_cg.get(cs.class_group_id, 0) + cs.periods_per_week
            )
        schedule_by_cg[cs.class_group_id] = cs.bell_schedule_id
        cg_names[cs.class_group_id] = cs.class_group_name
        if cs.double_periods_per_week * 2 > cs.periods_per_week:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="INVALID_DOUBLE_PERIOD_RULE",
                    message=(
                        f"{cs.subject_name} in {cs.class_group_name} requests "
                        f"{cs.double_periods_per_week} double periods ({cs.double_periods_per_week * 2} slots) "
                        f"which exceeds its {cs.periods_per_week} periods per week."
                    ),
                )
            )
        if cs.double_periods_per_week > 0 and cs.max_periods_per_day < 2:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="INVALID_MAX_PER_DAY",
                    message=(
                        f"{cs.subject_name} in {cs.class_group_name} requires double periods "
                        f"but max_periods_per_day is {cs.max_periods_per_day}."
                    ),
                )
            )
        if cs.preferred_room_id and cs.preferred_room_id in rooms:
            room = rooms[cs.preferred_room_id]
            if room.capacity is not None and cs.enrollment_count > room.capacity:
                diagnostics.append(
                    SolverConflictDiagnostic(
                        code="ROOM_CAPACITY_EXCEEDED",
                        message=(
                            f"{cs.class_group_name} ({cs.enrollment_count} learners) exceeds "
                            f"{room.name} capacity ({room.capacity})."
                        ),
                        details={
                            "room_id": str(cs.preferred_room_id),
                            "class_subject_id": str(cs.class_subject_id),
                        },
                    )
                )

    for cg_id, total_needed in periods_by_cg.items():
        avail = len(teaching_slots_by_schedule.get(schedule_by_cg[cg_id], []))
        if total_needed > avail:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="CLASS_CAPACITY_EXCEEDED",
                    message=(
                        f"{cg_names[cg_id]} requires {total_needed} periods/week, "
                        f"but its bell schedule only has {avail} teaching slots."
                    ),
                    details={
                        "bell_schedule_id": str(schedule_by_cg[cg_id]),
                        "class_group_id": str(cg_id),
                    },
                )
            )

    # Only abort early on structurally invalid double-period rules; allow capacity/room
    # warnings to proceed with a best-effort partial solve so admins can see what fits.
    fatal_codes = {"INVALID_DOUBLE_PERIOD_RULE", "INVALID_MAX_PER_DAY"}
    if any(d.code in fatal_codes for d in diagnostics):
        return TimetableSolveResult(success=False, diagnostics=diagnostics, unplaced_count=0)

    # Precompute adjacent teaching slot pairs per schedule (must have consecutive period_order and no non-teaching barrier)
    adjacent_next: dict[UUID, SlotSpec] = {}
    for (_sched_id, _day), day_slots in all_slots_by_schedule_day.items():
        day_slots_sorted = sorted(day_slots, key=lambda x: x.period_order)
        for idx in range(len(day_slots_sorted) - 1):
            cur, nxt = day_slots_sorted[idx], day_slots_sorted[idx + 1]
            if (
                cur.slot_type == "TEACHING"
                and nxt.slot_type == "TEACHING"
                and nxt.period_order == cur.period_order + 1
            ):
                adjacent_next[cur.id] = nxt

    # Build pinned lookup and initial placed entries
    solved_entries: list[SolvedEntry] = []
    pinned_cs_slot_counts: dict[UUID, int] = {}
    pinned_cs_double_counts: dict[UUID, int] = {}
    seen_double_pairs: set[UUID] = set()

    for pin in pinned_entries:
        cs = cs_by_id.get(pin.class_subject_id)
        slot = slots_by_id.get(pin.timetable_slot_id)
        if cs is None or slot is None:
            continue
        solved_entries.append(
            SolvedEntry(
                class_group_id=pin.class_group_id,
                class_subject_id=pin.class_subject_id,
                teacher_ids=cs.teacher_ids,
                timetable_slot_id=pin.timetable_slot_id,
                room_id=pin.room_id,
                sync_group_id=None,
                is_double_period_part=pin.double_period_pair_id is not None,
                double_period_pair_id=pin.double_period_pair_id,
                is_pinned=True,
            )
        )
        pinned_cs_slot_counts[cs.class_subject_id] = (
            pinned_cs_slot_counts.get(cs.class_subject_id, 0) + 1
        )
        if pin.double_period_pair_id and pin.double_period_pair_id not in seen_double_pairs:
            seen_double_pairs.add(pin.double_period_pair_id)
            pinned_cs_double_counts[cs.class_subject_id] = (
                pinned_cs_double_counts.get(cs.class_subject_id, 0) + 1
            )

    # Group synced ClassSubjects into block units first, then remaining ClassSubjects
    sync_member_to_group: dict[UUID, SyncGroupSpec] = {}
    for sg in sync_groups:
        for csid in sg.class_subject_ids:
            sync_member_to_group[csid] = sg

    units: list[_BlockUnit] = []
    handled_cs: set[UUID] = set()

    for sg in sync_groups:
        members = [cs_by_id[csid] for csid in sg.class_subject_ids if csid in cs_by_id]
        if not members:
            continue
        for m in members:
            handled_cs.add(m.class_subject_id)
        min_doubles = min(
            max(0, m.double_periods_per_week - pinned_cs_double_counts.get(m.class_subject_id, 0))
            for m in members
        )
        min_periods = min(
            max(0, m.periods_per_week - pinned_cs_slot_counts.get(m.class_subject_id, 0))
            for m in members
        )
        singles_needed = max(0, min_periods - min_doubles * 2)
        all_tids = tuple(sorted({tid for m in members for tid in m.teacher_ids}))

        for idx in range(min_doubles):
            units.append(
                _BlockUnit(
                    unit_id=f"sg:{sg.id}:d:{idx}",
                    is_double=True,
                    members=tuple(members),
                    sync_group_id=sg.id,
                    sync_type=sg.sync_type,
                    teacher_ids=all_tids,
                    room_by_member={m.class_subject_id: m.preferred_room_id for m in members},
                    prefer_morning=any(m.prefer_morning for m in members),
                )
            )
        for idx in range(singles_needed):
            units.append(
                _BlockUnit(
                    unit_id=f"sg:{sg.id}:s:{idx}",
                    is_double=False,
                    members=tuple(members),
                    sync_group_id=sg.id,
                    sync_type=sg.sync_type,
                    teacher_ids=all_tids,
                    room_by_member={
                        m.class_subject_id: (
                            None if m.room_only_for_double_periods else m.preferred_room_id
                        )
                        for m in members
                    },
                    prefer_morning=any(m.prefer_morning for m in members),
                )
            )

    for cs in class_subjects:
        rem_periods = max(
            0, cs.periods_per_week - pinned_cs_slot_counts.get(cs.class_subject_id, 0)
        )
        rem_doubles = max(
            0, cs.double_periods_per_week - pinned_cs_double_counts.get(cs.class_subject_id, 0)
        )
        if cs.class_subject_id in handled_cs:
            # Handle any leftover periods if one sync member has more periods than the synced block
            sg = sync_member_to_group[cs.class_subject_id]
            members = [cs_by_id[c] for c in sg.class_subject_ids if c in cs_by_id]
            synced_p = min(m.periods_per_week for m in members)
            synced_d = min(m.double_periods_per_week for m in members)
            rem_periods = max(0, rem_periods - synced_p)
            rem_doubles = max(0, rem_doubles - synced_d)

        rem_singles = max(0, rem_periods - rem_doubles * 2)
        for idx in range(rem_doubles):
            units.append(
                _BlockUnit(
                    unit_id=f"cs:{cs.class_subject_id}:d:{idx}",
                    is_double=True,
                    members=(cs,),
                    sync_group_id=None,
                    sync_type=None,
                    teacher_ids=cs.teacher_ids,
                    room_by_member={cs.class_subject_id: cs.preferred_room_id},
                    prefer_morning=cs.prefer_morning,
                )
            )
        for idx in range(rem_singles):
            units.append(
                _BlockUnit(
                    unit_id=f"cs:{cs.class_subject_id}:s:{idx}",
                    is_double=False,
                    members=(cs,),
                    sync_group_id=None,
                    sync_type=None,
                    teacher_ids=cs.teacher_ids,
                    room_by_member={
                        cs.class_subject_id: (
                            None if cs.room_only_for_double_periods else cs.preferred_room_id
                        )
                    },
                    prefer_morning=cs.prefer_morning,
                )
            )

    # Check teacher weekly load caps
    for cs in class_subjects:
        for tid in cs.teacher_ids:
            teacher_weekly_load[tid] = teacher_weekly_load.get(tid, 0) + cs.periods_per_week
    for tid, load in teacher_weekly_load.items():
        tc = teacher_constraints.get(tid)
        if tc and tc.max_periods_per_week is not None and load > tc.max_periods_per_week:
            diagnostics.append(
                SolverConflictDiagnostic(
                    code="TEACHER_WEEKLY_LOAD_EXCEEDED",
                    message=(
                        f"{tc.teacher_name} is assigned {load} periods/week, "
                        f"exceeding their weekly limit of {tc.max_periods_per_week}."
                    ),
                    details={"teacher_id": str(tid), "needed_weekly_cap": load},
                )
            )
    # Only abort early on structurally invalid double-period rules; allow capacity/room
    # warnings to proceed with a best-effort partial solve so admins can see what fits.
    fatal_codes = {"INVALID_DOUBLE_PERIOD_RULE", "INVALID_MAX_PER_DAY"}
    if any(d.code in fatal_codes for d in diagnostics):
        return TimetableSolveResult(success=False, diagnostics=diagnostics, unplaced_count=0)

    # Tracking state for fast incremental feasibility checks
    class_slot_occupied: set[tuple[UUID, UUID]] = {
        (e.class_group_id, e.timetable_slot_id) for e in solved_entries
    }
    class_slot_owner: dict[tuple[UUID, UUID], str] = {}
    cs_day_counts: dict[tuple[UUID, int], int] = {}
    teacher_day_counts: dict[tuple[UUID, int], int] = {}
    teacher_bookings: dict[UUID, list[_TeacherBooking]] = {}
    room_bookings: dict[UUID, list[_RoomBooking]] = {}
    cs_slots: dict[UUID, list[SlotSpec]] = {}

    for e in solved_entries:
        s = slots_by_id[e.timetable_slot_id]
        cs = cs_by_id[e.class_subject_id]
        cs_day_counts[(cs.class_subject_id, s.day_of_week)] = (
            cs_day_counts.get((cs.class_subject_id, s.day_of_week), 0) + 1
        )
        cs_slots.setdefault(cs.class_subject_id, []).append(s)
        class_slot_owner[(e.class_group_id, s.id)] = cs.subject_name
        for tid in e.teacher_ids:
            teacher_day_counts[(tid, s.day_of_week)] = (
                teacher_day_counts.get((tid, s.day_of_week), 0) + 1
            )
            teacher_bookings.setdefault(tid, []).append(
                _TeacherBooking(
                    s,
                    cs.level_category,
                    e.sync_group_id,
                    cs.class_group_name,
                    cs.subject_name,
                    cs.class_subject_id,
                    cs.subject_code,
                )
            )
        if e.room_id:
            room_bookings.setdefault(e.room_id, []).append(
                _RoomBooking(
                    s, e.sync_group_id, cs.class_group_name, cs.subject_name, cs.class_subject_id
                )
            )

    rng = random.Random(seed)

    def _candidate_placements(
        unit: _BlockUnit,
        candidate_slots: list[SlotSpec] | None = None,
    ) -> list[tuple[int, dict[UUID, tuple[SlotSpec, SlotSpec | None]]]]:
        anchor = unit.members[0]
        anchor_slots = (
            candidate_slots
            if candidate_slots is not None
            else teaching_slots_by_schedule.get(anchor.bell_schedule_id, [])
        )
        placements: list[tuple[int, dict[UUID, tuple[SlotSpec, SlotSpec | None]]]] = []

        for s1 in anchor_slots:
            if unit.is_double:
                s2 = adjacent_next.get(s1.id)
                if s2 is None:
                    continue
            else:
                s2 = None

            member_map: dict[UUID, tuple[SlotSpec, SlotSpec | None]] = {}
            valid = True
            score = 0

            for m in unit.members:
                # Find matching slot(s) in member's bell schedule with same day & start/end time
                if m.bell_schedule_id == anchor.bell_schedule_id:
                    ms1, ms2 = s1, s2
                else:
                    ms1 = next(
                        (
                            cand
                            for cand in teaching_slots_by_schedule.get(m.bell_schedule_id, [])
                            if cand.day_of_week == s1.day_of_week
                            and cand.start_time == s1.start_time
                            and cand.end_time == s1.end_time
                        ),
                        None,
                    )
                    ms2 = adjacent_next.get(ms1.id) if (ms1 and unit.is_double) else None
                    if ms1 is None or (unit.is_double and ms2 is None):
                        valid = False
                        break

                needed_slots = [ms1] + ([ms2] if ms2 else [])
                # Class arm slot availability
                if any((m.class_group_id, ns.id) in class_slot_occupied for ns in needed_slots):
                    valid = False
                    break

                # Max periods per day for subject
                day_used = cs_day_counts.get((m.class_subject_id, ms1.day_of_week), 0)
                if day_used + len(needed_slots) > m.max_periods_per_day:
                    valid = False
                    break

                # Disjoint student elective pairs -- only ever checks this
                # class subject's actual disjoint partners (see
                # disjoint_partners above), not every class subject placed
                # so far.
                for other_csid in disjoint_partners.get(m.class_subject_id, ()):
                    other_slots = cs_slots.get(other_csid)
                    if not other_slots:
                        continue
                    if any(
                        intervals_overlap(
                            ns.day_of_week,
                            ns.start_time,
                            ns.end_time,
                            os.day_of_week,
                            os.start_time,
                            os.end_time,
                        )
                        for ns in needed_slots
                        for os in other_slots
                    ):
                        valid = False
                        break
                if not valid:
                    break

                # Teacher checks
                for tid in m.teacher_ids:
                    tc = teacher_constraints.get(tid)
                    if any(_teacher_unavailable(ns, tc) for ns in needed_slots):
                        valid = False
                        break
                    if any(_teacher_unavailable(ns, tc, include_avoid=True) for ns in needed_slots):
                        score -= 20
                    if tc and tc.max_periods_per_day is not None:
                        t_day = teacher_day_counts.get((tid, ms1.day_of_week), 0)
                        if t_day + len(needed_slots) > tc.max_periods_per_day:
                            valid = False
                            break
                    for b in teacher_bookings.get(tid, []):
                        if (
                            unit.sync_type == "JOINT_CLASS"
                            and b.sync_group_id == unit.sync_group_id
                        ):
                            continue
                        gap = (
                            tc.min_gap_minutes_between_categories
                            if (tc and b.level_category != m.level_category)
                            else 0
                        )
                        if any(
                            intervals_overlap(
                                ns.day_of_week,
                                ns.start_time,
                                ns.end_time,
                                b.slot.day_of_week,
                                b.slot.start_time,
                                b.slot.end_time,
                                gap_minutes=gap,
                            )
                            for ns in needed_slots
                        ):
                            valid = False
                            break
                    if not valid:
                        break
                if not valid:
                    break

                # Room check
                r_id = unit.room_by_member.get(m.class_subject_id)
                if r_id is not None:
                    for b in room_bookings.get(r_id, []):
                        if (
                            unit.sync_type == "JOINT_CLASS"
                            and b.sync_group_id == unit.sync_group_id
                        ):
                            continue
                        if any(
                            intervals_overlap(
                                ns.day_of_week,
                                ns.start_time,
                                ns.end_time,
                                b.slot.day_of_week,
                                b.slot.start_time,
                                b.slot.end_time,
                            )
                            for ns in needed_slots
                        ):
                            valid = False
                            break
                if not valid:
                    break

                member_map[m.class_subject_id] = (ms1, ms2)

                # Soft preferences: morning preference, even weekly spread & teacher fatigue
                mid = _slot_midpoint_minutes(ms1)
                if m.prefer_morning:
                    score += max(0, (12 * 60 - mid) // 10)
                if day_used == 0:
                    score += 15
                else:
                    score -= 10
                for tid in m.teacher_ids:
                    # Penalize >3 consecutive periods for the same teacher on the same day
                    day_orders = sorted(
                        {
                            b.slot.period_order
                            for b in teacher_bookings.get(tid, [])
                            if b.slot.day_of_week == ms1.day_of_week
                        }
                        | {ns.period_order for ns in needed_slots}
                    )
                    streak = 1
                    max_streak = 1
                    for idx_o in range(1, len(day_orders)):
                        if day_orders[idx_o] == day_orders[idx_o - 1] + 1:
                            streak += 1
                            max_streak = max(max_streak, streak)
                        else:
                            streak = 1
                    if max_streak > 3:
                        score -= 18 * (max_streak - 3)

            if valid:
                # Deterministic per-(seed, slot) jitter for tie-breaking. This must
                # give the same value every time the same slot is re-evaluated
                # within one solve (local search calls this repeatedly), otherwise
                # hill-climbing's "strictly improving" check is comparing against a
                # moving target and can oscillate forever instead of converging.
                # Varying it by seed still gives different restarts different
                # tie-breaks.
                score += (s1.id.int + seed) % 4
                placements.append((score, member_map))

        placements.sort(key=lambda x: x[0], reverse=True)
        return placements

    # Order most-constrained units first (double periods before singles so pairs aren't fragmented)
    units_ordered = sorted(
        units,
        key=lambda u: (
            0 if u.is_double else 1,
            0 if u.sync_group_id else 1,
            -len(u.teacher_ids),
            0 if u.prefer_morning else 1,
            u.unit_id,
        ),
    )

    total_soft_score = 0
    unplaced_units: list[_BlockUnit] = []

    def _commit_entry(entry: SolvedEntry, slot: SlotSpec, m: ClassSubjectSpec) -> None:
        solved_entries.append(entry)
        class_slot_occupied.add((m.class_group_id, slot.id))
        class_slot_owner[(m.class_group_id, slot.id)] = m.subject_name
        cs_day_counts[(m.class_subject_id, slot.day_of_week)] = (
            cs_day_counts.get((m.class_subject_id, slot.day_of_week), 0) + 1
        )
        cs_slots.setdefault(m.class_subject_id, []).append(slot)
        for tid in m.teacher_ids:
            teacher_day_counts[(tid, slot.day_of_week)] = (
                teacher_day_counts.get((tid, slot.day_of_week), 0) + 1
            )
            teacher_bookings.setdefault(tid, []).append(
                _TeacherBooking(
                    slot,
                    m.level_category,
                    entry.sync_group_id,
                    m.class_group_name,
                    m.subject_name,
                    m.class_subject_id,
                    m.subject_code,
                )
            )
        if entry.room_id is not None:
            room_bookings.setdefault(entry.room_id, []).append(
                _RoomBooking(
                    slot,
                    entry.sync_group_id,
                    m.class_group_name,
                    m.subject_name,
                    m.class_subject_id,
                )
            )

    def _uncommit_entry(entry: SolvedEntry, slot: SlotSpec, m: ClassSubjectSpec) -> None:
        solved_entries.remove(entry)
        class_slot_occupied.discard((m.class_group_id, slot.id))
        class_slot_owner.pop((m.class_group_id, slot.id), None)
        cs_day_counts[(m.class_subject_id, slot.day_of_week)] = max(
            0, cs_day_counts.get((m.class_subject_id, slot.day_of_week), 1) - 1
        )
        if m.class_subject_id in cs_slots and slot in cs_slots[m.class_subject_id]:
            cs_slots[m.class_subject_id].remove(slot)
        for tid in m.teacher_ids:
            teacher_day_counts[(tid, slot.day_of_week)] = max(
                0, teacher_day_counts.get((tid, slot.day_of_week), 1) - 1
            )
            if tid in teacher_bookings:
                booking = _TeacherBooking(
                    slot,
                    m.level_category,
                    entry.sync_group_id,
                    m.class_group_name,
                    m.subject_name,
                    m.class_subject_id,
                    m.subject_code,
                )
                if booking in teacher_bookings[tid]:
                    teacher_bookings[tid].remove(booking)
        if entry.room_id is not None and entry.room_id in room_bookings:
            rbooking = _RoomBooking(
                slot, entry.sync_group_id, m.class_group_name, m.subject_name, m.class_subject_id
            )
            if rbooking in room_bookings[entry.room_id]:
                room_bookings[entry.room_id].remove(rbooking)

    for unit in units_ordered:
        if time_module.monotonic() >= deadline:
            unplaced_units.append(unit)
            continue
        cands = _candidate_placements(unit)
        if not cands:
            unplaced_units.append(unit)
            continue
        best_score, chosen_map = cands[0]
        total_soft_score += best_score
        pair_id = uuid4() if unit.is_double else None

        for m in unit.members:
            ms1, ms2 = chosen_map[m.class_subject_id]
            r_id = unit.room_by_member.get(m.class_subject_id)
            for slot in [ms1] + ([ms2] if ms2 else []):
                entry = SolvedEntry(
                    class_group_id=m.class_group_id,
                    class_subject_id=m.class_subject_id,
                    teacher_ids=m.teacher_ids,
                    timetable_slot_id=slot.id,
                    room_id=r_id,
                    sync_group_id=unit.sync_group_id,
                    is_double_period_part=unit.is_double,
                    double_period_pair_id=pair_id,
                    is_pinned=False,
                )
                _commit_entry(entry, slot, m)

    # Multi-pass 1-step swap repair for any remaining unplaced single units
    for _repair_pass in range(3):
        if not unplaced_units or time_module.monotonic() >= deadline:
            break
        still_unplaced: list[_BlockUnit] = []
        repaired_any = False
        for unit in unplaced_units:
            if time_module.monotonic() >= deadline:
                still_unplaced.append(unit)
                continue
            if unit.is_double or len(unit.members) != 1:
                still_unplaced.append(unit)
                continue
            m = unit.members[0]
            repaired = False
            movable = [
                e
                for e in list(solved_entries)
                if e.class_group_id == m.class_group_id
                and not e.is_pinned
                and not e.is_double_period_part
                and e.sync_group_id is None
            ]
            for existing in movable:
                ex_cs = cs_by_id[existing.class_subject_id]
                ex_slot = slots_by_id[existing.timetable_slot_id]
                _uncommit_entry(existing, ex_slot, ex_cs)
                new_cands = _candidate_placements(unit)
                if new_cands:
                    cand_score, chosen_map = new_cands[0]
                    ms1, _ = chosen_map[m.class_subject_id]
                    new_entry = SolvedEntry(
                        class_group_id=m.class_group_id,
                        class_subject_id=m.class_subject_id,
                        teacher_ids=m.teacher_ids,
                        timetable_slot_id=ms1.id,
                        room_id=unit.room_by_member.get(m.class_subject_id),
                        sync_group_id=None,
                        is_double_period_part=False,
                        double_period_pair_id=None,
                        is_pinned=False,
                    )
                    _commit_entry(new_entry, ms1, m)
                    ex_unit = _BlockUnit(
                        unit_id=f"repair:{ex_cs.class_subject_id}",
                        is_double=False,
                        members=(ex_cs,),
                        sync_group_id=None,
                        sync_type=None,
                        teacher_ids=ex_cs.teacher_ids,
                        room_by_member={ex_cs.class_subject_id: existing.room_id},
                        prefer_morning=ex_cs.prefer_morning,
                    )
                    ex_cands = _candidate_placements(ex_unit)
                    if ex_cands:
                        ex_cand_score, ex_map = ex_cands[0]
                        ex_ms1, _ = ex_map[ex_cs.class_subject_id]
                        moved_entry = SolvedEntry(
                            class_group_id=ex_cs.class_group_id,
                            class_subject_id=ex_cs.class_subject_id,
                            teacher_ids=ex_cs.teacher_ids,
                            timetable_slot_id=ex_ms1.id,
                            room_id=existing.room_id,
                            sync_group_id=None,
                            is_double_period_part=False,
                            double_period_pair_id=None,
                            is_pinned=False,
                        )
                        _commit_entry(moved_entry, ex_ms1, ex_cs)
                        total_soft_score += cand_score + ex_cand_score
                        repaired = True
                        repaired_any = True
                        break
                    _uncommit_entry(new_entry, ms1, m)
                _commit_entry(existing, ex_slot, ex_cs)
            if not repaired:
                still_unplaced.append(unit)
        unplaced_units = still_unplaced
        if not repaired_any:
            break

    # Tier 2 Local Search: hill-climbing pass to improve morning balance,
    # even weekly spread, and teacher consecutive-period fatigue.
    def _entry_slot_score(cs: ClassSubjectSpec, slot: SlotSpec) -> int:
        # Must include the same deterministic jitter _candidate_placements adds,
        # or every comparison is biased toward whichever slot's jitter happens to
        # be higher -- the current slot's own jitter would never get credited,
        # so local search would "improve" into high-jitter slots forever and
        # never converge.
        s = (slot.id.int + seed) % 4
        mid = _slot_midpoint_minutes(slot)
        if cs.prefer_morning:
            s += max(0, (12 * 60 - mid) // 10)
        day_used = cs_day_counts.get((cs.class_subject_id, slot.day_of_week), 0)
        s += 15 if day_used == 0 else -10
        for tid in cs.teacher_ids:
            tc = teacher_constraints.get(tid)
            if _teacher_unavailable(slot, tc, include_avoid=True):
                s -= 20
            day_orders = sorted(
                {
                    b.slot.period_order
                    for b in teacher_bookings.get(tid, [])
                    if b.slot.day_of_week == slot.day_of_week
                }
                | {slot.period_order}
            )
            streak = 1
            max_streak = 1
            for idx_o in range(1, len(day_orders)):
                if day_orders[idx_o] == day_orders[idx_o - 1] + 1:
                    streak += 1
                    max_streak = max(max_streak, streak)
                else:
                    streak = 1
            if max_streak > 3:
                s -= 18 * (max_streak - 3)
        return s

    def _single_unit(cs: ClassSubjectSpec, room_id: UUID | None, tag: str) -> _BlockUnit:
        return _BlockUnit(
            unit_id=f"{tag}:{cs.class_subject_id}",
            is_double=False,
            members=(cs,),
            sync_group_id=None,
            sync_type=None,
            teacher_ids=cs.teacher_ids,
            room_by_member={cs.class_subject_id: room_id},
            prefer_morning=cs.prefer_morning,
        )

    # Relocate-to-empty-slot moves, with a swap-move fallback for when the grid
    # is too full for a free slot to exist. Loops to convergence (no improving
    # move found in a full sweep) or until the deadline is reached, whichever
    # comes first -- bounded so large schools (120+ arms) can't stall a worker.
    improved = True
    while improved and time_module.monotonic() < deadline:
        improved = False
        movable_all = [
            e
            for e in solved_entries
            if not e.is_pinned and not e.is_double_period_part and e.sync_group_id is None
        ]
        rng.shuffle(movable_all)
        for existing in movable_all:
            if time_module.monotonic() >= deadline:
                break
            if existing not in solved_entries:
                continue
            ex_cs = cs_by_id[existing.class_subject_id]
            ex_slot = slots_by_id[existing.timetable_slot_id]
            _uncommit_entry(existing, ex_slot, ex_cs)
            old_score = _entry_slot_score(ex_cs, ex_slot)
            ex_unit = _single_unit(ex_cs, existing.room_id, "ls")
            cands = _candidate_placements(ex_unit)
            if cands and cands[0][0] > old_score + 4:
                best_s, best_map = cands[0]
                new_slot, _ = best_map[ex_cs.class_subject_id]
                moved_entry = SolvedEntry(
                    class_group_id=ex_cs.class_group_id,
                    class_subject_id=ex_cs.class_subject_id,
                    teacher_ids=ex_cs.teacher_ids,
                    timetable_slot_id=new_slot.id,
                    room_id=existing.room_id,
                    sync_group_id=None,
                    is_double_period_part=False,
                    double_period_pair_id=None,
                    is_pinned=False,
                )
                _commit_entry(moved_entry, new_slot, ex_cs)
                total_soft_score += best_s - old_score
                improved = True
                continue

            # No improving empty slot -- try swapping slots with another movable
            # entry (a bounded random sample, not an exhaustive scan, to keep this
            # affordable on large schools).
            swapped = False
            sample_pool = [o for o in movable_all if o is not existing and o in solved_entries]
            for other in rng.sample(sample_pool, min(30, len(sample_pool))):
                other_cs = cs_by_id[other.class_subject_id]
                other_slot = slots_by_id[other.timetable_slot_id]
                _uncommit_entry(other, other_slot, other_cs)
                other_old_score = _entry_slot_score(other_cs, other_slot)
                a_cands = _candidate_placements(ex_unit, candidate_slots=[other_slot])
                if not a_cands:
                    _commit_entry(other, other_slot, other_cs)
                    continue
                a_score, a_map = a_cands[0]
                a_slot, _ = a_map[ex_cs.class_subject_id]
                temp_a = SolvedEntry(
                    class_group_id=ex_cs.class_group_id,
                    class_subject_id=ex_cs.class_subject_id,
                    teacher_ids=ex_cs.teacher_ids,
                    timetable_slot_id=a_slot.id,
                    room_id=existing.room_id,
                    sync_group_id=None,
                    is_double_period_part=False,
                    double_period_pair_id=None,
                    is_pinned=False,
                )
                _commit_entry(temp_a, a_slot, ex_cs)
                other_unit = _single_unit(other_cs, other.room_id, "ls")
                b_cands = _candidate_placements(other_unit, candidate_slots=[ex_slot])
                if not b_cands or (a_score + b_cands[0][0]) <= (old_score + other_old_score + 4):
                    _uncommit_entry(temp_a, a_slot, ex_cs)
                    _commit_entry(other, other_slot, other_cs)
                    continue
                b_score, b_map = b_cands[0]
                b_slot, _ = b_map[other_cs.class_subject_id]
                temp_b = SolvedEntry(
                    class_group_id=other_cs.class_group_id,
                    class_subject_id=other_cs.class_subject_id,
                    teacher_ids=other_cs.teacher_ids,
                    timetable_slot_id=b_slot.id,
                    room_id=other.room_id,
                    sync_group_id=None,
                    is_double_period_part=False,
                    double_period_pair_id=None,
                    is_pinned=False,
                )
                _commit_entry(temp_b, b_slot, other_cs)
                total_soft_score += (a_score + b_score) - (old_score + other_old_score)
                improved = True
                swapped = True
                break

            if not swapped:
                _commit_entry(existing, ex_slot, ex_cs)

    def _explain_unplaced(unit: _BlockUnit) -> tuple[str, dict[str, object]]:
        anchor = unit.members[0]
        anchor_slots = teaching_slots_by_schedule.get(anchor.bell_schedule_id, [])
        reason_counts: dict[str, int] = {}
        reason_examples: dict[str, str] = {}
        reason_data: dict[str, dict[str, object]] = {}

        def _note(code: str, example: str, data: dict[str, object] | None = None) -> None:
            reason_counts[code] = reason_counts.get(code, 0) + 1
            reason_examples.setdefault(code, example)
            if code not in reason_data:
                reason_data[code] = data or {}

        total_tried = 0
        for s1 in anchor_slots:
            if unit.is_double:
                s2 = adjacent_next.get(s1.id)
                if s2 is None:
                    _note(
                        "NO_ADJACENT_DOUBLE_SLOT",
                        f"{_fmt_slot(s1)} has no adjacent teaching period to pair with for a double period.",
                    )
                    continue
            else:
                s2 = None
            total_tried += 1

            for m in unit.members:
                if m.bell_schedule_id == anchor.bell_schedule_id:
                    ms1, ms2 = s1, s2
                else:
                    ms1 = next(
                        (
                            cand
                            for cand in teaching_slots_by_schedule.get(m.bell_schedule_id, [])
                            if cand.day_of_week == s1.day_of_week
                            and cand.start_time == s1.start_time
                            and cand.end_time == s1.end_time
                        ),
                        None,
                    )
                    ms2 = adjacent_next.get(ms1.id) if (ms1 and unit.is_double) else None
                    if ms1 is None or (unit.is_double and ms2 is None):
                        _note(
                            "NO_MATCHING_SLOT_OTHER_SCHEDULE",
                            f"{m.class_group_name}'s bell schedule has no period matching {_fmt_slot(s1)}.",
                        )
                        break

                needed_slots = [ms1] + ([ms2] if ms2 else [])

                if any((m.class_group_id, ns.id) in class_slot_occupied for ns in needed_slots):
                    busy_with = next(
                        (
                            class_slot_owner[(m.class_group_id, ns.id)]
                            for ns in needed_slots
                            if (m.class_group_id, ns.id) in class_slot_owner
                        ),
                        "another subject",
                    )
                    _note(
                        "CLASS_ARM_BUSY",
                        f"{m.class_group_name} already has {busy_with} at {_fmt_slot(ms1)}.",
                        {
                            "bell_schedule_id": str(m.bell_schedule_id),
                            "class_group_id": str(m.class_group_id),
                        },
                    )
                    break

                day_used = cs_day_counts.get((m.class_subject_id, ms1.day_of_week), 0)
                if day_used + len(needed_slots) > m.max_periods_per_day:
                    _note(
                        "MAX_PER_DAY",
                        f"{m.subject_name} already has its max of {m.max_periods_per_day} "
                        f"period(s) a day for {m.class_group_name} on {_DAY_NAMES.get(ms1.day_of_week, ms1.day_of_week)}.",
                        {"class_subject_id": str(m.class_subject_id)},
                    )
                    break

                disjoint_hit = False
                for other_csid in disjoint_partners.get(m.class_subject_id, ()):
                    other_slots = cs_slots.get(other_csid)
                    if not other_slots:
                        continue
                    if any(
                        intervals_overlap(
                            ns.day_of_week,
                            ns.start_time,
                            ns.end_time,
                            os.day_of_week,
                            os.start_time,
                            os.end_time,
                        )
                        for ns in needed_slots
                        for os in other_slots
                    ):
                        other_name = (
                            cs_by_id[other_csid].subject_name
                            if other_csid in cs_by_id
                            else "a linked elective"
                        )
                        _note(
                            "DISJOINT_ELECTIVE_CONFLICT",
                            f"{m.subject_name} clashes with {other_name} (students take both) at {_fmt_slot(ms1)}.",
                        )
                        disjoint_hit = True
                        break
                if disjoint_hit:
                    break

                teacher_hit = False
                for tid in m.teacher_ids:
                    tc = teacher_constraints.get(tid)
                    tname = tc.teacher_name if tc else "The teacher"
                    if any(_teacher_unavailable(ns, tc) for ns in needed_slots):
                        _note(
                            "TEACHER_UNAVAILABLE",
                            f"{tname} is marked unavailable at {_fmt_slot(ms1)}.",
                            {
                                "teacher_id": str(tid),
                                "day_of_week": ms1.day_of_week,
                                "start_time": ms1.start_time.isoformat(),
                                "end_time": ms1.end_time.isoformat(),
                            },
                        )
                        teacher_hit = True
                        break
                    if tc and tc.max_periods_per_day is not None:
                        t_day = teacher_day_counts.get((tid, ms1.day_of_week), 0)
                        if t_day + len(needed_slots) > tc.max_periods_per_day:
                            _note(
                                "TEACHER_MAX_PER_DAY",
                                f"{tname} already has their max of {tc.max_periods_per_day} period(s) on "
                                f"{_DAY_NAMES.get(ms1.day_of_week, ms1.day_of_week)}.",
                                {"teacher_id": str(tid)},
                            )
                            teacher_hit = True
                            break
                    for b in teacher_bookings.get(tid, []):
                        if (
                            unit.sync_type == "JOINT_CLASS"
                            and b.sync_group_id == unit.sync_group_id
                        ):
                            continue
                        gap = (
                            tc.min_gap_minutes_between_categories
                            if (tc and b.level_category != m.level_category)
                            else 0
                        )
                        if any(
                            intervals_overlap(
                                ns.day_of_week,
                                ns.start_time,
                                ns.end_time,
                                b.slot.day_of_week,
                                b.slot.start_time,
                                b.slot.end_time,
                                gap_minutes=gap,
                            )
                            for ns in needed_slots
                        ):
                            _note(
                                "TEACHER_BOOKED",
                                f"{tname} is already teaching {b.subject_name} to {b.class_group_name} "
                                f"at {_fmt_slot(b.slot)}.",
                                {
                                    "teacher_id": str(tid),
                                    "class_subject_id": str(m.class_subject_id),
                                    "other_class_subject_id": str(b.class_subject_id),
                                    "other_subject_code": b.subject_code,
                                    "same_subject": b.subject_code == m.subject_code,
                                },
                            )
                            teacher_hit = True
                            break
                    if teacher_hit:
                        break
                if teacher_hit:
                    break

                r_id = unit.room_by_member.get(m.class_subject_id)
                if r_id is not None:
                    room_hit = False
                    for b in room_bookings.get(r_id, []):
                        if (
                            unit.sync_type == "JOINT_CLASS"
                            and b.sync_group_id == unit.sync_group_id
                        ):
                            continue
                        if any(
                            intervals_overlap(
                                ns.day_of_week,
                                ns.start_time,
                                ns.end_time,
                                b.slot.day_of_week,
                                b.slot.start_time,
                                b.slot.end_time,
                            )
                            for ns in needed_slots
                        ):
                            room_name = rooms[r_id].name if r_id in rooms else "The room"
                            _note(
                                "ROOM_BOOKED",
                                f"{room_name} is already booked for {b.subject_name} ({b.class_group_name}) "
                                f"at {_fmt_slot(b.slot)}.",
                                {"room_id": str(r_id), "class_subject_id": str(m.class_subject_id)},
                            )
                            room_hit = True
                            break
                    if room_hit:
                        break

        subj = anchor.subject_name
        cgname = ", ".join(sorted({m.class_group_name for m in unit.members}))
        header = (
            f"Could not place {'a double period of ' if unit.is_double else ''}{subj} for {cgname}."
        )
        ranked = sorted(reason_counts.items(), key=lambda kv: -kv[1])
        if not ranked:
            lines = [
                header,
                "No teaching periods in this bell schedule were even tried (the schedule may have zero teaching slots).",
            ]
        else:
            lines = [header]
            for code, count in ranked[:3]:
                lines.append(
                    f"- {reason_examples[code]} ({count} of {total_tried} weekly slot(s) blocked this way)"
                )
        message = "\n".join(lines)
        details: dict[str, object] = {
            "class_subject_ids": [str(m.class_subject_id) for m in unit.members],
            "is_double": unit.is_double,
            "slots_considered": total_tried,
            "reasons": [
                {
                    "code": code,
                    "count": count,
                    "example": reason_examples[code],
                    "data": reason_data.get(code, {}),
                }
                for code, count in ranked
            ],
        }
        return message, details

    for unit in unplaced_units:
        message, details = _explain_unplaced(unit)
        diagnostics.append(
            SolverConflictDiagnostic(code="UNPLACED_PERIOD", message=message, details=details)
        )

    return TimetableSolveResult(
        success=len(unplaced_units) == 0 and len(diagnostics) == 0,
        entries=solved_entries,
        diagnostics=diagnostics,
        unplaced_count=len(unplaced_units),
        soft_score=total_soft_score,
    )


def solve_timetable(
    *,
    slots: list[SlotSpec],
    class_subjects: list[ClassSubjectSpec],
    rooms: dict[UUID, RoomSpec] | None = None,
    sync_groups: list[SyncGroupSpec] | None = None,
    teacher_constraints: dict[UUID, TeacherConstraintSpec] | None = None,
    pinned_entries: list[PinnedEntrySpec] | None = None,
    disjoint_class_subject_pairs: set[tuple[UUID, UUID]] | None = None,
    seed: int = 42,
    max_time_in_seconds: float = 20.0,
    restarts: int = 4,
) -> TimetableSolveResult:
    """Solve a conflict-free weekly timetable, trying several randomized
    construction orders/local-search runs within a shared time budget and
    keeping the best result (fewest unplaced periods, then highest soft score).

    A single greedy construction + local search run can get stuck in a
    mediocre local optimum depending on tie-break luck; restarting a handful
    of times with different seeds is a cheap, dependency-free way to let the
    better run win without changing any of the constraint logic.
    """
    deadline = time_module.monotonic() + max_time_in_seconds
    best: TimetableSolveResult | None = None

    for attempt in range(max(1, restarts)):
        if attempt > 0 and time_module.monotonic() >= deadline:
            break
        result = _solve_once(
            slots=slots,
            class_subjects=class_subjects,
            rooms=rooms,
            sync_groups=sync_groups,
            teacher_constraints=teacher_constraints,
            pinned_entries=pinned_entries,
            disjoint_class_subject_pairs=disjoint_class_subject_pairs,
            seed=seed + attempt * 7919,
            deadline=deadline,
        )
        # Structurally invalid input (bad double-period rules, etc.) is a fixed
        # diagnosis, not a search-quality issue -- no point burning restarts on it.
        if not result.success and result.unplaced_count == 0 and result.diagnostics:
            return result
        if best is None or (result.unplaced_count, -result.soft_score) < (
            best.unplaced_count,
            -best.soft_score,
        ):
            best = result

    assert best is not None
    return best
