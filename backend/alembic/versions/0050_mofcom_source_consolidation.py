"""整合商务部重复外部信源为唯一 mofcom-entity-detail 入口。

Revision ID: 0050
Revises: 0049

背景：data_sources 中同时存在 mofcom-entity-control（声明式列表源，业务库 ID 4）
与 mofcom-entity-detail（内置详情适配器，业务库 ID 361）两个商务部外部信源。
代码已改为 MofcomEntityDetailAdapter 自行抓取首页，不再读取 mofcom-entity-control
的声明式配置；本迁移把重复入口收敛为单一 source code，避免两个源同时被调度。

upgrade 语义（全部按 code 定位，ID 仅作注释与额外保护，绝不作为唯一条件）：
- mofcom-entity-detail 缺失时按项目既有 seed 约定（0009/0013 的 bulk
  INSERT 约定）插入一行，并继承 mofcom-entity-control 的有效 enabled/schedule
  （若存在）；两者都不存在时用安全停用默认值，绝不删除或重绑任何历史行。
- mofcom-entity-detail 置为 builtin、endpoint_url 固定、adapter_config 归空，
  存在 control 时同时继承其 enabled/schedule。
- mofcom-entity-control 停用并取消调度：仅设 enabled=false、schedule=NULL、
  updated_at；adapter_status、adapter_config、endpoint_url 等旧连接器恢复所需
  配置原样保留；行及历史 signals/runs 保留。
- 两个商务部信源中超过 30 分钟仍 running 的 collection_runs 标记 failed，
  finished_at=now()，写入稳定迁移恢复原因。

downgrade 语义（保守、可逆）：从 detail 回填 control 的 enabled/schedule
（upgrade 时由 control 继承而来，是确定性逆值）；upgrade 从未改动 control 的
adapter_status / adapter_config / endpoint_url，原 published 声明式配置仍在，
旧代码的 detail→control 依赖（从 control 配置抓列表）可继续工作；不重开已收尾
运行，不删除历史数据，不触碰 raw_signals / source_member_states /
collection_runs。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTROL_CODE = "mofcom-entity-control"
DETAIL_CODE = "mofcom-entity-detail"
DETAIL_NAME = "商务部产业安全与进出口管制局实体名单详情"
DETAIL_ENDPOINT_URL = "http://aqygzj.mofcom.gov.cn/"
DETAIL_DESCRIPTION = "商务部实体名单详情解析；承接原 mofcom-entity-control 的采集与调度入口。"
# 稳定迁移恢复原因：与 app.signals.service 的运行时收尾文本刻意区分，便于回溯。
STALE_RUN_RECOVERY_ERROR = "迁移0050：商务部信源整合，超过30分钟未收尾的采集运行已标记失败"
STALE_RUN_SECONDS = 1800

_PARAMS: dict[str, object] = {
    "control_code": CONTROL_CODE,
    "detail_code": DETAIL_CODE,
    "detail_name": DETAIL_NAME,
    "detail_url": DETAIL_ENDPOINT_URL,
    "detail_description": DETAIL_DESCRIPTION,
    "reason": STALE_RUN_RECOVERY_ERROR,
}

_INSERT_COLUMNS = """
    code, name, source_type, credibility, schedule, endpoint_url,
    auth_type, login_config, description, adapter_config,
    adapter_status, adapter_version, enabled
"""

# 1a. control 存在：插入 detail，继承其有效 enabled/schedule。
_INSERT_DETAIL_FROM_CONTROL = sa.text(
    f"""
    INSERT INTO data_sources ({_INSERT_COLUMNS})
    SELECT
        :detail_code, :detail_name, 'sanctions', 98, control.schedule,
        :detail_url, 'none', '{{}}'::jsonb, :detail_description,
        '{{}}'::jsonb, 'builtin', 0, control.enabled
    FROM data_sources AS control
    WHERE control.code = :control_code
      AND NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :detail_code)
    """
)

# 1b. control 缺失：仍保证 detail 存在，用安全停用默认值。
_INSERT_DETAIL_DEFAULT = sa.text(
    f"""
    INSERT INTO data_sources ({_INSERT_COLUMNS})
    SELECT
        :detail_code, :detail_name, 'sanctions', 98, NULL,
        :detail_url, 'none', '{{}}'::jsonb, :detail_description,
        '{{}}'::jsonb, 'builtin', 0, false
    WHERE NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :control_code)
      AND NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :detail_code)
    """
)

# 2a. control 存在：detail 归一为 builtin 并继承 enabled/schedule。
_UPDATE_DETAIL_WITH_CONTROL = sa.text(
    """
    UPDATE data_sources AS detail
    SET adapter_status = 'builtin',
        endpoint_url = :detail_url,
        adapter_config = '{}'::jsonb,
        enabled = control.enabled,
        schedule = control.schedule,
        updated_at = now()
    FROM data_sources AS control
    WHERE detail.code = :detail_code
      AND control.code = :control_code
    """
)

# 2b. control 缺失：detail 仍归一为 builtin，但保留自身 enabled/schedule。
_UPDATE_DETAIL_STANDALONE = sa.text(
    """
    UPDATE data_sources
    SET adapter_status = 'builtin',
        endpoint_url = :detail_url,
        adapter_config = '{}'::jsonb,
        updated_at = now()
    WHERE code = :detail_code
      AND NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :control_code)
    """
)

# 3. control 停用并取消调度；仅设 enabled/schedule/updated_at，保留 adapter_status、
#    adapter_config、endpoint_url 等旧连接器恢复所需配置；不删除行、不重绑历史。
_DISABLE_CONTROL = sa.text(
    """
    UPDATE data_sources
    SET enabled = false,
        schedule = NULL,
        updated_at = now()
    WHERE code = :control_code
    """
)

# 4. 两个商务部信源中超过阈值仍 running 的运行按失败收尾。
_FINALIZE_STALE_RUNS = sa.text(
    f"""
    UPDATE collection_runs
    SET status = 'failed',
        finished_at = now(),
        error = :reason
    WHERE status = 'running'
      AND started_at < now() - make_interval(secs => {STALE_RUN_SECONDS})
      AND source_id IN (
          SELECT id FROM data_sources
          WHERE code IN (:control_code, :detail_code)
      )
    """
)

# 降级：从 detail 回填 control 的 enabled/schedule（upgrade 继承的确定性逆值）。
_RESTORE_CONTROL_SCHEDULE = sa.text(
    """
    UPDATE data_sources AS control
    SET enabled = detail.enabled,
        schedule = detail.schedule,
        updated_at = now()
    FROM data_sources AS detail
    WHERE control.code = :control_code
      AND detail.code = :detail_code
    """
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(_INSERT_DETAIL_FROM_CONTROL, _PARAMS)
    connection.execute(_INSERT_DETAIL_DEFAULT, _PARAMS)
    connection.execute(_UPDATE_DETAIL_WITH_CONTROL, _PARAMS)
    connection.execute(_UPDATE_DETAIL_STANDALONE, _PARAMS)
    connection.execute(_DISABLE_CONTROL, _PARAMS)
    connection.execute(_FINALIZE_STALE_RUNS, _PARAMS)


def downgrade() -> None:
    # 只恢复可安全恢复的源配置；不重开已收尾运行，不删除历史数据。
    connection = op.get_bind()
    connection.execute(_RESTORE_CONTROL_SCHEDULE, _PARAMS)
