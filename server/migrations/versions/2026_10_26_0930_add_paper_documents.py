"""add paper_documents for bilingual OCR reader

Revision ID: c3d7f1a8b942
Revises: a7c1e9d24b30
Create Date: 2026-10-26 09:30:00.000000+00:00

Stores the per-page OCR markdown, its Chinese translation and the rendered page
image keys, so the reader can show the original page beside the translation.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d7f1a8b942"
down_revision: Union[str, None] = "a7c1e9d24b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ocr_model", sa.String(), nullable=True),
        sa.Column("translation_model", sa.String(), nullable=True),
        sa.Column("pages", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_paper_documents_paper_id", "paper_documents", ["paper_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_paper_documents_paper_id", table_name="paper_documents")
    op.drop_table("paper_documents")
