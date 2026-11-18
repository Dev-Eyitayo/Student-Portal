"""m9 primary school support

Revision ID: b1c4d7e9a2f0
Revises: a7c3e9f2b6d4
Create Date: 2026-11-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c4d7e9a2f0"
down_revision: str | None = "a7c3e9f2b6d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column("school_type", sa.String(length=16), server_default="SECONDARY", nullable=False),
    )
    op.create_check_constraint(
        "chk_schools_school_type",
        "schools",
        "school_type IN ('PRIMARY', 'SECONDARY')",
    )

    op.drop_constraint("chk_level_category", "academic_levels", type_="check")
    op.create_check_constraint(
        "chk_level_category",
        "academic_levels",
        "category IN ('PRIMARY', 'JUNIOR_SECONDARY', 'SENIOR_SECONDARY')",
    )


def downgrade() -> None:
    # NOTE: Downgrading requires no active rows with category = 'PRIMARY' in academic_levels;
    # otherwise PostgreSQL will raise CheckViolationError on chk_level_category and roll back.
    op.drop_constraint("chk_level_category", "academic_levels", type_="check")
    op.create_check_constraint(
        "chk_level_category",
        "academic_levels",
        "category IN ('JUNIOR_SECONDARY', 'SENIOR_SECONDARY')",
    )

    op.drop_constraint("chk_schools_school_type", "schools", type_="check")
    op.drop_column("schools", "school_type")
