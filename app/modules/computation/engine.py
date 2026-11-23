from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class AssessmentInput:
    code: str
    max_score: Decimal
    weight_percentage: Decimal
    score_status: str
    raw_score: Decimal | None


@dataclass(frozen=True, slots=True)
class AssessmentComputation:
    code: str
    score_status: str
    raw_score: Decimal | None
    scaled_score: Decimal


@dataclass(frozen=True, slots=True)
class SubjectInput:
    class_subject_id: str
    subject_code: str
    subject_name: str
    assessments: tuple[AssessmentInput, ...]
    subject_rank_key: str = ""


@dataclass(frozen=True, slots=True)
class SubjectComputation:
    class_subject_id: str
    subject_code: str
    subject_name: str
    total: Decimal
    obtainable: Decimal
    grade: str
    grade_point: Decimal
    remark: str
    position: int
    class_highest_score: Decimal
    class_lowest_score: Decimal
    class_average_score: Decimal
    components: tuple[AssessmentComputation, ...]


@dataclass(frozen=True, slots=True)
class StudentTermComputation:
    student_enrollment_id: str
    subjects: dict[str, SubjectComputation]
    total_obtained: Decimal
    total_obtainable: Decimal
    percentage: Decimal
    class_position: int
    total_students_in_class: int
    level_position: int = 1
    total_students_in_level: int = 1


@dataclass(frozen=True, slots=True)
class TermComputation:
    students: dict[str, StudentTermComputation]


def round_marks(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def compute_assessment(assessment: AssessmentInput) -> AssessmentComputation:
    if assessment.max_score <= ZERO:
        raise ValueError("Assessment max_score must be greater than zero.")
    if assessment.weight_percentage < ZERO:
        raise ValueError("Assessment weight_percentage cannot be negative.")

    if assessment.score_status == "SCORED":
        if assessment.raw_score is None:
            raise ValueError("A scored assessment needs a raw score.")
        if assessment.raw_score < ZERO or assessment.raw_score > assessment.max_score:
            raise ValueError("A raw score must be between zero and the assessment maximum.")
        scaled = assessment.raw_score * assessment.weight_percentage / assessment.max_score
    elif assessment.score_status == "ABSENT":
        scaled = ZERO
    elif assessment.score_status == "EXEMPT":
        scaled = ZERO
    elif assessment.score_status == "PENDING":
        raise ValueError("Pending scores cannot be computed.")
    else:
        raise ValueError(f"Unsupported score status: {assessment.score_status}")

    return AssessmentComputation(
        code=assessment.code,
        score_status=assessment.score_status,
        raw_score=assessment.raw_score,
        scaled_score=round_marks(scaled),
    )


def compute_subject_totals(
    components: Sequence[AssessmentComputation], assessments: Sequence[AssessmentInput]
) -> tuple[Decimal, Decimal]:
    weights_by_code = {assessment.code: assessment.weight_percentage for assessment in assessments}
    included = [component for component in components if component.score_status != "EXEMPT"]
    obtainable = sum((weights_by_code[component.code] for component in included), ZERO)
    if obtainable <= ZERO:
        return ZERO, ZERO
    marks = sum((component.scaled_score for component in included), ZERO)
    return round_marks(marks / obtainable * ONE_HUNDRED), round_marks(obtainable)


def grade_for_score(
    score: Decimal, grading_scale_schema: Mapping[str, Any]
) -> tuple[str, Decimal, str]:
    for entry in grading_scale_schema.get("grades", []):
        minimum = decimal_value(entry["min_score"])
        maximum = decimal_value(entry["max_score"])
        if minimum <= score <= maximum:
            grade_point = decimal_value(entry.get("grade_point", ZERO))
            return str(entry["grade"]), round_marks(grade_point), str(entry["remark"])
    raise ValueError("The grading scale does not include a grade for this score.")


def competition_ranks(values: Mapping[str, Decimal]) -> dict[str, int]:
    return {
        key: 1 + sum(1 for other in values.values() if other > value)
        for key, value in values.items()
    }


def is_exempt_subject(subject: SubjectComputation) -> bool:
    return bool(subject.components) and all(
        component.score_status == "EXEMPT" for component in subject.components
    )


def compute_subject(
    subject: SubjectInput, grading_scale_schema: Mapping[str, Any]
) -> SubjectComputation:
    components = tuple(compute_assessment(assessment) for assessment in subject.assessments)
    total, obtainable = compute_subject_totals(components, subject.assessments)
    grade, grade_point, remark = grade_for_score(total, grading_scale_schema)
    return SubjectComputation(
        class_subject_id=subject.class_subject_id,
        subject_code=subject.subject_code,
        subject_name=subject.subject_name,
        total=total,
        obtainable=obtainable,
        grade=grade,
        grade_point=grade_point,
        remark=remark,
        position=1,
        class_highest_score=ZERO,
        class_lowest_score=ZERO,
        class_average_score=ZERO,
        components=components,
    )


def compute_term_results(
    students: Mapping[str, Sequence[SubjectInput]],
    grading_scale_schema: Mapping[str, Any],
    *,
    student_dept_cohorts: Mapping[str, str] | None = None,
    student_level_cohorts: Mapping[str, str] | None = None,
) -> TermComputation:
    computed: dict[str, dict[str, SubjectComputation]] = {}
    rank_key_by_cs_id: dict[str, str] = {}
    for student_id, subjects in students.items():
        computed[student_id] = {
            subject.class_subject_id: compute_subject(subject, grading_scale_schema)
            for subject in subjects
        }
        for subject in subjects:
            rank_key_by_cs_id[subject.class_subject_id] = (
                subject.subject_rank_key or subject.class_subject_id
            )

    rank_keys = set(rank_key_by_cs_id.values())
    subject_statistics: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    subject_ranks: dict[str, dict[str, int]] = {}
    for rank_key in rank_keys:
        scores: dict[str, Decimal] = {}
        for student_id, subjects in computed.items():
            for cs_id, subj in subjects.items():
                if rank_key_by_cs_id.get(cs_id) == rank_key and not is_exempt_subject(subj):
                    scores[student_id] = subj.total
                    break
        if scores:
            highest = max(scores.values())
            lowest = min(scores.values())
            average = round_marks(sum(scores.values(), ZERO) / Decimal(len(scores)))
            subject_ranks[rank_key] = competition_ranks(scores)
        else:
            highest = lowest = average = ZERO
            subject_ranks[rank_key] = {}
        subject_statistics[rank_key] = (highest, lowest, average)

    for student_id, subjects in computed.items():
        for subject_id, subject in subjects.items():
            rank_key = rank_key_by_cs_id[subject_id]
            highest, lowest, average = subject_statistics[rank_key]
            subjects[subject_id] = replace(
                subject,
                position=subject_ranks[rank_key].get(student_id, 0),
                class_highest_score=highest,
                class_lowest_score=lowest,
                class_average_score=average,
            )

    percentages: dict[str, Decimal] = {}
    totals: dict[str, tuple[Decimal, Decimal]] = {}
    for student_id, subjects in computed.items():
        obtained = sum((subject.total for subject in subjects.values()), ZERO)
        obtainable = sum((subject.obtainable for subject in subjects.values()), ZERO)
        totals[student_id] = (round_marks(obtained), round_marks(obtainable))
        percentages[student_id] = (
            round_marks(obtained / obtainable * ONE_HUNDRED) if obtainable > ZERO else ZERO
        )

    dept_buckets: dict[str, dict[str, Decimal]] = {}
    level_buckets: dict[str, dict[str, Decimal]] = {}
    for student_id, pct in percentages.items():
        d_key = student_dept_cohorts.get(student_id, "ALL") if student_dept_cohorts else "ALL"
        l_key = student_level_cohorts.get(student_id, "ALL") if student_level_cohorts else "ALL"
        dept_buckets.setdefault(d_key, {})[student_id] = pct
        level_buckets.setdefault(l_key, {})[student_id] = pct

    dept_positions: dict[str, int] = {}
    dept_sizes: dict[str, int] = {}
    for bucket in dept_buckets.values():
        ranks = competition_ranks(bucket)
        for student_id, rank in ranks.items():
            dept_positions[student_id] = rank
            dept_sizes[student_id] = len(bucket)

    level_positions: dict[str, int] = {}
    level_sizes: dict[str, int] = {}
    for bucket in level_buckets.values():
        ranks = competition_ranks(bucket)
        for student_id, rank in ranks.items():
            level_positions[student_id] = rank
            level_sizes[student_id] = len(bucket)

    results: dict[str, StudentTermComputation] = {}
    for student_id, subjects in computed.items():
        obtained, obtainable = totals[student_id]
        results[student_id] = StudentTermComputation(
            student_enrollment_id=student_id,
            subjects=subjects,
            total_obtained=obtained,
            total_obtainable=obtainable,
            percentage=percentages[student_id],
            class_position=dept_positions[student_id],
            total_students_in_class=dept_sizes[student_id],
            level_position=level_positions[student_id],
            total_students_in_level=level_sizes[student_id],
        )
    return TermComputation(students=results)


def weighted_average(
    values: Mapping[str, Decimal], annual_results_schema: Mapping[str, Any]
) -> Decimal:
    weights = annual_results_schema["term_weights"]
    total_weight = sum(decimal_value(weight) for weight in weights.values())
    if total_weight <= ZERO:
        raise ValueError("Annual term weights must add up to a positive number.")
    weighted_sum = sum(values[key] * decimal_value(weights[key]) for key in values)
    return round_marks(weighted_sum / total_weight)


def promotion_decision(percentage: Decimal, annual_results_schema: Mapping[str, Any]) -> str:
    thresholds = annual_results_schema["promotion_thresholds"]
    promoted = decimal_value(thresholds["promoted_min_percentage"])
    trial = decimal_value(thresholds["trial_min_percentage"])
    if percentage >= promoted:
        return "PROMOTED"
    if percentage >= trial:
        return "PROMOTED_ON_TRIAL"
    return "REPEATING"
