"""播种中央气象台公告类栏目信源（nmc-weather-bulletin）。

Revision ID: 0067
Revises: 0066

背景：
- 中央气象台除 findAlarm 的正式预警信号外，还以"公告"形式发布更早的风险线索：
  每日天气提示、重要天气提示、台风预警。它们由内置适配器
  ``NmcBulletinAdapter`` 承接（三个栏目页串行抓取，正文在 ``#text`` 容器内）。
- 仓库此前没有该 code 的 data_sources 行，适配器即使实现了也永远不会被调度。

upgrade 语义（单行、按 code 定位）：
- 行不存在：按规范插入一行（source_type='weather'、可信度 85、
  schedule='7,37 * * * *'、enabled=true、adapter_status='builtin'、
  adapter_config='{}'、validity_policy=fixed_days=7 天），
  并写 marker ``{"revision":"0067","was_inserted":true}``。
  采集周期刻意错开 ``nmc-weather`` 的 ``*/30``：两者同域，受控链路对
  www.nmc.cn 有 10 秒最小请求间隔，同刻触发会互相判定为"请求过于频繁"。
- 行已存在（人工预建）：只把 adapter_status/adapter_config/endpoint_url 归一到
  内置目标态并记录前态，**不修改** enabled / schedule / name / source_type /
  credibility / validity_policy，保留运营语义；重复执行幂等。
- validity_policy 采用 fixed_days=7 天：公告的影响期常跨一周（台风路径预报可覆盖
  5-6 天），按发布起 7 天有效；与 ``nmc-weather``（3 天）仅参数不同，模式本身
  仍用既有档位，不引入新模式。

downgrade 语义（保守）：
1. 仅当下列 5 张引用 data_sources.id 的表全部无引用时删除新建行——
   collection_runs / source_member_states / raw_signals 与
   data_source_audit_logs / source_onboarding_drafts。
2. 仍有引用：保留行与 marker，把不可自动降级原因写入
   ``marker.downgrade_blocked``，由人工处理。
3. 既有行的有效 marker 逐字段条件还原（仅还原仍等于迁移写入值的字段）并清理 marker。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0067"
MARKER_KEY = "nmc_bulletin_migration"
BULLETIN_CODE = "nmc-weather-bulletin"
BULLETIN_NAME = "中央气象台天气公告"
BULLETIN_SOURCE_TYPE = "weather"
BULLETIN_CREDIBILITY = 85
# 错开 nmc-weather 的 */30，避免同域两个采集任务同刻互相触发节流。
BULLETIN_SCHEDULE = "7,37 * * * *"
# 与 NmcBulletinAdapter.endpoint 保持一致（测试断言二者字面相同）。
BULLETIN_ENDPOINT_URL = "https://www.nmc.cn/publish/weatherperday/index.htm"
BULLETIN_DESCRIPTION = (
    "内置采集：中央气象台公告类栏目（每日天气提示 / 重要天气提示 / 台风预警），"
    "三个栏目页串行抓取，取各页最新一条全文；公告通常早于地方台正式预警信号。"
)
BULLETIN_VALIDITY_POLICY = '{"mode":"fixed_days","fixed_days":7}'
BLOCKED_REASON = (
    "迁移 0067 新建的 nmc-weather-bulletin 行仍存在引用，已保留行与 marker，"
    "未自动删除；请人工确认后再处理。"
)

_PARAMS: dict[str, object] = {
    "code": BULLETIN_CODE,
    "name": BULLETIN_NAME,
    "source_type": BULLETIN_SOURCE_TYPE,
    "credibility": BULLETIN_CREDIBILITY,
    "schedule": BULLETIN_SCHEDULE,
    "endpoint": BULLETIN_ENDPOINT_URL,
    "description": BULLETIN_DESCRIPTION,
    "validity_policy": BULLETIN_VALIDITY_POLICY,
    "marker_key": MARKER_KEY,
    "marker_revision": MIGRATION_MARKER,
    "blocked_reason": BLOCKED_REASON,
}

# 1. 行缺失时插入内置行（统一行，enabled=true），并写 was_inserted=true marker。
_INSERT_MISSING = sa.text(
    """
    INSERT INTO data_sources (
        code, name, source_type, credibility, schedule, endpoint_url,
        auth_type, login_config, description, adapter_config,
        adapter_status, adapter_version, enabled, validity_policy
    )
    SELECT
        :code, :name, :source_type, :credibility, :schedule, :endpoint,
        'none',
        jsonb_build_object(
            CAST(:marker_key AS text),
            jsonb_build_object(
                'revision', CAST(:marker_revision AS text),
                'was_inserted', true
            )
        ),
        :description, '{}'::jsonb,
        'builtin', 0, true, CAST(:validity_policy AS jsonb)
    WHERE NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :code)
    """
)

# 2. 行已存在且不在目标态：记录前态后归一为内置；不动 enabled/schedule/展示字段。
_CONVERT_EXISTING = sa.text(
    """
    UPDATE data_sources
    SET adapter_status = 'builtin',
        adapter_config = '{}'::jsonb,
        endpoint_url = :endpoint,
        login_config = jsonb_set(
            login_config,
            ARRAY[CAST(:marker_key AS text)],
            jsonb_build_object(
                'revision', CAST(:marker_revision AS text),
                'was_inserted', false,
                'before', jsonb_build_object(
                    'adapter_status', adapter_status,
                    'adapter_config', adapter_config,
                    'endpoint_url', endpoint_url,
                    'adapter_version', adapter_version,
                    'adapter_published_at', adapter_published_at
                )
            ),
            true
        )
    WHERE code = :code
      AND COALESCE(login_config->CAST(:marker_key AS text)->>'revision', '')
          <> CAST(:marker_revision AS text)
      AND NOT (
          adapter_status = 'builtin'
          AND adapter_config = '{}'::jsonb
          AND endpoint_url IS NOT DISTINCT FROM :endpoint
      )
    """
)

# 3. downgrade：无任何引用时删除新建行。
_DELETE_INSERTED = sa.text(
    """
    DELETE FROM data_sources AS ds
    WHERE ds.code = :code
      AND jsonb_typeof(ds.login_config->CAST(:marker_key AS text)) = 'object'
      AND ds.login_config->CAST(:marker_key AS text)->>'revision' = CAST(:marker_revision AS text)
      AND ds.login_config->CAST(:marker_key AS text)->>'was_inserted' = 'true'
      AND NOT EXISTS (SELECT 1 FROM collection_runs AS r WHERE r.source_id = ds.id)
      AND NOT EXISTS (SELECT 1 FROM source_member_states AS m WHERE m.source_id = ds.id)
      AND NOT EXISTS (SELECT 1 FROM raw_signals AS s WHERE s.source_id = ds.id)
      AND NOT EXISTS (SELECT 1 FROM data_source_audit_logs AS a WHERE a.source_id = ds.id)
      AND NOT EXISTS (SELECT 1 FROM source_onboarding_drafts AS d WHERE d.source_id = ds.id)
    """
)

# 4. downgrade：有引用的新建行保留并记录阻止原因。
_RECORD_BLOCKED = sa.text(
    """
    UPDATE data_sources AS ds
    SET login_config = jsonb_set(
            ds.login_config,
            ARRAY[CAST(:marker_key AS text), 'downgrade_blocked'],
            jsonb_build_object(
                'reason', CAST(:blocked_reason AS text),
                'enabled', ds.enabled,
                'has_collection_runs', EXISTS (
                    SELECT 1 FROM collection_runs AS r WHERE r.source_id = ds.id
                ),
                'has_source_member_states', EXISTS (
                    SELECT 1 FROM source_member_states AS m WHERE m.source_id = ds.id
                ),
                'has_raw_signals', EXISTS (
                    SELECT 1 FROM raw_signals AS s WHERE s.source_id = ds.id
                ),
                'has_data_source_audit_logs', EXISTS (
                    SELECT 1 FROM data_source_audit_logs AS a WHERE a.source_id = ds.id
                ),
                'has_onboarding_drafts', EXISTS (
                    SELECT 1 FROM source_onboarding_drafts AS d WHERE d.source_id = ds.id
                )
            ),
            true
        )
    WHERE ds.code = :code
      AND jsonb_typeof(ds.login_config->CAST(:marker_key AS text)) = 'object'
      AND ds.login_config->CAST(:marker_key AS text)->>'revision' = CAST(:marker_revision AS text)
      AND ds.login_config->CAST(:marker_key AS text)->>'was_inserted' = 'true'
    """
)

# 5. downgrade：既有行的有效 marker 逐字段条件还原，并清理 marker。
_RESTORE_EXISTING = sa.text(
    """
    UPDATE data_sources
    SET adapter_status = CASE
            WHEN adapter_status = 'builtin'
            THEN login_config->CAST(:marker_key AS text)->'before'->>'adapter_status'
            ELSE adapter_status
        END,
        adapter_config = CASE
            WHEN adapter_config = '{}'::jsonb
            THEN login_config->CAST(:marker_key AS text)->'before'->'adapter_config'
            ELSE adapter_config
        END,
        endpoint_url = CASE
            WHEN endpoint_url IS NOT DISTINCT FROM :endpoint
            THEN login_config->CAST(:marker_key AS text)->'before'->>'endpoint_url'
            ELSE endpoint_url
        END,
        login_config = login_config - CAST(:marker_key AS text)
    WHERE code = :code
      AND jsonb_typeof(login_config->CAST(:marker_key AS text)) = 'object'
      AND login_config->CAST(:marker_key AS text)->>'revision' = CAST(:marker_revision AS text)
      AND login_config->CAST(:marker_key AS text)->>'was_inserted' = 'false'
      AND (login_config->CAST(:marker_key AS text)) ? 'before'
    """
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(_INSERT_MISSING, _PARAMS)
    connection.execute(_CONVERT_EXISTING, _PARAMS)


def downgrade() -> None:
    # 顺序固定：先删可删的新建行，再记录被阻止的新建行，最后还原既有行并清理 marker。
    connection = op.get_bind()
    connection.execute(_DELETE_INSERTED, _PARAMS)
    connection.execute(_RECORD_BLOCKED, _PARAMS)
    connection.execute(_RESTORE_EXISTING, _PARAMS)
