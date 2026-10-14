"""信号稳定去重身份 dedup_key：列 + 历史回填 + 非破坏性去重 + 唯一约束。

Revision ID: 0060
Revises: 0059

背景：
- ``raw_signals`` 既有唯一键 ``uq_raw_signals_source_fingerprint`` 建在整模型
  ``fingerprint`` 上，而 16 个内置适配器的 fingerprint 覆盖整个
  ``ManualSignalInput``（含 url / content / title）。价格漂移、官方标题修订、
  http<->https 切换都会改变 fingerprint，同一条逻辑信号因此被反复写成新行：
  commodity-futures 因价格漂移产生过 152 条重复，usgs-earthquake-day 因标题
  修订产生过 190 条重复，mee/ofac/wto 亦有零星重复。
- 根因是「身份用易变字段定义」，不是去重机制本身失效。

方案：
- 新增 ``dedup_key`` 列，身份只由各信源的稳定字段子集决定，并由
   ``app.signals.identity.compute_dedup_key`` 统一计算（URL 先做 canonicalize）：
   公告类信源（mee/customs/fmprc）用 url，商务部详情用 external_id（适配器已把
   规范化详情 URL 与实体名编码进 external_id，兼顾同页多实体与协议/措辞不变性），
   sse-shipping / usgs-earthquake-day 用 external_id，其余信源回落到 external_id。
- 运行时与本回填调用同一个函数，保证历史行与新写入行的键一致——否则唯一索引会
  把旧数据当成新数据重新插一遍。
- ``fingerprint`` 列及其唯一约束保持不变：supersession 同权威时间胜负判定与天眼查
  写入路径仍按 ``(source_id, fingerprint)`` 取回行。

upgrade 语义（顺序固定）：
1. 加可空 ``dedup_key`` 列 + 非唯一索引 ``ix_raw_signals_source_dedup_key``（回填
   期间按 (source_id, dedup_key) 定位分组用）。
2. 按 id 分批回填：
   - 声明式信源（adapter_status='published'）读 ``adapter_config.fingerprint_fields``
     原样传入，与运行时 ``DeclarativeSourceAdapter.dedup_fields`` 逐字一致；
   - 其余信源按 ``data_sources.code`` 查 ``DEDUP_FIELDS``，缺省 external_id；
   - manual-json 缺 external_id 的历史行直接沿用其 ``fingerprint``（整模型口径），
     与运行时 ``ManualJsonAdapter.dedup_key`` 的回落分支一致。
   历史遗留的非法字段（如 ``content``）在 ``compute_dedup_key`` 中按缺失处理。
3. 碰撞消解：同 ``(source_id, dedup_key)`` 的多行按
   （validity_state='active' 优先 → valid_from 大者 → collected_at 大者 → id 大者）
   保留最优行，其余行 ``dedup_key`` 追加 ``#legacy-{id}`` 后缀。**绝不删除任何行**，
   避免级联删除 ai_analysis_records / risk_event_signals 的历史证据。
4. 校验无 NULL 后置 NOT NULL；删掉过渡用非唯一索引，建唯一约束
   ``uq_raw_signals_source_dedup_key``。

downgrade 语义：drop 唯一约束与列。历史行数据原样保留，回填不可逆但可无损重建。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from app.signals.identity import DEDUP_FIELDS, compute_dedup_key

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEDUP_INDEX_NAME = "uq_raw_signals_source_dedup_key"
DEDUP_BACKFILL_INDEX_NAME = "ix_raw_signals_source_dedup_key"
MANUAL_SOURCE_CODE = "manual-json"
# 声明式信源才读 adapter_config.fingerprint_fields；builtin 走 DEDUP_FIELDS 映射，
# 与运行时的适配器选择分支（router.build_adapter）一致。
DECLARATIVE_STATUS = "published"
_BACKFILL_BATCH_SIZE = 1000

_UPDATE_ROW_DEDUP_KEY = sa.text(
    "UPDATE raw_signals SET dedup_key = :dedup_key WHERE id = :id"
)

# 同 (source_id, dedup_key) 组内排序：active 优先、权威时间与采集时间新者优先，
# 最后以 id 兜底保证全序确定（重复执行得到同一结果）。
_SUFFIX_COLLISIONS = sa.text(
    """
    WITH ranked AS (
        SELECT id,
               row_number() OVER (
                   PARTITION BY source_id, dedup_key
                   ORDER BY (validity_state = 'active') DESC,
                            valid_from DESC NULLS LAST,
                            collected_at DESC,
                            id DESC
               ) AS position
        FROM raw_signals
    )
    UPDATE raw_signals AS rs
    SET dedup_key = rs.dedup_key || '#legacy-' || rs.id
    FROM ranked
    WHERE rs.id = ranked.id
      AND ranked.position > 1
    """
)

_ASSERT_NO_NULL = sa.text(
    "SELECT count(*) FROM raw_signals WHERE dedup_key IS NULL"
)


def _source_field_map(connection: Connection) -> dict[int, tuple[str, tuple[str, ...] | None]]:
    """source_id -> (code, 声明式字段集合或 None)。

    声明式字段集合原样返回（含历史遗留的非法字段），以便与运行时对同一份数据
    算出同一个键；是否合法由 ``AdapterSpec`` 校验负责，不在迁移里另写一套口径。
    """
    rows = connection.execute(
        sa.text("SELECT id, code, adapter_status, adapter_config FROM data_sources")
    ).mappings()
    mapping: dict[int, tuple[str, tuple[str, ...] | None]] = {}
    for row in rows:
        fields: tuple[str, ...] | None = None
        if row["adapter_status"] == DECLARATIVE_STATUS:
            config = row["adapter_config"] or {}
            declared = config.get("fingerprint_fields") if isinstance(config, dict) else None
            if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
                fields = tuple(declared)
        mapping[int(row["id"])] = (str(row["code"]), fields)
    return mapping


def _dedup_key_for(
    code: str,
    fields: tuple[str, ...] | None,
    *,
    external_id: str | None,
    url: str | None,
    title: str | None,
    fingerprint: str,
) -> str:
    """与运行时完全一致的去重键；manual-json 缺 external_id 时沿用整模型指纹。"""
    if code == MANUAL_SOURCE_CODE and not external_id:
        return fingerprint
    return compute_dedup_key(
        code,
        external_id=external_id,
        url=url,
        title=title,
        fields=fields,
    )


def _backfill(connection: Connection) -> None:
    sources = _source_field_map(connection)
    last_id = 0
    while True:
        rows = connection.execute(
            sa.text(
                "SELECT id, source_id, external_id, url, title, fingerprint "
                "FROM raw_signals WHERE id > :last_id ORDER BY id LIMIT :limit"
            ),
            {"last_id": last_id, "limit": _BACKFILL_BATCH_SIZE},
        ).mappings().all()
        if not rows:
            break
        updates: list[dict[str, Any]] = []
        for row in rows:
            # FK 保证 source 一定存在；缺失只可能是并发删源，回落默认字段口径。
            code, fields = sources.get(int(row["source_id"]), ("", None))
            updates.append(
                {
                    "id": int(row["id"]),
                    "dedup_key": _dedup_key_for(
                        code,
                        fields,
                        external_id=row["external_id"],
                        url=row["url"],
                        title=row["title"],
                        fingerprint=str(row["fingerprint"]),
                    ),
                }
            )
        connection.execute(_UPDATE_ROW_DEDUP_KEY, updates)
        last_id = updates[-1]["id"]


def upgrade() -> None:
    op.add_column(
        "raw_signals",
        sa.Column("dedup_key", sa.Text(), nullable=True),
    )
    op.create_index(
        DEDUP_BACKFILL_INDEX_NAME, "raw_signals", ["source_id", "dedup_key"], unique=False
    )
    connection = op.get_bind()
    _backfill(connection)
    # 非破坏性消解碰撞：保留全部历史行，仅给落败行追加稳定后缀。
    connection.execute(_SUFFIX_COLLISIONS)
    if int(connection.scalar(_ASSERT_NO_NULL) or 0) != 0:
        raise RuntimeError("0060 回填后仍存在 dedup_key 为空的 raw_signals 行")
    op.alter_column(
        "raw_signals",
        "dedup_key",
        nullable=False,
        existing_type=sa.Text(),
    )
    op.drop_index(DEDUP_BACKFILL_INDEX_NAME, table_name="raw_signals")
    op.create_unique_constraint(
        DEDUP_INDEX_NAME, "raw_signals", ["source_id", "dedup_key"]
    )


def downgrade() -> None:
    op.drop_constraint(DEDUP_INDEX_NAME, "raw_signals", type_="unique")
    op.drop_column("raw_signals", "dedup_key")