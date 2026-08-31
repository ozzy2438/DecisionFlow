"""Enforce completed event-store immutability in PostgreSQL.

Revision ID: 0005_immutable_event_store
Revises: 0004_unique_workflow_id
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_immutable_event_store"
down_revision: str | None = "0004_unique_workflow_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION decisionflow_guard_dataset_imports() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'running' THEN
                    RAISE EXCEPTION 'dataset imports must be created in running state';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status = 'completed' THEN
                RAISE EXCEPTION 'completed dataset import % is immutable', OLD.id;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dataset_imports_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON dataset_imports
        FOR EACH ROW EXECUTE FUNCTION decisionflow_guard_dataset_imports()
        """
    )

    op.execute(
        """
        CREATE FUNCTION decisionflow_guard_event_rows() RETURNS trigger AS $$
        DECLARE
            selected_import_id uuid;
            selected_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                selected_import_id := OLD.import_id;
            ELSE
                selected_import_id := NEW.import_id;
            END IF;

            SELECT status INTO selected_status
            FROM dataset_imports
            WHERE id = selected_import_id;

            IF selected_status = 'completed' THEN
                RAISE EXCEPTION 'completed event-store import % is immutable', selected_import_id;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("p2p_cases", "p2p_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION decisionflow_guard_event_rows()"
        )


def downgrade() -> None:
    for table in ("p2p_events", "p2p_cases"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS decisionflow_guard_event_rows()")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_imports_immutable ON dataset_imports")
    op.execute("DROP FUNCTION IF EXISTS decisionflow_guard_dataset_imports()")
