"""school role permissions

Revision ID: d8e9f0a1b2c3
Revises: c2d5e8f1a3b4
Create Date: 2026-11-21 08:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c2d5e8f1a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _policy_expression() -> str:
    return (
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
    )


def upgrade() -> None:
    op.create_table(
        "school_role_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "school_id",
            "role",
            "permission_id",
            name="uq_school_role_permissions_school_role_perm",
        ),
    )
    op.create_index(
        "ix_school_role_permissions_school_id",
        "school_role_permissions",
        ["school_id"],
        unique=False,
    )

    policy = _policy_expression()
    op.execute("ALTER TABLE school_role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE school_role_permissions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_school_role_permissions ON school_role_permissions")
    op.execute(
        f"CREATE POLICY tenant_isolation_school_role_permissions ON school_role_permissions FOR ALL USING ({policy}) WITH CHECK ({policy})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_school_role_permissions ON school_role_permissions")
    op.execute("ALTER TABLE school_role_permissions DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_school_role_permissions_school_id", table_name="school_role_permissions")
    op.drop_table("school_role_permissions")
