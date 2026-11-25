"""allow_parallel_electives_in_same_class_slot

Revision ID: 847d221c0db2
Revises: f4b6ff7c0445
Create Date: 2026-11-23 22:32:09.870869

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "847d221c0db2"
down_revision: str | None = "f4b6ff7c0445"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_timetable_class_slot_active",
        table_name="timetable_entries",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_timetable_class_slot_active",
        "timetable_entries",
        ["timetable_id", "class_group_id", "class_subject_id", "timetable_slot_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_timetable_class_slot_active",
        table_name="timetable_entries",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_timetable_class_slot_active",
        "timetable_entries",
        ["timetable_id", "class_group_id", "timetable_slot_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
