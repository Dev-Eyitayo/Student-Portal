"""m5a attendance records and summaries

Revision ID: c2d5e8f1a3b4
Revises: b1c4d7e9a2f0
Create Date: 2026-11-19 01:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2d5e8f1a3b4"
down_revision: str | None = "b1c4d7e9a2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTENDANCE_TABLES = ("attendance_records", "attendance_summaries")


def _policy_expression() -> str:
    return (
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
    )


def upgrade() -> None:
    op.create_table(
        "attendance_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("student_enrollment_id", sa.UUID(), nullable=False),
        sa.Column("academic_term_id", sa.UUID(), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("recorded_by_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["student_enrollment_id"], ["student_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["academic_term_id"], ["academic_terms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_enrollment_id",
            "attendance_date",
            name="uq_attendance_enrollment_date",
        ),
        sa.CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'LATE')",
            name="chk_attendance_record_status",
        ),
    )
    op.create_index(
        "idx_attendance_records_term",
        "attendance_records",
        ["school_id", "academic_term_id", "attendance_date"],
    )

    op.create_table(
        "attendance_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("student_enrollment_id", sa.UUID(), nullable=False),
        sa.Column("academic_term_id", sa.UUID(), nullable=False),
        sa.Column("times_school_opened", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("times_present", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("times_absent", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("source", sa.String(length=16), server_default="DAILY", nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("updated_by_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["student_enrollment_id"], ["student_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["academic_term_id"], ["academic_terms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_enrollment_id",
            "academic_term_id",
            name="uq_attendance_summary_enrollment_term",
        ),
        sa.CheckConstraint(
            "source IN ('DAILY', 'MANUAL')",
            name="chk_attendance_summary_source",
        ),
        sa.CheckConstraint(
            "times_school_opened >= 0 AND times_present >= 0 AND times_absent >= 0",
            name="chk_attendance_summary_non_negative",
        ),
    )
    op.create_index(
        "idx_attendance_summaries_term",
        "attendance_summaries",
        ["school_id", "academic_term_id"],
    )

    policy = _policy_expression()
    for table in ATTENDANCE_TABLES:
        policy_name = f"tenant_isolation_{table}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(
            f"CREATE POLICY {policy_name} ON {table} FOR ALL USING ({policy}) WITH CHECK ({policy})"
        )


def downgrade() -> None:
    for table in ATTENDANCE_TABLES:
        policy_name = f"tenant_isolation_{table}"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("idx_attendance_summaries_term", table_name="attendance_summaries")
    op.drop_table("attendance_summaries")
    op.drop_index("idx_attendance_records_term", table_name="attendance_records")
    op.drop_table("attendance_records")
