"""school custom roles

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-21 08:18:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _policy_expression() -> str:
    return (
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
    )


def upgrade() -> None:
    op.create_table(
        "school_roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
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
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_school_roles_school_id", "school_roles", ["school_id"], unique=False)
    op.create_index(
        "uq_school_roles_school_code_active",
        "school_roles",
        ["school_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    policy = _policy_expression()
    op.execute("ALTER TABLE school_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE school_roles FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_school_roles ON school_roles")
    op.execute(
        f"CREATE POLICY tenant_isolation_school_roles ON school_roles FOR ALL USING ({policy}) WITH CHECK ({policy})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_school_roles ON school_roles")
    op.execute("ALTER TABLE school_roles DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_school_roles_school_code_active", table_name="school_roles")
    op.drop_index("ix_school_roles_school_id", table_name="school_roles")
    op.drop_table("school_roles")
