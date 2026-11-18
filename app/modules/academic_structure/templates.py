from typing import Any

STRUCTURE_TEMPLATE_ID = "olivet_nigeria_jss_ss3"

LEVELS = [
    {"name": "JSS 1", "code": "JSS1", "level_order": 1, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 2", "code": "JSS2", "level_order": 2, "category": "JUNIOR_SECONDARY"},
    {"name": "JSS 3", "code": "JSS3", "level_order": 3, "category": "JUNIOR_SECONDARY"},
    {"name": "SS 1", "code": "SS1", "level_order": 4, "category": "SENIOR_SECONDARY"},
    {"name": "SS 2", "code": "SS2", "level_order": 5, "category": "SENIOR_SECONDARY"},
    {"name": "SS 3", "code": "SS3", "level_order": 6, "category": "SENIOR_SECONDARY"},
]

DEPARTMENTS = [
    {"name": "Science", "code": "SCI"},
    {"name": "Arts", "code": "ART"},
    {"name": "Commercial", "code": "COMM"},
]

ARMS = ["O", "L", "I", "V", "E", "T", "B", "H", "S", "Y"]


def get_structure_template_preview(template_id: str) -> dict[str, Any] | None:
    if template_id != STRUCTURE_TEMPLATE_ID:
        return None

    class_groups: list[dict[str, Any]] = []
    for level in LEVELS:
        departments = [] if level["category"] == "JUNIOR_SECONDARY" else DEPARTMENTS
        for department in departments or [{"code": None}]:
            for arm in ARMS:
                class_groups.append(
                    {
                        "level_code": level["code"],
                        "department_code": department["code"],
                        "arm_name": arm,
                    }
                )

    return {
        "id": STRUCTURE_TEMPLATE_ID,
        "name": "Olivet Nigeria JSS-SS3",
        "description": (
            "Six secondary levels, three senior departments, ten arms, and optional class groups."
        ),
        "levels": LEVELS,
        "departments": DEPARTMENTS,
        "arms": ARMS,
        "class_group_count": len(class_groups),
        "class_groups": class_groups,
    }
