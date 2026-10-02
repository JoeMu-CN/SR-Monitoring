"""把 usgs-earthquake-day 从声明式适配器转为内置采集（逐行 provenance，保守降级）。

Revision ID: 0055
Revises: 0054

背景：
- USGS 全天地震速报已由内置适配器 ``UsgsEarthquakeAdapter`` 承接（与声明式 spec 的
  字段映射和 ``external_id + title`` 指纹逐字节一致），不再需要声明式 adapter_config。
- 业务库中该行（编码 ``usgs-earthquake-day``）可能是已发布的声明式信源，也可能在全新
  库中根本不存在；本迁移对两种前态分别处理，并把原 adapter 元数据写入 login_config
  provenance marker，保证逐行可逆。

upgrade 语义（先插入缺失行，再转换既有行；均按 code 定位）：
- 行不存在：按规范插入一行内置信源（source_type='official_api'、可信度 95、
  schedule='*/30 * * * *'、enabled=false、adapter_status='builtin'、adapter_config='{}'），
  并写 marker ``{"revision":"0055","was_inserted":true}``；空结果无副作用。
- 行存在且不处于目标态（builtin + 空 adapter_config + 固定 endpoint_url）：
  把原 adapter_status / adapter_config / endpoint_url / adapter_version /
  adapter_published_at 写入 ``login_config.usgs_builtin_migration.before``，再置
  adapter_status='builtin'、adapter_config='{}'、endpoint_url 为固定官方地址。
  不修改 updated_at，保留 enabled/schedule 与全部历史数据。
- 已处于目标态的行 0 行更新、不写 marker（重复执行幂等）；已有 0055 marker 的行不重复
  覆盖，避免丢失 provenance。

downgrade 语义（保守、逐字段条件恢复）：
1. 先删除 ``was_inserted=true`` 的行：仅当该行仍停用（enabled=false）且下列 5 张引用
   ``data_sources.id`` 的表全部无引用时才删除——
   collection_runs / source_member_states / raw_signals（CASCADE）与
   data_source_audit_logs / source_onboarding_drafts（SET NULL）。
2. 仍有引用的新建行：保留行与 marker，并把不可自动降级原因（enabled 与各表引用标志）
   写入 marker 的 ``downgrade_blocked``，方便人工处理。
3. ``was_inserted=false`` 的有效 marker：仅当字段当前值仍等于迁移写入值
   （adapter_status='builtin'、adapter_config='{}'、endpoint_url 固定）时才从 marker
   逐字段还原 before 原值；已被运营再次改写的字段保留现值；有效 marker 一律清理。
4. 目标行缺失时全部 no-op、不报错；不重开收尾任何历史数据。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0055"
MARKER_KEY = "usgs_builtin_migration"
USGS_CODE = "usgs-earthquake-day"
USGS_NAME = "USGS 全天地震速报"
USGS_SOURCE_TYPE = "official_api"
USGS_CREDIBILITY = 95
USGS_SCHEDULE = "*/30 * * * *"
USGS_ENDPOINT_URL = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
)
USGS_DESCRIPTION = (
    "内置采集：官方 GeoJSON 免认证，每 30 分钟拉取全天地震速报；"
    "字段映射与指纹见 UsgsEarthquakeAdapter。"
)
BLOCKED_REASON = (
    "迁移 0055 新建的 usgs-earthquake-day 行仍启用或存在引用，已保留行与 marker，"
    "未自动删除；请人工确认后再处理。"
)

_PARAMS: dict[str, object] = {
    "code": USGS_CODE,
    "name": USGS_NAME,
    "source_type": USGS_SOURCE_TYPE,
    "credibility": USGS_CREDIBILITY,
    "schedule": USGS_SCHEDULE,
    "endpoint": USGS_ENDPOINT_URL,
    "description": USGS_DESCRIPTION,
    "marker_key": MARKER_KEY,
    "marker_revision": MIGRATION_MARKER,
    "blocked_reason": BLOCKED_REASON,
}

# 1. 行缺失时按规范插入默认停用的内置行，并写 was_inserted=true marker。
_INSERT_MISSING = sa.text(
    """
    INSERT INTO data_sources (
        code, name, source_type, credibility, schedule, endpoint_url,
        auth_type, login_config, description, adapter_config,
        adapter_status, adapter_version, enabled
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
        'builtin', 0, false
    WHERE NOT EXISTS (SELECT 1 FROM data_sources WHERE code = :code)
    """
)

# 2. 行存在且不在目标态时：先记录完整前态，再归一为内置；不触碰 updated_at/enabled/schedule。
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

# 3. downgrade：仅当仍停用且 5 张引用表全部无引用时删除新建行。
_DELETE_INSERTED = sa.text(
    """
    DELETE FROM data_sources AS ds
    WHERE ds.code = :code
      AND jsonb_typeof(ds.login_config->CAST(:marker_key AS text)) = 'object'
      AND ds.login_config->CAST(:marker_key AS text)->>'revision' = CAST(:marker_revision AS text)
      AND ds.login_config->CAST(:marker_key AS text)->>'was_inserted' = 'true'
      AND ds.enabled = false
      AND NOT EXISTS (
          SELECT 1 FROM collection_runs AS r WHERE r.source_id = ds.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM source_member_states AS m WHERE m.source_id = ds.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM raw_signals AS s WHERE s.source_id = ds.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM data_source_audit_logs AS a WHERE a.source_id = ds.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM source_onboarding_drafts AS d WHERE d.source_id = ds.id
      )
    """
)

# 4. downgrade：有引用/仍启用的新建行保留行与 marker，并记录不可自动降级原因。
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
