"""为市场指标监控播种三个行情信源（逐行 provenance，保守降级）。

Revision ID: 0062
Revises: 0061

背景：
- ``commodity-futures``/``fx-rates``/``semiconductor-index`` 三个信源已在
  ``app.signals.sources`` 中由内置适配器（CommodityFuturesAdapter / FxRatesAdapter /
  SemiconductorIndexAdapter）承接，并由 ``app.market.registry`` 绑定为 15 期货 + 4 汇率
  + 1 半导体指数共 20 个行情指标。但仓库此前没有任何迁移创建这三行 ``data_sources``，
  新库或未采集过的业务库里适配器根本没有对应的启用行，行情指标永远停在"数据积累中"。
- 本迁移为三个 code 各播种一行：默认停用、固定官方 endpoint、内置适配器、空
  adapter_config。**不启用任何信源**：是否启用、是否启用某个指标由运营在信息源控制台
  与市场指标配置中显式决定，迁移不得代替运营做启用决策。

upgrade 语义（按 code 逐行处理，三行同一套逻辑）：
- 行不存在：按规范插入（source_type='market_data'、可信度 80、schedule='0 8 * * *'、
  enabled=false、adapter_status='builtin'、adapter_config='{}'、auth_type='none'），并写
  marker ``{"revision":"0062","was_inserted":true}``；空结果无副作用。
- 行存在且不处于目标态（builtin + 空 adapter_config + 固定 endpoint_url）：把原
  adapter_status / adapter_config / endpoint_url / adapter_version / adapter_published_at
  写入 ``login_config.market_sources_migration.before``，再置 adapter_status='builtin'、
  adapter_config='{}'、endpoint_url 为适配器真实 endpoint。
  **不修改** enabled / schedule / updated_at / source_type / credibility / name：
  运营已自定义的启停、周期与展示字段一律保留，避免播种动作改写运营语义。
- 已处于目标态的行 0 行更新、不写 marker（重复执行幂等）；已有 0062 marker 的行不重复
  覆盖，避免丢失 provenance。

downgrade 语义（保守、逐字段条件恢复）：
1. 先删除 ``was_inserted=true`` 的行：仅当该行仍停用（enabled=false）且下列 5 张引用
   ``data_sources.id`` 的表全部无引用时才删除——
   collection_runs / source_member_states / raw_signals（CASCADE）与
   data_source_audit_logs / source_onboarding_drafts（SET NULL）。
2. 仍有引用或已被运营启用的新建行：保留行与 marker，并把不可自动降级原因（enabled 与
   各表引用标志）写入 marker 的 ``downgrade_blocked``，方便人工处理。
3. ``was_inserted=false`` 的有效 marker：仅当字段当前值仍等于迁移写入值
   （adapter_status='builtin'、adapter_config='{}'、endpoint_url 固定）时才从 marker
   逐字段还原 before 原值；已被运营再次改写的字段保留现值；有效 marker 一律清理。
4. 目标行缺失时全部 no-op、不报错；不重开收尾任何历史数据。

endpoint_url 固定值逐字取自三个适配器的 ``endpoint`` 属性（测试用
``tests/test_market_sources_migration.py`` 断言二者一致，防止代码改了 endpoint 而迁移
仍播旧地址，导致内置采集静默失效）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0062"
MARKER_KEY = "market_sources_migration"
MARKET_SOURCE_TYPE = "market_data"
MARKET_CREDIBILITY = 80
# 日频行情（期货收盘 / 汇率日更 / 芯片指数收盘），统一每天 08:00 采集，默认停用。
MARKET_SCHEDULE = "0 8 * * *"

NAMES: dict[str, str] = {
    "commodity-futures": "大宗商品期货行情",
    "fx-rates": "国际汇率行情",
    "semiconductor-index": "国证半导体芯片指数行情",
}

# 逐字取自 app.signals.sources 中三个适配器的 endpoint。
ENDPOINTS: dict[str, str] = {
    "commodity-futures": "https://hq.sinajs.cn/list=",
    "fx-rates": "https://open.er-api.com/v6/latest/USD",
    "semiconductor-index": "https://hq.sinajs.cn/list=s_sz980017",
}

DESCRIPTIONS: dict[str, str] = {
    "commodity-futures": (
        "内置采集：新浪期货行情免认证（需 Referer、GBK 编码），取 15 个品种主力连续合约，"
        "每品种每日一条；供关键原材料价格/短缺维度使用。"
    ),
    "fx-rates": (
        "内置采集：open.er-api.com 免费免认证 JSON 实时汇率（以 USD 为基准），"
        "取 CNY/EUR/JPY/GBP 关键币种每日一条；供汇率与成本维度使用。"
    ),
    "semiconductor-index": (
        "内置采集：新浪简版行情国证半导体芯片指数（980017）点位与涨跌幅，每日一条；"
        "供半导体供应链景气维度使用。"
    ),
}

BLOCKED_REASON = (
    "迁移 0062 新建的行情信源行仍启用或存在引用，已保留行与 marker，未自动删除；"
    "请人工确认后再处理。"
)

CODES: tuple[str, ...] = tuple(ENDPOINTS)


def _params(code: str) -> dict[str, object]:
    return {
        "code": code,
        "name": NAMES[code],
        "source_type": MARKET_SOURCE_TYPE,
        "credibility": MARKET_CREDIBILITY,
        "schedule": MARKET_SCHEDULE,
        "endpoint": ENDPOINTS[code],
        "description": DESCRIPTIONS[code],
        "marker_key": MARKER_KEY,
        "marker_revision": MIGRATION_MARKER,
        "blocked_reason": BLOCKED_REASON,
    }


# 1. 行缺失时按规范插入默认停用的内置行情行，并写 was_inserted=true marker。
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

# 2. 行存在且不在目标态时：先记录完整前态，再归一为内置；不触碰
#    updated_at/enabled/schedule/source_type/credibility/name。
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
    for code in CODES:
        params = _params(code)
        connection.execute(_INSERT_MISSING, params)
        connection.execute(_CONVERT_EXISTING, params)


def downgrade() -> None:
    # 顺序固定：先删可删的新建行，再记录被阻止的新建行，最后还原既有行并清理 marker。
    connection = op.get_bind()
    for code in CODES:
        params = _params(code)
        connection.execute(_DELETE_INSERTED, params)
        connection.execute(_RECORD_BLOCKED, params)
        connection.execute(_RESTORE_EXISTING, params)
