"""名单成员状态与完整快照质量跟踪。

Revision ID: 0048
Revises: 0047
"""

# noqa: SIZE_OK — 单版可逆 DDL 必须集中保存完整约束及其对称回退顺序。

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEMBER_STATUS_VALUES = "'active', 'revoked'"
SNAPSHOT_QUALITY_VALUES = "'complete', 'partial', 'empty', 'failed'"


def upgrade() -> None:
    op.create_table(
        "source_member_states",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("data_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consecutive_missing",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("baseline_snapshot_hash", sa.Text(), nullable=False),
        sa.Column(
            "quarantine_round",
            sa.Integer(),
            server_default=sa.text("0"),
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
        sa.UniqueConstraint(
            "source_id", "member_key", name="uq_source_member_states_source_key"
        ),
        sa.CheckConstraint(
            f"status IN ({MEMBER_STATUS_VALUES})",
            name="ck_source_member_states_status",
        ),
        sa.CheckConstraint(
            "consecutive_missing >= 0", name="ck_source_member_states_consecutive_missing"
        ),
        sa.CheckConstraint(
            "quarantine_round >= 0", name="ck_source_member_states_quarantine_round"
        ),
        sa.CheckConstraint(
            "btrim(member_key) <> ''", name="ck_source_member_states_member_key"
        ),
    )
    op.create_index(
        "ix_source_member_states_source_status",
        "source_member_states",
        ["source_id", "status"],
    )

    op.add_column(
        "collection_runs",
        sa.Column(
            "snapshot_complete",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column("snapshot_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "snapshot_quality",
            sa.Text(),
            server_default=sa.text("'partial'"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "member_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "new_member_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "revoked_member_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "quarantine_round",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "collection_runs",
        sa.Column("quarantine_baseline_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "collection_runs",
        sa.Column("quarantine_missing_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "collection_runs",
        sa.Column("summary", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_collection_runs_snapshot_quality",
        "collection_runs",
        f"snapshot_quality IN ({SNAPSHOT_QUALITY_VALUES})",
    )
    op.create_check_constraint(
        "ck_collection_runs_member_counts",
        "collection_runs",
        "member_count >= 0 AND new_member_count >= 0 AND revoked_member_count >= 0",
    )
    op.create_check_constraint(
        "ck_collection_runs_quarantine_round",
        "collection_runs",
        "quarantine_round >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_collection_runs_quarantine_round", "collection_runs", type_="check"
    )
    op.drop_constraint(
        "ck_collection_runs_member_counts", "collection_runs", type_="check"
    )
    op.drop_constraint(
        "ck_collection_runs_snapshot_quality", "collection_runs", type_="check"
    )
    for column_name in (
        "summary",
        "quarantine_missing_hash",
        "quarantine_baseline_hash",
        "quarantine_round",
        "revoked_member_count",
        "new_member_count",
        "member_count",
        "snapshot_quality",
        "snapshot_hash",
        "snapshot_complete",
    ):
        op.drop_column("collection_runs", column_name)

    op.drop_index("ix_source_member_states_source_status", table_name="source_member_states")
    op.drop_table("source_member_states")