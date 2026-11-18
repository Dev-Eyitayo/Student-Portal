"""m5 computation snapshots and published record immutability

Revision ID: a7c3e9f2b6d4
Revises: f29a6d4e8b03
Create Date: 2026-11-18 00:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9f2b6d4"
down_revision: str | None = "f29a6d4e8b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "school_configurations",
        sa.Column("annual_results_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE school_configurations
        SET annual_results_schema = '{
          "version": 1,
          "term_weights": {
            "first_term": 1,
            "second_term": 1,
            "third_term": 1
          },
          "promotion_thresholds": {
            "promoted_min_percentage": "50.00",
            "trial_min_percentage": "40.00"
          }
        }'::jsonb
        WHERE annual_results_schema IS NULL
        """
    )
    op.alter_column("school_configurations", "annual_results_schema", nullable=False)

    op.add_column(
        "subject_results",
        sa.Column("frozen_config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE subject_results
        SET frozen_config_snapshot = '{}'::jsonb
        WHERE frozen_config_snapshot IS NULL AND status != 'PUBLISHED'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM subject_results
            WHERE frozen_config_snapshot IS NULL AND status = 'PUBLISHED'
          ) THEN
            RAISE EXCEPTION 'Found PUBLISHED subject_results rows with no frozen_config_snapshot. '
              'Backfill their real historical configuration manually before running this migration.';
          END IF;
        END $$;
        """
    )
    op.alter_column("subject_results", "frozen_config_snapshot", nullable=False)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_published_result_mutations()
        RETURNS trigger AS $$
        DECLARE
          is_published boolean;
          old_rest jsonb;
          new_rest jsonb;
        BEGIN
          IF TG_TABLE_NAME = 'subject_results' THEN
            is_published := (OLD.status = 'PUBLISHED');
          ELSE
            is_published := (OLD.publication_status = 'PUBLISHED');
          END IF;

          IF NOT is_published THEN
            IF TG_OP = 'DELETE' THEN
              RETURN OLD;
            END IF;
            RETURN NEW;
          END IF;

          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Published results are frozen and cannot be deleted'
              USING ERRCODE = 'check_violation';
          END IF;

          -- Allow a soft-delete (deleted_at NULL -> timestamp) with every other column
          -- unchanged; block any other mutation to a published row.
          old_rest := to_jsonb(OLD) - 'deleted_at' - 'updated_at';
          new_rest := to_jsonb(NEW) - 'deleted_at' - 'updated_at';
          IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL AND old_rest = new_rest THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'Published results are frozen and cannot be changed'
            USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_subject_results_published_immutable
        BEFORE UPDATE OR DELETE ON subject_results
        FOR EACH ROW EXECUTE FUNCTION prevent_published_result_mutations()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_term_report_cards_published_immutable
        BEFORE UPDATE OR DELETE ON term_report_cards
        FOR EACH ROW EXECUTE FUNCTION prevent_published_result_mutations()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_term_report_cards_published_immutable ON term_report_cards"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_subject_results_published_immutable ON subject_results")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_result_mutations()")
    op.drop_column("subject_results", "frozen_config_snapshot")
    op.drop_column("school_configurations", "annual_results_schema")
