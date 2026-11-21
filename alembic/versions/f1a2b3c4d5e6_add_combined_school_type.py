"""add combined school type

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-11-21 22:48:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("chk_schools_school_type", "schools", type_="check")
    op.create_check_constraint(
        "chk_schools_school_type",
        "schools",
        "school_type IN ('PRIMARY', 'SECONDARY', 'COMBINED')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_schools_school_type", "schools", type_="check")
    op.create_check_constraint(
        "chk_schools_school_type",
        "schools",
        "school_type IN ('PRIMARY', 'SECONDARY')",
    )
