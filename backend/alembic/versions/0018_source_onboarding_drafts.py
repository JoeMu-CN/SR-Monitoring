"""保存可恢复的数据源接入草稿。

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_onboarding_drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column(
            "current_step",
            sa.Text(),
            server_default=sa.text("'source_url'"),
            nullable=False,
        ),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_session_id", name="uq_source_onboarding_drafts_session"),
    )
    op.create_index(
        "ix_source_onboarding_drafts_updated",
        "source_onboarding_drafts",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_onboarding_drafts_updated", table_name="source_onboarding_drafts")
    op.drop_table("source_onboarding_drafts")
