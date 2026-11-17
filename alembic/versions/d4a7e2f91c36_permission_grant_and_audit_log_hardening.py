"""permission grant soft-delete and audit log fk hardening

Revision ID: d4a7e2f91c36
Revises: 9163fe5d4368
Create Date: 2026-11-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a7e2f91c36"
down_revision: str | None = "9163fe5d4368"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("audit_logs_school_id_fkey", "audit_logs", type_="foreignkey")
    op.create_foreign_key(
        "audit_logs_school_id_fkey",
        "audit_logs",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "user_permission_grants",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_permission_grants",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("uq_user_permission_grant", "user_permission_grants", type_="unique")
    op.create_index(
        "uq_user_permission_grant_active",
        "user_permission_grants",
        [
            "user_id",
            "permission_id",
            "scope_type",
            "department_id",
            "academic_level_id",
            "class_group_id",
        ],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_permission_grant_active", table_name="user_permission_grants")
    op.create_unique_constraint(
        "uq_user_permission_grant",
        "user_permission_grants",
        [
            "user_id",
            "permission_id",
            "scope_type",
            "department_id",
            "academic_level_id",
            "class_group_id",
        ],
    )
    op.drop_column("user_permission_grants", "deleted_at")
    op.drop_column("user_permission_grants", "updated_at")

    op.drop_constraint("audit_logs_school_id_fkey", "audit_logs", type_="foreignkey")
    op.create_foreign_key(
        "audit_logs_school_id_fkey",
        "audit_logs",
        "schools",
        ["school_id"],
        ["id"],
        ondelete="CASCADE",
    )
