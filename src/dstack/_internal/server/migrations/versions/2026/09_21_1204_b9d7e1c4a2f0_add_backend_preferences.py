"""Add BackendModel.preferences

Revision ID: b9d7e1c4a2f0
Revises: 40f19bea9d2a
Create Date: 2026-09-21 12:04:00+09:00

"""

import sqlalchemy as sa
from alembic import op

revision = "b9d7e1c4a2f0"
down_revision = "40f19bea9d2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("backends", schema=None) as batch_op:
        batch_op.add_column(sa.Column("preferences", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backends", schema=None) as batch_op:
        batch_op.drop_column("preferences")
