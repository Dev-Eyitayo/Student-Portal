"""enable row level security tenant isolation

Revision ID: b7c1f2a4d9e0
Revises: 165a34e29b40
Create Date: 2026-11-13

Enables PostgreSQL Row-Level Security (RLS) on tenant-scoped data tables.

The application connects as the table owner (``olivet_app``), and table owners
bypass RLS unless ``FORCE ROW LEVEL SECURITY`` is also set. Both are enabled
here. Tenant context is provided by the ``get_tenant_db`` dependency via
``set_config('app.current_school_id', ...)``; platform super admins set
``app.is_super_admin = 'true'``.

Platform/auth-lookup tables (``schools``, ``users``, ``school_configurations``,
``staff_profiles``, ``permissions``, ``role_default_permissions``) are
intentionally excluded because they must be readable before a tenant context
is known (login, invitation verification, school onboarding).
"""
from alembic import op

revision = "b7c1f2a4d9e0"
down_revision = "165a34e29b40"
branch_labels = None
depends_on = None


TENANT_TABLES = [
    "academic_sessions",
    "academic_terms",
    "academic_levels",
    "departments",
    "class_arms",
    "class_groups",
    "students",
    "student_enrollments",
    "subjects",
    "subject_offerings",
    "class_subjects",
    "student_subject_registrations",
    "teacher_assignments",
    "assessment_definitions",
    "assessment_scores",
    "subject_results",
    "term_report_cards",
    "result_access_tokens",
    "user_permission_grants",
    "audit_logs",
]


def _policy_expression() -> str:
    return (
        "school_id = NULLIF(current_setting('app.current_school_id', true), '')::uuid "
        "OR current_setting('app.is_super_admin', true) = 'true'"
    )


def upgrade() -> None:
    policy = _policy_expression()
    for table in TENANT_TABLES:
        policy_name = f"tenant_isolation_{table}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(
            f"CREATE POLICY {policy_name} ON {table} "
            f"FOR ALL USING ({policy}) WITH CHECK ({policy})"
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        policy_name = f"tenant_isolation_{table}"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
