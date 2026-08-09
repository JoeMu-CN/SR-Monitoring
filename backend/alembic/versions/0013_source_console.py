"""增加数据源控制台配置字段与管理员审计日志。

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("data_sources", sa.Column("endpoint_url", sa.Text(), nullable=True))
    op.add_column(
        "data_sources",
        sa.Column("auth_type", sa.Text(), nullable=False, server_default="none"),
    )
    op.add_column(
        "data_sources",
        sa.Column(
            "login_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("data_sources", sa.Column("credential_ref", sa.Text(), nullable=True))
    op.add_column("data_sources", sa.Column("api_key_hash", sa.Text(), nullable=True))
    op.add_column("data_sources", sa.Column("api_key_last4", sa.Text(), nullable=True))
    op.add_column("data_sources", sa.Column("description", sa.Text(), nullable=True))
    op.create_table(
        "data_source_audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_data_source_audit_logs_created_at",
        "data_source_audit_logs",
        ["created_at"],
    )
    op.bulk_insert(
        sa.table(
            "data_sources",
            sa.column("code", sa.Text()),
            sa.column("name", sa.Text()),
            sa.column("source_type", sa.Text()),
            sa.column("credibility", sa.SmallInteger()),
            sa.column("schedule", sa.Text()),
            sa.column("endpoint_url", sa.Text()),
            sa.column("auth_type", sa.Text()),
            sa.column("login_config", postgresql.JSONB(astext_type=sa.Text())),
            sa.column("description", sa.Text()),
            sa.column("enabled", sa.Boolean()),
        ),
        [
            {
                "code": "mofcom-entity-control",
                "name": "商务部不可靠实体与出口管制清单",
                "source_type": "sanctions",
                "credibility": 98,
                "schedule": "0 3 * * *",
                "endpoint_url": "https://www.mofcom.gov.cn/",
                "auth_type": "none",
                "login_config": {},
                "description": "规划 P0；公开清单，需按公告/文件指纹增量接入。",
                "enabled": False,
            },
            {
                "code": "bis-entity-list",
                "name": "美国商务部 BIS Entity List",
                "source_type": "export-control",
                "credibility": 97,
                "schedule": "0 */6 * * *",
                "endpoint_url": "https://www.bis.gov/regulations/ear/744#section-744.16",
                "auth_type": "none",
                "login_config": {},
                "description": "官方 EAR 744 实体清单入口；启用前确认抓取许可与变更格式。",
                "enabled": False,
            },
            {
                "code": "un-consolidated-sanctions",
                "name": "联合国安理会综合制裁清单",
                "source_type": "sanctions",
                "credibility": 98,
                "schedule": "0 4 * * *",
                "endpoint_url": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
                "auth_type": "none",
                "login_config": {},
                "description": "联合国官方制裁清单；适合作为跨境制裁交叉核验来源。",
                "enabled": False,
            },
            {
                "code": "eu-official-journal",
                "name": "欧盟官方公报 EUR-Lex",
                "source_type": "policy",
                "credibility": 96,
                "schedule": "0 5 * * *",
                "endpoint_url": "https://eur-lex.europa.eu/oj/direct-access.html",
                "auth_type": "none",
                "login_config": {},
                "description": "CSDDD/CBAM 等法规的官方发布入口；建议按主题订阅而非全量抓取。",
                "enabled": False,
            },
            {
                "code": "mem-incident-bulletin",
                "name": "应急管理部事故通报",
                "source_type": "incident",
                "credibility": 95,
                "schedule": "0 */6 * * *",
                "endpoint_url": "https://www.mem.gov.cn/",
                "auth_type": "none",
                "login_config": {},
                "description": "规划 N4/F4；事故通报需保留原文链接并人工确认结构化字段。",
                "enabled": False,
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM data_sources WHERE code IN ("
        "'mofcom-entity-control','bis-entity-list','un-consolidated-sanctions',"
        "'eu-official-journal','mem-incident-bulletin')"
    )
    op.drop_index("ix_data_source_audit_logs_created_at", table_name="data_source_audit_logs")
    op.drop_table("data_source_audit_logs")
    for column in (
        "description",
        "api_key_last4",
        "api_key_hash",
        "credential_ref",
        "login_config",
        "auth_type",
        "endpoint_url",
    ):
        op.drop_column("data_sources", column)
