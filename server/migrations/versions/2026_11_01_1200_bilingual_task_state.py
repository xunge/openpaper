"""move bilingual jobs to Celery

Revision ID: f4b2c8d1e930
Revises: c3d7f1a8b942
Create Date: 2026-11-01 12:00:00.000000+00:00

The pipeline now runs in the jobs worker, so the document needs to remember which
Celery task owns it (to revoke on delete and to ignore events from a superseded
run) and where it is inside the pipeline (render / ocr / translate / assemble).

The paper_id uniqueness is relaxed to (paper_id, user_id): a paper shared into a
project is read by several users, each of whom can generate their own bilingual
document.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4b2c8d1e930"
down_revision: Union[str, None] = "c3d7f1a8b942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("paper_documents", sa.Column("task_id", sa.String(), nullable=True))
    op.add_column("paper_documents", sa.Column("stage", sa.String(), nullable=True))
    op.add_column(
        "paper_documents",
        sa.Column("stage_progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "paper_documents",
        sa.Column("stage_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_paper_documents_task_id", "paper_documents", ["task_id"], unique=False
    )

    op.execute(
        "ALTER TABLE paper_documents "
        "DROP CONSTRAINT IF EXISTS paper_documents_paper_id_key"
    )
    op.create_unique_constraint(
        "uq_paper_documents_paper_user", "paper_documents", ["paper_id", "user_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_paper_documents_paper_user", "paper_documents", type_="unique"
    )
    op.create_unique_constraint(
        "paper_documents_paper_id_key", "paper_documents", ["paper_id"]
    )
    op.drop_index("ix_paper_documents_task_id", table_name="paper_documents")
    op.drop_column("paper_documents", "stage_total")
    op.drop_column("paper_documents", "stage_progress")
    op.drop_column("paper_documents", "stage")
    op.drop_column("paper_documents", "task_id")
