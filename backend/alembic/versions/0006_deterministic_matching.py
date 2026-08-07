"""新增 D6 确定性主体、地点和产品匹配。

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE supplier_sites
        ADD COLUMN geom geography(Point, 4326)
        GENERATED ALWAYS AS (
            CASE
                WHEN longitude IS NULL THEN NULL
                ELSE ST_SetSRID(
                    ST_MakePoint(
                        longitude::double precision,
                        latitude::double precision
                    ),
                    4326
                )::geography
            END
        ) STORED
        """
    )
    op.create_index(
        "ix_supplier_sites_geom",
        "supplier_sites",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )

    op.create_table(
        "event_entities",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("registry_no", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id", "normalized_name", name="uq_event_entities_name"
        ),
    )
    op.create_table(
        "event_locations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("radius_km", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_event_locations_coordinate_pair",
        ),
        sa.CheckConstraint(
            "radius_km IS NULL OR (latitude IS NOT NULL AND radius_km > 0)",
            name="ck_event_locations_radius",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id", "normalized_name", name="uq_event_locations_name"
        ),
    )
    op.execute(
        """
        ALTER TABLE event_locations
        ADD COLUMN geom geography(Point, 4326)
        GENERATED ALWAYS AS (
            CASE
                WHEN longitude IS NULL THEN NULL
                ELSE ST_SetSRID(
                    ST_MakePoint(longitude, latitude),
                    4326
                )::geography
            END
        ) STORED
        """
    )
    op.create_index(
        "ix_event_locations_geom",
        "event_locations",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )

    op.add_column(
        "supplier_event_matches",
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_risk_event_signals_signal_id",
        "risk_event_signals",
        ["signal_id"],
        unique=False,
    )
    op.create_index(
        "ix_supplier_event_matches_event_id",
        "supplier_event_matches",
        ["event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_event_matches_event_id", table_name="supplier_event_matches"
    )
    op.drop_index("ix_risk_event_signals_signal_id", table_name="risk_event_signals")
    op.drop_column("supplier_event_matches", "evidence")
    op.drop_index("ix_event_locations_geom", table_name="event_locations")
    op.drop_table("event_locations")
    op.drop_table("event_entities")
    op.drop_index("ix_supplier_sites_geom", table_name="supplier_sites")
    op.drop_column("supplier_sites", "geom")
