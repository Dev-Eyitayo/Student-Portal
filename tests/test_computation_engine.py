from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.modules.computation import engine

GRADING_SCALE = {
    "grades": [
        {
            "grade": "A1",
            "min_score": 75,
            "max_score": 100,
            "grade_point": 4,
            "remark": "Excellent",
        },
        {
            "grade": "F9",
            "min_score": 0,
            "max_score": 74.99,
            "grade_point": 0,
            "remark": "Fail",
        },
    ]
}

ANNUAL_CONFIG = {
    "version": 1,
    "term_weights": {"first_term": 1, "second_term": 1, "third_term": 1},
    "promotion_thresholds": {
        "promoted_min_percentage": "50.00",
        "trial_min_percentage": "40.00",
    },
}


@given(st.decimals(min_value=0, max_value=100, places=3))
def test_round_marks_uses_half_up(value: Decimal) -> None:
    result = engine.round_marks(value)
    assert result == value.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.decimals(min_value=0, max_value=100, places=2),
        min_size=1,
        max_size=8,
    )
)
def test_competition_ranks_match_strictly_greater_count(values: dict[str, Decimal]) -> None:
    ranks = engine.competition_ranks(values)
    for key, value in values.items():
        assert ranks[key] == 1 + sum(1 for other in values.values() if other > value)


@given(
    st.decimals(min_value=0, max_value=10, places=2),
    st.decimals(min_value=0, max_value=70, places=2),
)
def test_exempt_assessment_is_removed_from_denominator(
    test_score: Decimal, exam_score: Decimal
) -> None:
    assessments = (
        engine.AssessmentInput("TEST1", Decimal("10"), Decimal("10"), "SCORED", test_score),
        engine.AssessmentInput("EXAM", Decimal("70"), Decimal("70"), "SCORED", exam_score),
        engine.AssessmentInput("PRAC", Decimal("20"), Decimal("20"), "EXEMPT", None),
    )
    components = tuple(engine.compute_assessment(assessment) for assessment in assessments)
    total, obtainable = engine.compute_subject_totals(components, assessments)
    assert obtainable == Decimal("80.00")
    assert Decimal("0") <= total <= Decimal("100")


@given(
    st.decimals(min_value=0, max_value=10, places=2),
    st.decimals(min_value=0, max_value=70, places=2),
)
def test_absent_assessment_contributes_zero(test_score: Decimal, exam_score: Decimal) -> None:
    def totals(status: str, raw_score: Decimal | None) -> tuple[Decimal, Decimal]:
        assessments = (
            engine.AssessmentInput("TEST1", Decimal("10"), Decimal("10"), "SCORED", test_score),
            engine.AssessmentInput("EXAM", Decimal("70"), Decimal("70"), status, raw_score),
        )
        components = tuple(engine.compute_assessment(assessment) for assessment in assessments)
        return engine.compute_subject_totals(components, assessments)

    absent_total, absent_obtainable = totals("ABSENT", None)
    zero_total, zero_obtainable = totals("SCORED", Decimal("0"))
    scored_total, _ = totals("SCORED", exam_score)
    assert absent_total == zero_total
    assert absent_obtainable == zero_obtainable == Decimal("80.00")
    assert scored_total >= zero_total


@given(st.data())
def test_term_percentage_matches_aggregate_formula(data: st.DataObject) -> None:
    first = data.draw(st.decimals(min_value=0, max_value=100, places=2))
    second = data.draw(st.decimals(min_value=0, max_value=100, places=2))
    assessments = (engine.AssessmentInput("EXAM", Decimal("100"), Decimal("100"), "SCORED", first),)
    other = (engine.AssessmentInput("EXAM", Decimal("100"), Decimal("100"), "SCORED", second),)
    students = {
        "a": (
            engine.SubjectInput("math", "MTH", "Mathematics", assessments),
            engine.SubjectInput("eng", "ENG", "English", other),
        )
    }
    result = engine.compute_term_results(students, GRADING_SCALE).students["a"]
    expected = engine.round_marks((first + second) / Decimal("2"))
    assert result.percentage == expected


@given(
    st.decimals(min_value=0, max_value=100, places=2),
    st.decimals(min_value=0, max_value=100, places=2),
    st.decimals(min_value=0, max_value=100, places=2),
)
def test_annual_score_uses_configured_weights(
    first: Decimal, second: Decimal, third: Decimal
) -> None:
    result = engine.weighted_average(
        {"first_term": first, "second_term": second, "third_term": third}, ANNUAL_CONFIG
    )
    assert result == engine.round_marks((first + second + third) / Decimal("3"))


@given(st.decimals(min_value=0, max_value=100, places=2))
def test_promotion_decision_uses_configured_thresholds(percentage: Decimal) -> None:
    decision = engine.promotion_decision(percentage, ANNUAL_CONFIG)
    if percentage >= Decimal("50"):
        assert decision == "PROMOTED"
    elif percentage >= Decimal("40"):
        assert decision == "PROMOTED_ON_TRIAL"
    else:
        assert decision == "REPEATING"
