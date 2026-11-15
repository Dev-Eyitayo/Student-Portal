"""m2 structure and student constraints

Revision ID: c3d8a4f61b72
Revises: b7c1f2a4d9e0
Create Date: 2026-11-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d8a4f61b72"
down_revision: str | None = "b7c1f2a4d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SOFT_RETIRED_TABLES = ("academic_levels", "departments", "class_arms")


def upgrade() -> None:
    for table_name in SOFT_RETIRED_TABLES:
        op.add_column(
            table_name,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("clock_timestamp()"),
                nullable=False,
            ),
        )
        op.add_column(
            table_name,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

    op.drop_constraint("uq_school_level_code", "academic_levels", type_="unique")
    op.drop_constraint("uq_school_level_order", "academic_levels", type_="unique")
    op.drop_constraint("uq_school_department_code", "departments", type_="unique")
    op.drop_constraint("uq_school_arm_name", "class_arms", type_="unique")
    op.drop_constraint("uq_school_session_name", "academic_sessions", type_="unique")
    op.drop_constraint("uq_session_term_name", "academic_terms", type_="unique")
    op.drop_constraint("uq_session_term_order", "academic_terms", type_="unique")
    op.drop_constraint("uq_class_group_composition", "class_groups", type_="unique")

    op.create_index(
        "uq_school_level_code_active",
        "academic_levels",
        ["school_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_school_level_order_active",
        "academic_levels",
        ["school_id", "level_order"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_school_department_code_active",
        "departments",
        ["school_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_school_arm_name_active",
        "class_arms",
        ["school_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_school_session_name_active",
        "academic_sessions",
        ["school_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_current_session_per_school",
        "academic_sessions",
        ["school_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_session_term_name_active",
        "academic_terms",
        ["academic_session_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_session_term_order_active",
        "academic_terms",
        ["academic_session_id", "term_order"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_current_term_per_school",
        "academic_terms",
        ["school_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_class_group_composition_with_dept",
        "class_groups",
        ["school_id", "academic_session_id", "academic_level_id", "department_id", "class_arm_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_class_group_composition_no_dept",
        "class_groups",
        ["school_id", "academic_session_id", "academic_level_id", "class_arm_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL AND deleted_at IS NULL"),
    )

    op.create_check_constraint(
        "chk_level_category",
        "academic_levels",
        "category IN ('JUNIOR_SECONDARY', 'SENIOR_SECONDARY')",
    )
    op.create_check_constraint("chk_term_order_positive", "academic_terms", "term_order > 0")
    op.create_check_constraint(
        "chk_student_status",
        "students",
        "status IN ('ADMITTED', 'ACTIVE', 'SUSPENDED', 'WITHDRAWN', 'TRANSFERRED', 'GRADUATED')",
    )
    op.create_check_constraint(
        "chk_enrollment_status",
        "student_enrollments",
        "enrollment_status IN ('ENROLLED', 'PROMOTED', 'REPEATING', 'TRANSFERRED', 'WITHDRAWN')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_enrollment_status", "student_enrollments", type_="check")
    op.drop_constraint("chk_student_status", "students", type_="check")
    op.drop_constraint("chk_term_order_positive", "academic_terms", type_="check")
    op.drop_constraint("chk_level_category", "academic_levels", type_="check")

    op.drop_index("uq_class_group_composition_no_dept", table_name="class_groups")
    op.drop_index("uq_class_group_composition_with_dept", table_name="class_groups")
    op.drop_index("uq_current_term_per_school", table_name="academic_terms")
    op.drop_index("uq_session_term_order_active", table_name="academic_terms")
    op.drop_index("uq_session_term_name_active", table_name="academic_terms")
    op.drop_index("uq_current_session_per_school", table_name="academic_sessions")
    op.drop_index("uq_school_session_name_active", table_name="academic_sessions")
    op.drop_index("uq_school_arm_name_active", table_name="class_arms")
    op.drop_index("uq_school_department_code_active", table_name="departments")
    op.drop_index("uq_school_level_order_active", table_name="academic_levels")
    op.drop_index("uq_school_level_code_active", table_name="academic_levels")

    op.create_unique_constraint(
        "uq_class_group_composition",
        "class_groups",
        ["academic_session_id", "academic_level_id", "department_id", "class_arm_id"],
    )
    op.create_unique_constraint(
        "uq_session_term_order",
        "academic_terms",
        ["academic_session_id", "term_order"],
    )
    op.create_unique_constraint(
        "uq_session_term_name",
        "academic_terms",
        ["academic_session_id", "name"],
    )
    op.create_unique_constraint(
        "uq_school_session_name",
        "academic_sessions",
        ["school_id", "name"],
    )
    op.create_unique_constraint("uq_school_arm_name", "class_arms", ["school_id", "name"])
    op.create_unique_constraint("uq_school_department_code", "departments", ["school_id", "code"])
    op.create_unique_constraint(
        "uq_school_level_order",
        "academic_levels",
        ["school_id", "level_order"],
    )
    op.create_unique_constraint(
        "uq_school_level_code",
        "academic_levels",
        ["school_id", "code"],
    )

    for table_name in reversed(SOFT_RETIRED_TABLES):
        op.drop_column(table_name, "deleted_at")
        op.drop_column(table_name, "updated_at")
