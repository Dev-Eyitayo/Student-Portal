"""enforce m2 historical foreign key delete rules

Revision ID: 9163fe5d4368
Revises: c3d8a4f61b72
Create Date: 2026-11-15 00:52:17.832330

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9163fe5d4368"
down_revision: str | None = "c3d8a4f61b72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


HISTORICAL_FOREIGN_KEYS = (
    (
        "academic_terms_academic_session_id_fkey",
        "academic_terms",
        "academic_sessions",
        ["academic_session_id"],
        ["id"],
    ),
    (
        "class_groups_academic_session_id_fkey",
        "class_groups",
        "academic_sessions",
        ["academic_session_id"],
        ["id"],
    ),
    (
        "student_enrollments_academic_session_id_fkey",
        "student_enrollments",
        "academic_sessions",
        ["academic_session_id"],
        ["id"],
    ),
    (
        "student_enrollments_student_id_fkey",
        "student_enrollments",
        "students",
        ["student_id"],
        ["id"],
    ),
)


def _replace_foreign_keys(ondelete: str) -> None:
    for name, table, referred_table, local_columns, remote_columns in HISTORICAL_FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name,
            table,
            referred_table,
            local_columns,
            remote_columns,
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_foreign_keys("RESTRICT")


def downgrade() -> None:
    _replace_foreign_keys("CASCADE")
