"""m4 score sheet draft-submitted-approved lifecycle table

Revision ID: f29a6d4e8b03
Revises: e5b8f3a04c71
Create Date: 2026-11-17 00:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f29a6d4e8b03"
down_revision: str | None = "e5b8f3a04c71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_sheet_statuses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("academic_term_id", sa.Uuid(), nullable=False),
        sa.Column("class_subject_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("submitted_by_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlocked_by_id", sa.Uuid(), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlock_reason", sa.String(length=500), nullable=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED')", name="chk_score_sheet_status"
        ),
        sa.ForeignKeyConstraint(["academic_term_id"], ["academic_terms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["class_subject_id"], ["class_subjects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["unlocked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("academic_term_id", "class_subject_id", name="uq_score_sheet_status"),
    )
    op.create_index(
        op.f("ix_score_sheet_statuses_school_id"),
        "score_sheet_statuses",
        ["school_id"],
        unique=False,
    )

    op.execute("ALTER TABLE score_sheet_statuses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE score_sheet_statuses FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_score_sheet_statuses ON score_sheet_statuses "
        "FOR ALL USING ("
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
        ") WITH CHECK ("
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
        ")"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_score_sheet_statuses ON score_sheet_statuses"
    )
    op.execute("ALTER TABLE score_sheet_statuses NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE score_sheet_statuses DISABLE ROW LEVEL SECURITY")

    op.drop_index(op.f("ix_score_sheet_statuses_school_id"), table_name="score_sheet_statuses")
    op.drop_table("score_sheet_statuses")
