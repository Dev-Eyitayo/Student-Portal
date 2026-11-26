"""add_trigram_search_indexes

Revision ID: a3c7e5f91b02
Revises: 92573906a81b
Create Date: 2026-11-26 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7e5f91b02"
down_revision: str | None = "92573906a81b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        "CREATE INDEX idx_students_trgm_admission_number "
        "ON students USING gin (admission_number gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_students_trgm_first_name ON students USING gin (first_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_students_trgm_middle_name "
        "ON students USING gin (middle_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_students_trgm_last_name ON students USING gin (last_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX idx_guardians_trgm_full_name ON guardians USING gin (full_name gin_trgm_ops)"
    )
    op.execute("CREATE INDEX idx_guardians_trgm_email ON guardians USING gin (email gin_trgm_ops)")
    op.execute("CREATE INDEX idx_guardians_trgm_phone ON guardians USING gin (phone gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_guardians_trgm_phone")
    op.execute("DROP INDEX IF EXISTS idx_guardians_trgm_email")
    op.execute("DROP INDEX IF EXISTS idx_guardians_trgm_full_name")
    op.execute("DROP INDEX IF EXISTS idx_students_trgm_last_name")
    op.execute("DROP INDEX IF EXISTS idx_students_trgm_middle_name")
    op.execute("DROP INDEX IF EXISTS idx_students_trgm_first_name")
    op.execute("DROP INDEX IF EXISTS idx_students_trgm_admission_number")
