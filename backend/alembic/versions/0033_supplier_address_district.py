"""补充供应商注册地址与区级行政区字段。

Revision ID: 0033
Revises: 0027
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("registration_address", sa.Text(), nullable=True))
    op.add_column("supplier_sites", sa.Column("district", sa.Text(), nullable=True))
    op.add_column("event_locations", sa.Column("district", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("event_locations", "district")
    op.drop_column("supplier_sites", "district")
    op.drop_column("suppliers", "registration_address")
