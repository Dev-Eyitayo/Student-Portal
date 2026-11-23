import string
from typing import Any

SECONDARY_STRUCTURE_TEMPLATE_ID = "olivet_nigeria_jss_ss3"
GENERAL_SECONDARY_STRUCTURE_TEMPLATE_ID = "nigeria_secondary_jss_ss3"
JUNIOR_SECONDARY_STRUCTURE_TEMPLATE_ID = "nigeria_junior_secondary"
SENIOR_SECONDARY_STRUCTURE_TEMPLATE_ID = "nigeria_senior_secondary"
PRIMARY_STRUCTURE_TEMPLATE_ID = "nigeria_primary_basic1_6"
COMBINED_STRUCTURE_TEMPLATE_ID = "nigeria_combined_k12"
STRUCTURE_TEMPLATE_ID = SECONDARY_STRUCTURE_TEMPLATE_ID

JUNIOR_SECONDARY_LEVELS = [
    {"name": "JSS 1", "code": "JSS1", "level_order": 1, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 2", "code": "JSS2", "level_order": 2, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 3", "code": "JSS3", "level_order": 3, "category": "JUNIOR_SECONDARY"},
]

SENIOR_SECONDARY_LEVELS = [
    {"name": "SS 1", "code": "SS1", "level_order": 4, "category": "SENIOR_SECONDARY"},
    {"name": "SS 2", "code": "SS2", "level_order": 5, "category": "SENIOR_SECONDARY"},
    {"name": "SS 3", "code": "SS3", "level_order": 6, "category": "SENIOR_SECONDARY"},
]

SECONDARY_LEVELS = [*JUNIOR_SECONDARY_LEVELS, *SENIOR_SECONDARY_LEVELS]

SECONDARY_DEPARTMENTS = [
    {"name": "Science", "code": "SCI"},
    {"name": "Arts", "code": "ART"},
    {"name": "Commercial", "code": "COMM"},
]

# Olivet-specific arm letters preserved for the Olivet pilot template & regression tests
SECONDARY_ARMS = ["O", "L", "I", "V", "E", "T", "B", "H", "S", "Y"]
DEFAULT_INCREMENTAL_ARMS = ["A", "B", "C"]

PRIMARY_LEVELS = [
    {"name": "Basic 1", "code": "BASIC1", "level_order": 1, "category": "PRIMARY"},
    {"name": "Basic 2", "code": "BASIC2", "level_order": 2, "category": "PRIMARY"},
    {"name": "Basic 3", "code": "BASIC3", "level_order": 3, "category": "PRIMARY"},
    {"name": "Basic 4", "code": "BASIC4", "level_order": 4, "category": "PRIMARY"},
    {"name": "Basic 5", "code": "BASIC5", "level_order": 5, "category": "PRIMARY"},
    {"name": "Basic 6", "code": "BASIC6", "level_order": 6, "category": "PRIMARY"},
]

PRIMARY_DEPARTMENTS: list[dict[str, Any]] = []
PRIMARY_ARMS = ["A", "B", "C", "D"]

# Nigerian NERDC Curriculum Subjects by level category & department
PRIMARY_SUBJECTS: list[dict[str, Any]] = [
    {
        "name": "English Studies",
        "code": "ENG",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Mathematics",
        "code": "MTH",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Basic Science & Technology",
        "code": "BST",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "National Values (Social & Civic)",
        "code": "NVE",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Cultural & Creative Arts",
        "code": "CCA",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Pre-Vocational Studies",
        "code": "PVS",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Physical & Health Education",
        "code": "PHE",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Computer Studies / ICT",
        "code": "CMP",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Christian Religious Studies",
        "code": "CRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Islamic Religious Studies",
        "code": "IRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": False,
    },
    {
        "name": "Yoruba Language",
        "code": "YOR",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Quantitative & Verbal Reasoning",
        "code": "QVR",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "French",
        "code": "FRN",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["PRIMARY"],
        "department_codes": None,
        "default_selected": False,
    },
]

JUNIOR_SECONDARY_SUBJECTS: list[dict[str, Any]] = [
    {
        "name": "English Language",
        "code": "ENG",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Mathematics",
        "code": "MTH",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Basic Science",
        "code": "BSC",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Basic Technology",
        "code": "BTC",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Business Studies",
        "code": "BUS",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Social Studies",
        "code": "SOS",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Civic Education",
        "code": "CIV",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Security Education",
        "code": "SEC",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Agricultural Science",
        "code": "AGR",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Home Economics",
        "code": "HEC",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Physical & Health Education",
        "code": "PHE",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Cultural & Creative Arts",
        "code": "CCA",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Computer Studies",
        "code": "CMP",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Christian Religious Studies",
        "code": "CRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "Islamic Religious Studies",
        "code": "IRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": False,
    },
    {
        "name": "Yoruba Language",
        "code": "YOR",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": True,
    },
    {
        "name": "French",
        "code": "FRN",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["JUNIOR_SECONDARY"],
        "department_codes": None,
        "default_selected": False,
    },
]

SENIOR_SECONDARY_SUBJECTS: list[dict[str, Any]] = [
    # General Compulsory Core
    {
        "name": "English Language",
        "code": "ENG",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
    {
        "name": "Mathematics",
        "code": "MTH",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
    {
        "name": "Civic Education",
        "code": "CIV",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
    # Science Core & Electives
    {
        "name": "Physics",
        "code": "PHY",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": True,
    },
    {
        "name": "Chemistry",
        "code": "CHM",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": True,
    },
    {
        "name": "Biology",
        "code": "BIO",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": True,
    },
    {
        "name": "Further Mathematics",
        "code": "FMA",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": True,
    },
    {
        "name": "Agricultural Science",
        "code": "AGR",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": True,
    },
    {
        "name": "Geography",
        "code": "GEO",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART"],
        "default_selected": True,
    },
    {
        "name": "Technical Drawing",
        "code": "TDW",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI"],
        "default_selected": False,
    },
    # Arts Core & Electives
    {
        "name": "Literature-in-English",
        "code": "LIT",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART"],
        "default_selected": True,
    },
    {
        "name": "Government",
        "code": "GOV",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART", "COMM"],
        "default_selected": True,
    },
    {
        "name": "History",
        "code": "HIS",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART"],
        "default_selected": True,
    },
    {
        "name": "Christian Religious Studies",
        "code": "CRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART"],
        "default_selected": True,
    },
    {
        "name": "Islamic Religious Studies",
        "code": "IRS",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART"],
        "default_selected": False,
    },
    {
        "name": "Yoruba Language",
        "code": "YOR",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART"],
        "default_selected": True,
    },
    # Commercial Core & Electives
    {
        "name": "Financial Accounting",
        "code": "ACC",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["COMM"],
        "default_selected": True,
    },
    {
        "name": "Commerce",
        "code": "COM",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["COMM"],
        "default_selected": True,
    },
    {
        "name": "Economics",
        "code": "ECO",
        "is_elective": False,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["COMM", "ART"],
        "default_selected": True,
    },
    {
        "name": "Book Keeping",
        "code": "BKM",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["COMM"],
        "default_selected": True,
    },
    {
        "name": "Office Practice",
        "code": "OFP",
        "is_elective": True,
        "is_trade_subject": False,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["COMM"],
        "default_selected": False,
    },
    # Trade / Entrepreneurship Subjects
    {
        "name": "Data Processing",
        "code": "DPR",
        "is_elective": True,
        "is_trade_subject": True,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
    {
        "name": "Catering Craft Practice",
        "code": "CAT",
        "is_elective": True,
        "is_trade_subject": True,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
    {
        "name": "Garment Making",
        "code": "GAR",
        "is_elective": True,
        "is_trade_subject": True,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["ART", "COMM"],
        "default_selected": False,
    },
    {
        "name": "Fisheries",
        "code": "FIS",
        "is_elective": True,
        "is_trade_subject": True,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "COMM"],
        "default_selected": False,
    },
    {
        "name": "Marketing",
        "code": "MKT",
        "is_elective": True,
        "is_trade_subject": True,
        "categories": ["SENIOR_SECONDARY"],
        "department_codes": ["SCI", "ART", "COMM"],
        "default_selected": True,
    },
]


def _incremental_arms(count: int) -> list[str]:
    clamped = max(1, min(26, count))
    return list(string.ascii_uppercase[:clamped])


def _merge_subjects(*catalogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for catalog in catalogs:
        for item in catalog:
            code = item["code"]
            if code not in by_code:
                by_code[code] = {
                    **item,
                    "categories": list(item.get("categories") or []),
                    "department_codes": (
                        list(item["department_codes"]) if item.get("department_codes") else None
                    ),
                }
            else:
                existing = by_code[code]
                for cat in item.get("categories") or []:
                    if cat not in existing["categories"]:
                        existing["categories"].append(cat)
                if item.get("department_codes"):
                    if existing["department_codes"] is None:
                        existing["department_codes"] = list(item["department_codes"])
                    else:
                        for dc in item["department_codes"]:
                            if dc not in existing["department_codes"]:
                                existing["department_codes"].append(dc)
    return list(by_code.values())


def _build_class_groups(
    levels: list[dict[str, Any]],
    departments: list[dict[str, Any]],
    arms: list[str],
    *,
    department_arm_counts: dict[str, int] | None = None,
    level_arm_counts: dict[str, int] | None = None,
    level_department_arm_counts: dict[str, dict[str, int]] | None = None,
    separate_senior_departments: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    class_groups: list[dict[str, Any]] = []
    all_arms_used: list[str] = []

    for level in levels:
        level_code = level["code"]
        level_departments = (
            []
            if (
                not separate_senior_departments
                or level["category"] in ("JUNIOR_SECONDARY", "PRIMARY")
            )
            else departments
        )
        for department in level_departments or [{"code": None}]:
            dept_code = department["code"]
            if dept_code:
                dept_specific = (
                    (level_department_arm_counts or {}).get(level_code, {}).get(dept_code)
                )
                if dept_specific is not None:
                    dept_arms = _incremental_arms(dept_specific)
                elif department_arm_counts and dept_code in department_arm_counts:
                    dept_arms = _incremental_arms(department_arm_counts[dept_code])
                elif level_arm_counts and level_code in level_arm_counts:
                    dept_arms = _incremental_arms(level_arm_counts[level_code])
                else:
                    dept_arms = arms
            elif level_arm_counts and level_code in level_arm_counts:
                dept_arms = _incremental_arms(level_arm_counts[level_code])
            else:
                dept_arms = arms
            for arm in dept_arms:
                if arm not in all_arms_used:
                    all_arms_used.append(arm)
                class_groups.append(
                    {
                        "level_code": level_code,
                        "department_code": dept_code,
                        "arm_name": arm,
                    }
                )
    return class_groups, all_arms_used


def get_structure_template_preview(
    template_id: str,
    *,
    arms_per_level: int | None = None,
    department_arm_counts: dict[str, int] | None = None,
    level_arm_counts: dict[str, int] | None = None,
    level_department_arm_counts: dict[str, dict[str, int]] | None = None,
    separate_senior_departments: bool = True,
) -> dict[str, Any] | None:
    effective_departments = SECONDARY_DEPARTMENTS if separate_senior_departments else []
    if template_id == SECONDARY_STRUCTURE_TEMPLATE_ID:
        base_arms = _incremental_arms(arms_per_level) if arms_per_level else SECONDARY_ARMS
        class_groups, used_arms = _build_class_groups(
            SECONDARY_LEVELS,
            effective_departments,
            base_arms,
            department_arm_counts=department_arm_counts,
            level_arm_counts=level_arm_counts,
            level_department_arm_counts=level_department_arm_counts,
            separate_senior_departments=separate_senior_departments,
        )
        return {
            "id": SECONDARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "SECONDARY",
            "name": "Olivet Nigeria JSS-SS3",
            "description": (
                "Six secondary levels, three senior departments, ten arms, and optional class groups."
            ),
            "levels": SECONDARY_LEVELS,
            "departments": effective_departments,
            "arms": used_arms,
            "subjects": _merge_subjects(JUNIOR_SECONDARY_SUBJECTS, SENIOR_SECONDARY_SUBJECTS),
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == GENERAL_SECONDARY_STRUCTURE_TEMPLATE_ID:
        base_arms = (
            _incremental_arms(arms_per_level) if arms_per_level else DEFAULT_INCREMENTAL_ARMS
        )
        class_groups, used_arms = _build_class_groups(
            SECONDARY_LEVELS,
            effective_departments,
            base_arms,
            department_arm_counts=department_arm_counts,
            level_arm_counts=level_arm_counts,
            level_department_arm_counts=level_department_arm_counts,
            separate_senior_departments=separate_senior_departments,
        )
        return {
            "id": GENERAL_SECONDARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "SECONDARY",
            "name": "Nigeria Secondary (JSS 1-SS 3)",
            "description": (
                "Six secondary levels, three senior departments, incremental A, B, C... arms, "
                "and NERDC subjects."
            ),
            "levels": SECONDARY_LEVELS,
            "departments": effective_departments,
            "arms": used_arms,
            "subjects": _merge_subjects(JUNIOR_SECONDARY_SUBJECTS, SENIOR_SECONDARY_SUBJECTS),
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == JUNIOR_SECONDARY_STRUCTURE_TEMPLATE_ID:
        base_arms = (
            _incremental_arms(arms_per_level) if arms_per_level else DEFAULT_INCREMENTAL_ARMS
        )
        class_groups, used_arms = _build_class_groups(
            JUNIOR_SECONDARY_LEVELS,
            [],
            base_arms,
            level_arm_counts=level_arm_counts,
        )
        return {
            "id": JUNIOR_SECONDARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "SECONDARY",
            "name": "Nigeria Junior Secondary (JSS 1-3)",
            "description": (
                "Three junior secondary levels with incremental A, B, C... arms and NERDC subjects."
            ),
            "levels": JUNIOR_SECONDARY_LEVELS,
            "departments": [],
            "arms": used_arms,
            "subjects": JUNIOR_SECONDARY_SUBJECTS,
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == SENIOR_SECONDARY_STRUCTURE_TEMPLATE_ID:
        senior_levels = [
            {**lvl, "level_order": idx + 1} for idx, lvl in enumerate(SENIOR_SECONDARY_LEVELS)
        ]
        base_arms = (
            _incremental_arms(arms_per_level) if arms_per_level else DEFAULT_INCREMENTAL_ARMS
        )
        class_groups, used_arms = _build_class_groups(
            senior_levels,
            effective_departments,
            base_arms,
            department_arm_counts=department_arm_counts,
            level_arm_counts=level_arm_counts,
            level_department_arm_counts=level_department_arm_counts,
            separate_senior_departments=separate_senior_departments,
        )
        return {
            "id": SENIOR_SECONDARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "SECONDARY",
            "name": "Nigeria Senior Secondary (SS 1-3)",
            "description": (
                "Three senior secondary levels, Science/Arts/Commercial departments, incremental A, B, C... arms, and NERDC subjects."
            ),
            "levels": senior_levels,
            "departments": effective_departments,
            "arms": used_arms,
            "subjects": SENIOR_SECONDARY_SUBJECTS,
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == PRIMARY_STRUCTURE_TEMPLATE_ID:
        base_arms = (
            _incremental_arms(arms_per_level) if arms_per_level else DEFAULT_INCREMENTAL_ARMS
        )
        class_groups, used_arms = _build_class_groups(
            PRIMARY_LEVELS,
            PRIMARY_DEPARTMENTS,
            base_arms,
            level_arm_counts=level_arm_counts,
        )
        return {
            "id": PRIMARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "PRIMARY",
            "name": "Nigeria Primary Basic 1-6",
            "description": (
                "Six primary levels, incremental A, B, C... arms, and Nigerian primary core subjects."
            ),
            "levels": PRIMARY_LEVELS,
            "departments": PRIMARY_DEPARTMENTS,
            "arms": used_arms,
            "subjects": PRIMARY_SUBJECTS,
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == COMBINED_STRUCTURE_TEMPLATE_ID:
        combined_levels = [
            *PRIMARY_LEVELS,
            *[
                {**lvl, "level_order": len(PRIMARY_LEVELS) + idx + 1}
                for idx, lvl in enumerate(SECONDARY_LEVELS)
            ],
        ]
        base_arms = (
            _incremental_arms(arms_per_level) if arms_per_level else DEFAULT_INCREMENTAL_ARMS
        )
        class_groups, used_arms = _build_class_groups(
            combined_levels,
            effective_departments,
            base_arms,
            department_arm_counts=department_arm_counts,
            level_arm_counts=level_arm_counts,
            level_department_arm_counts=level_department_arm_counts,
            separate_senior_departments=separate_senior_departments,
        )
        return {
            "id": COMBINED_STRUCTURE_TEMPLATE_ID,
            "template_type": "COMBINED",
            "name": "Nigeria Combined (Primary + JSS 1-SS 3)",
            "description": (
                "Primary (Basic 1-6), Junior Secondary (JSS 1-3), and Senior Secondary (SS 1-3) with incremental arms and NERDC subjects."
            ),
            "levels": combined_levels,
            "departments": effective_departments,
            "arms": used_arms,
            "subjects": _merge_subjects(
                PRIMARY_SUBJECTS, JUNIOR_SECONDARY_SUBJECTS, SENIOR_SECONDARY_SUBJECTS
            ),
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    return None
