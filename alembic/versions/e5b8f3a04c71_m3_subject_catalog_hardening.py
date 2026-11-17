"""m3 subject catalog soft-delete and constraint hardening

Revision ID: e5b8f3a04c71
Revises: d4a7e2f91c36
Create Date: 2026-11-17 00:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b8f3a04c71"
down_revision: str | None = "d4a7e2f91c36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_timestamp_columns(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def _drop_timestamp_columns(table: str) -> None:
    op.drop_column(table, "deleted_at")
    op.drop_column(table, "updated_at")


def upgrade() -> None:
    # subjects: soft-delete support
    _add_timestamp_columns("subjects")
    op.drop_constraint("uq_school_subject_code", "subjects", type_="unique")
    op.create_index(
        "uq_school_subject_code_active",
        "subjects",
        ["school_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # subject_offerings: soft-delete support + fix the NULL-department uniqueness hole
    _add_timestamp_columns("subject_offerings")
    op.drop_constraint("uq_offering_unique", "subject_offerings", type_="unique")
    op.create_index(
        "uq_offering_no_dept_active",
        "subject_offerings",
        ["subject_id", "academic_session_id", "academic_level_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_offering_with_dept_active",
        "subject_offerings",
        ["subject_id", "academic_session_id", "academic_level_id", "department_id"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL AND deleted_at IS NULL"),
    )

    # class_subjects: soft-delete support
    _add_timestamp_columns("class_subjects")
    op.drop_constraint("uq_class_subject", "class_subjects", type_="unique")
    op.create_index(
        "uq_class_subject_active",
        "class_subjects",
        ["subject_offering_id", "class_group_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # teacher_assignments: a teacher can be reassigned to the same class subject
    # after a prior assignment ended, so uniqueness must only apply while active.
    op.drop_constraint("uq_active_teacher_class_subject", "teacher_assignments", type_="unique")
    op.create_index(
        "uq_active_teacher_class_subject",
        "teacher_assignments",
        ["class_subject_id", "teacher_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.drop_constraint(
        "teacher_assignments_class_subject_id_fkey", "teacher_assignments", type_="foreignkey"
    )
    op.create_foreign_key(
        "teacher_assignments_class_subject_id_fkey",
        "teacher_assignments",
        "class_subjects",
        ["class_subject_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # student_subject_registrations: enum guard + preserve registration history
    # when a class subject is retired instead of silently cascading it away.
    op.create_check_constraint(
        "chk_subject_category",
        "student_subject_registrations",
        "subject_category IN ('COMPULSORY_CORE', 'DEPARTMENT_CORE', 'TRADE_ELECTIVE', "
        "'FREE_ELECTIVE')",
    )
    op.drop_constraint(
        "student_subject_registrations_class_subject_id_fkey",
        "student_subject_registrations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "student_subject_registrations_class_subject_id_fkey",
        "student_subject_registrations",
        "class_subjects",
        ["class_subject_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "student_subject_registrations_class_subject_id_fkey",
        "student_subject_registrations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "student_subject_registrations_class_subject_id_fkey",
        "student_subject_registrations",
        "class_subjects",
        ["class_subject_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("chk_subject_category", "student_subject_registrations", type_="check")

    op.drop_constraint(
        "teacher_assignments_class_subject_id_fkey", "teacher_assignments", type_="foreignkey"
    )
    op.create_foreign_key(
        "teacher_assignments_class_subject_id_fkey",
        "teacher_assignments",
        "class_subjects",
        ["class_subject_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("uq_active_teacher_class_subject", table_name="teacher_assignments")
    op.create_unique_constraint(
        "uq_active_teacher_class_subject",
        "teacher_assignments",
        ["class_subject_id", "teacher_id", "is_active"],
    )

    op.drop_index("uq_class_subject_active", table_name="class_subjects")
    op.create_unique_constraint(
        "uq_class_subject", "class_subjects", ["subject_offering_id", "class_group_id"]
    )
    _drop_timestamp_columns("class_subjects")

    op.drop_index("uq_offering_with_dept_active", table_name="subject_offerings")
    op.drop_index("uq_offering_no_dept_active", table_name="subject_offerings")
    op.create_unique_constraint(
        "uq_offering_unique",
        "subject_offerings",
        ["subject_id", "academic_session_id", "academic_level_id", "department_id"],
    )
    _drop_timestamp_columns("subject_offerings")

    op.drop_index("uq_school_subject_code_active", table_name="subjects")
    op.create_unique_constraint("uq_school_subject_code", "subjects", ["school_id", "code"])
    _drop_timestamp_columns("subjects")
