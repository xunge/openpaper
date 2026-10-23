"""add local password

Revision ID: a7c1e9d24b30
Revises: 8c89f2444587
Create Date: 2026-10-23 01:00:00.000000+00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c1e9d24b30"
down_revision: Union[str, None] = "8c89f2444587"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the password hash used by local (non-OAuth) accounts."""
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the local password hash."""
    op.drop_column("users", "password_hash")
