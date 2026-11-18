from typing import Any

SECONDARY_STRUCTURE_TEMPLATE_ID = "olivet_nigeria_jss_ss3"
PRIMARY_STRUCTURE_TEMPLATE_ID = "nigeria_primary_basic1_6"
STRUCTURE_TEMPLATE_ID = SECONDARY_STRUCTURE_TEMPLATE_ID

SECONDARY_LEVELS = [
    {"name": "JSS 1", "code": "JSS1", "level_order": 1, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 2", "code": "JSS2", "level_order": 2, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 3", "code": "JSS3", "level_order": 3, "category": "JUNIOR_SECONDARY"},
    {"name": "SS 1", "code": "SS1", "level_order": 4, "category": "SENIOR_SECONDARY"},
    {"name": "SS 2", "code": "SS2", "level_order": 5, "category": "SENIOR_SECONDARY"},
    {"name": "SS 3", "code": "SS3", "level_order": 6, "category": "SENIOR_SECONDARY"},
]

SECONDARY_DEPARTMENTS = [
    {"name": "Science", "code": "SCI"},
    {"name": "Arts", "code": "ART"},
    {"name": "Commercial", "code": "COMM"},
]

SECONDARY_ARMS = ["O", "L", "I", "V", "E", "T", "B", "H", "S", "Y"]

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

PRIMARY_SUBJECTS = [
    {"name": "English Language", "code": "ENG"},
    {"name": "Mathematics", "code": "MTH"},
    {"name": "Basic Science", "code": "BSC"},
    {"name": "Social Studies", "code": "SOS"},
    {"name": "Civic Education", "code": "CIV"},
    {"name": "Religious and Moral Education", "code": "RME"},
    {"name": "Physical and Health Education", "code": "PHE"},
    {"name": "Creative Arts", "code": "CRA"},
    {"name": "Computer Studies", "code": "CST"},
    {"name": "Nigerian Language", "code": "NLG"},
]


def _build_class_groups(
    levels: list[dict[str, Any]],
    departments: list[dict[str, Any]],
    arms: list[str],
) -> list[dict[str, Any]]:
    class_groups: list[dict[str, Any]] = []
    for level in levels:
        level_departments = (
            [] if level["category"] in ("JUNIOR_SECONDARY", "PRIMARY") else departments
        )
        for department in level_departments or [{"code": None}]:
            for arm in arms:
                class_groups.append(
                    {
                        "level_code": level["code"],
                        "department_code": department["code"],
                        "arm_name": arm,
                    }
                )
    return class_groups


def get_structure_template_preview(template_id: str) -> dict[str, Any] | None:
    if template_id == SECONDARY_STRUCTURE_TEMPLATE_ID:
        class_groups = _build_class_groups(SECONDARY_LEVELS, SECONDARY_DEPARTMENTS, SECONDARY_ARMS)
        return {
            "id": SECONDARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "SECONDARY",
            "name": "Olivet Nigeria JSS-SS3",
            "description": (
                "Six secondary levels, three senior departments, ten arms, and optional class groups."
            ),
            "levels": SECONDARY_LEVELS,
            "departments": SECONDARY_DEPARTMENTS,
            "arms": SECONDARY_ARMS,
            "subjects": [],
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    if template_id == PRIMARY_STRUCTURE_TEMPLATE_ID:
        class_groups = _build_class_groups(PRIMARY_LEVELS, PRIMARY_DEPARTMENTS, PRIMARY_ARMS)
        return {
            "id": PRIMARY_STRUCTURE_TEMPLATE_ID,
            "template_type": "PRIMARY",
            "name": "Nigeria Primary Basic 1-6",
            "description": (
                "Six primary levels, four arms, and compulsory core subjects with no departments."
            ),
            "levels": PRIMARY_LEVELS,
            "departments": PRIMARY_DEPARTMENTS,
            "arms": PRIMARY_ARMS,
            "subjects": PRIMARY_SUBJECTS,
            "class_group_count": len(class_groups),
            "class_groups": class_groups,
        }

    return None
