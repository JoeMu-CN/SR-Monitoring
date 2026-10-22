# 信号链路重构方案（待批准）

> 依据：10-21 13:44 CST 实测。候选 3,896 条中 95% 为清单/灾害类噪声；事件时延 P50 5.3 天、P90 26 天；10-14 后 LLM 实际处理 0–2 条/天。
> 先纠正我上一轮的一个说法：复核后，那 416 条「分析成功但未建事件」的信号**已经不在候选里**（已过期/无关，`process_event` 正常路径一定会建事件并写关联）。所以「死信重排」目前没有实例，方案里不再为它单独设计。

---

## 一、范围

**目标（可验收）**

1. 确定性过滤移出消费队列：队列里只剩「必须由 LLM 判断」的信号。
2. 消费与采集解耦：独立循环 + 时间预算，不再由采集触发。
3. 业务源优先且不反向饿死：政策/监管/事故先于清单/灾害。

**明确不做**

- 事件 → 告警 → 推送断点（357 事件 / 1,110 alerts / 9 条投递），另开一轮排查。
- LLM 提示词与评分模型。
- 前端改版（后端接口保持兼容，只在健康聚合里加字段）。
- 删除 OFAC 历史数据（只改状态，不删行）。

---

## 二、目标架构

```
采集（每源独立 cron）
  └─ 入库（dedup_key 唯一约束去重）
      └─ 确定性预筛（新增，批量）
          ├─ 命中 → 批量写过滤记录 + validity_state=irrelevant
          └─ 未命中 → 进入待分析队列
消费循环（新增，独立 job，20s 间隔 / 单轮时间预算）
  └─ ORDER BY priority DESC, collected_at ASC
      └─ 行情评估 / LLM → process_analysis → 事件
```

核心变化只有一句：**把「过滤」从消费时逐条做，改成入队时批量做。**

---

## 三、P0：预筛前移 + 独立消费（零数据库迁移）

### 3.1 新增 `backend/app/signals/prefilter.py`

- `load_supplier_index(session)`：一次性加载启用供应商的 `legal_name` + aliases，构建规范化集合。
  替代 `jobs.py:99 _matches_any_supplier` 的「每条信号全表扫描供应商」。
- `prefilter_created_signals(session, signal_ids, *, rules, supplier_index)`：
  - 顺序与现网一致：`relevance → list 源 → structured grade`（行情分支仍留在消费链，数量少且依赖登记指标）。
  - 过滤项批量写 `AIAnalysisRecord`（`executemany`，每批 1,000）+ 分片 `UPDATE raw_signals`（每批 2,000）置 `irrelevant` 与 `validity_reason`。
  - `filter_kind` / `model` / `prompt_version` 取值与现在完全一致，前端筛选不受影响。
  - 只处理「本次新入库」的 id，不遍历历史。

### 3.2 改造 `backend/app/signals/relevance.py`

从 `assess_signal_relevance(session, title, content)` 拆出纯函数：

```python
def assess_text_relevance(title, content, *, rules, suppliers) -> RelevanceDecision
```

原函数保留为薄包装（先加载 rules/suppliers 再调用），现有测试与调用方零改动。判定逻辑一行不改，只去掉函数内部的 DB 查询。

### 3.3 改造 `backend/app/signals/service.py` / `ingestion.py`

- `persist_signal_ingestions` 返回 created ids（利用 `INSERT ... ON CONFLICT DO NOTHING RETURNING id`）。
- `collect_source_async` 在 commit 后调用预筛；开关 `SIGNAL_PREFILTER_AT_COLLECT`（默认 true）。
- 结果写入 `CollectionRun` 的汇总日志：`prefiltered=N kept=M`。

### 3.4 新增 `backend/app/scheduler/consume.py`

- `consume_pending_signals_job()`：
  - `pg_try_advisory_lock(hashtext('pending_signals'))`，拿不到直接返回；`try/finally` 释放。多副本安全，替代 `jobs.py:114` 的进程内 `Lock`。
  - `deadline = now + SIGNAL_CONSUME_TIME_BUDGET_SECONDS`，循环取小批（默认 5 条），超预算即停。
  - 每轮写 `runtime.record_job_result`（processed / filtered / failed）与 `current_item_id`。
- `jobs.py`：把「取候选 + 逐条处理」抽成 `process_signal_batch(limit)`；`_process_pending_signals` 保留为兼容入口（P0 不删）。
- **删除**三处采集后调用：`jobs.py:206`、`jobs.py:280`、`jobs.py:617`。这是解耦关键。

### 3.5 改造 `backend/app/scheduler/main.py`

```python
scheduler.add_executor("signal_consume", ThreadPoolExecutor(max_workers=1))
scheduler.add_job(
    consume_pending_signals_job,
    IntervalTrigger(seconds=SIGNAL_CONSUME_INTERVAL_SECONDS),
    id="signal-consume", executor="signal_consume",
    max_instances=1, coalesce=True,
)
```

采集与消费使用不同 executor，LLM 阻塞不再占用采集线程。

### 3.6 观测 `backend/app/scheduler/health.py`

- 复用 `pending_signal_candidate_id_select` 计数（`health.py:347`），新增一个按 `source_type` 的 `GROUP BY`，仅供后续展示；响应结构只增字段，不改既有字段。

**回滚**：`SIGNAL_CONSUME_ENABLED=false` + `SIGNAL_PREFILTER_AT_COLLECT=false`，代码保留旧路径一版。

---

## 四、P1：优先级 + aging（一次迁移）

### 4.1 迁移 `backend/alembic/versions/0065_raw_signal_priority.py`

```sql
ALTER TABLE raw_signals ADD COLUMN priority SMALLINT NOT NULL DEFAULT 50;
CREATE INDEX CONCURRENTLY ix_raw_signals_priority_collected
  ON raw_signals (priority DESC, collected_at ASC);
```

回填分片（每批 5,000，`WHERE priority = 50`）：

| 来源类别 | priority |
|---|---|
| 清单类（ofac/bis/uflpa/mofcom-entity） | 10 |
| 天气/地震/官方 API | 20 |
| 其他业务 | 50 |
| 政策 / 监管 / 事故 / 地缘 | 70 |
| 命中供应商名（采集时判定） | 90 |

### 4.2 新增 `backend/app/signals/priority.py`

`compute_priority(signal, source, *, supplier_hit, rules) -> int`：

```
基础分 = source_tiers[source_type]（默认表见上）
+20 命中供应商（legal_name/alias）
+10 命中 high_impact 关键词
+10 命中 priority_countries
+10 剩余有效期 < 24h
上限 100
```

`source_tiers` 加进 `rule_dimension_configs(key='signal-filter')`，复用现有 60s TTL 缓存，**不再加 env 或迁移**。

### 4.3 排序与 aging

- `pending_signal_candidate_select` 改为 `ORDER BY priority DESC, collected_at ASC`。
- 新增每小时 job：对等待超过 2 小时且 `priority < 90` 的候选 `priority = LEAST(priority + 5, 90)`，单次限 5,000 行。低优先级信号最多 2 小时后开始持续提权，不会被饿死。

---

## 五、P2：LLM 并发与退避（可选）

- `analyze_raw_signal` 目前自己 commit/rollback（`ai/service.py:59-127`）。并发前必须改为「调用方管理会话」，再由 `SIGNAL_LLM_CONCURRENCY`（默认 1，上限 4）控制并发。
- 单条超时 `SIGNAL_LLM_TIMEOUT_SECONDS`（默认 45；现网实测 max 43s）。
- 失败退避：`failed` 记录加冷却（复用 `finished_at + cooldown`），3 次后置 `classification_failed`，不再每轮重试。

---

## 六、验证

**新增用例**

| 文件 | 覆盖 |
|---|---|
| `tests/test_signal_prefilter.py` | 清单源未命中供应商不进候选；relevance 不相关不再消费；命中供应商保留 |
| `tests/test_consume_loop.py` | 时间预算耗尽即停；advisory lock 互斥；采集后不再自动触发 |
| `tests/test_signal_priority.py` | tier、加分成、排序、aging 上限 |
| `tests/test_health_queue_breakdown.py` | 队列深度按 source_type 汇总，响应兼容 |

**回归（必须全绿）**

`test_market_pipeline.py`、`test_scheduler_registry.py`、`test_signal_irrelevant_write_path.py`、`test_list_filter.py`、`test_analysis_result_null_semantics.py`、`test_monitoring_health.py`、`test_risk_validity_e2e.py`。

**手动验收**

1. `ruff check .`、`mypy`（改动模块）。
2. 重建 scheduler，观察 200 行日志：采集日志不再跟「相关性预过滤跳过 N 条」，`signal-consume` 每 20s 有记录。
3. 观测表：`pending_signals` 的 `processed_count` 持续 > 0，`current_item_id` 变化，无 `failed`。
4. 对比改前/改后：候选数、政策类最老年龄、每小时 LLM 调用数。

---

## 七、验收标准

| 指标 | 现状 | 目标 |
|---|---|---|
| 队列深度（需判断） | 3,896（95% 噪声） | < 100，24h 内清空 |
| 政策类入队 → 分析完成 | 最老 8 天 | P95 < 10 分钟 |
| LLM 成功调用 | 0–2/天 | ≥ 到达量（约 40/天） |
| 事件时延 P50（不含源侧） | 5.3 天 | < 2 小时 |
| 单轮耗时 | 1.3s（20 条全过滤） | < 预算，无跳过 |

---

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| 预筛前移改变过滤语义 | 判定逻辑零改动，只换执行位置；`filter_kind` 取值不变；灰度开关 |
| 首次批量预筛拖慢采集 | 内存供应商索引 + 分批 1k/2k；预计万条 < 5s |
| 低优先级饿死 | aging（2h 后每轮 +5，上限 90） |
| 多副本重复消费 | DB advisory lock；进程内 Lock 仅作快路径 |
| LLM 并发触发限流 | 默认 1，按供应商限流逐步放 |
| 回填锁表 | 分批 5k；索引 `CONCURRENTLY` |

---

## 九、工作量

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0 | 预筛前移 + 独立消费 + 测试 | 1–1.5 天 |
| P1 | 优先级 + aging + 迁移回填 | 0.5–1 天 |
| P2 | LLM 并发/超时/退避 | 0.5 天 |
| 回归 + 手动验收 | 定向用例 + 容器验证 | 0.5–1 天 |

---

## 十、已确认的决策（2026-10-21）

1. **OFAC 历史 19k 条**：同意一次性批量置 `irrelevant`（不删数据，复核页可找回）。执行时机放在 P1 迁移完成后，用批量 SQL 按其现有 `validity_reason` / 过滤记录收敛状态。
2. **nmc-weather**：同意从 `*/5` 降到 `*/30`。改 `data_sources.schedule` 即可，调度器每分钟刷新注册表会自动重排。
3. **分析成功但未匹配供应商**：**不新增任何复核标记**。现有实现已经满足：
   - `risks/engine/processing.py:343-348` 已写 `analysis.needs_review = True` + `review_reason = "AI 分析成功但未匹配到供应商，未生成风险提醒"`；
   - `process_event` 一定会写 `risk_event_signals`（`processing.py:264-271`），信号因此被 `_risk_processed_signal_ids()` 排除，本来就不会重排；
   - 复核列表按 `AIAnalysisRecord.needs_review.is_(True)` 查询（`ai/router.py:97/108/140`），记录天然可见。
   结论：该决策点无需改代码，维持现状即可。
4. **上线节奏**：P0 + P1 一起上。

---

## 十一、执行状态（已实现并验证）

**已落地代码**

- 前置：`config.py` 6+1 个开关（含 `SIGNAL_PRIORITY_AGING_HOURS`）、`signals/models.py` 的 `priority` 列、`compose.yaml` 的调度器环境变量。
- Lane A（signals 层）：`prefilter.py`、`priority.py`、`relevance.py` 拆分（`SupplierProfile` / `assess_text_relevance` / `supplier_name_index` / `source_tiers`）、`ingestion.py` 返回 created ids、`service.py` 采集提交后「预筛 + 打分」、迁移 `0065`。
- Lane B（scheduler 层）：`consume.py`（advisory lock 长期连接 + 时间预算 + 分批）、`main.py` 独立 executor 与两个新 job、`jobs.py` 抽出 `process_signal_batch` + 优先级排序 + aging + 删除三处采集后调用 + 候选查询加 `LIMIT batch*20`、`health.py` 的 `queue_by_source_type`。
- 合并期修正：
  1. `assign_signal_priorities` 落库调用（Lane A 只写了纯函数）；
  2. 带 `source_extra` 的行情信号跳过预筛，避免 relevance 误杀行情事件；
  3. advisory lock 改由 `engine.connect()` 的长连接持有（Session commit 会归还连接，导致解锁落在别的连接、锁残留）；
  4. 消费观测改为「每批 started + 循环结束一次累计 result」。

**验证**

- `ruff check app tests`：All checks passed。
- 隔离测试栈（`scripts/test-backend.ps1`，`alembic upgrade head` 至 0065）跑 13 个定向文件：全绿，健康检查 `{"database":"supplier_risk_test","pytest":"passed","health":"ok"}`。
- 覆盖：预筛三链、优先级分档/加分、消费预算与锁跳过、队列按源汇总、采集不再触发消费、行情/清单/有效性/事件链路回归。

**已执行（2026-10-22）**

1. 镜像重建、迁移至 0065、`app` + `scheduler` 重启，健康检查 ok。
2. OFAC 历史收敛：预检 4,437 条、命中供应商 0 条，批量写过滤记录并置 `irrelevant`；待处理归零。
3. `nmc-weather` 调度改 `*/30`，05:00 已按新周期触发。
4. 观察：队列候选 3,896 → 0；LLM 调用恢复至 92–216 条/小时；`signal-consume` 每 20 秒稳定运行。

---

## 十二、状态模型重构（2026-10-22）

### 问题
`raw_signals.validity_state` 一个字段混了三种语义：生命周期、处理阶段、过滤结论，导致「数据积累中」被展示为「不相关」。

### 目标模型

| 字段 | 取值 | 含义 |
|---|---|---|
| `validity_state` | `active / expired / superseded / revoked / conflicted / legacy`，或 **NULL** | 只表达生命周期；未定时为 NULL |
| `processing_state` | `pending_classification / analyzed / filtered / no_trigger / classification_failed` | 处理进度与结论 |

语义拆分：

- `filtered`：确定性过滤，业务无关（相关性/清单/结构化）。
- `no_trigger`：已确定性评估、本次未触发（行情数据积累中、未达阈值）。
- `analyzed`：已进入事件链。
- `classification_failed`：分类失败，等待重试。

### 候选与判活口径

- 分析候选：`processing_state='pending_classification'` 且 `validity_state IS NULL OR validity_state='active'`，再叠加有效期。
- 有效信号：`validity_state='active'` 且 `processing_state IN ('pending_classification','analyzed')`。
- `filtered` / `no_trigger` 不计有效证据，但不改生命周期。

### 历史数据回填

1. `pending_classification` → `validity_state=NULL`，`processing_state='pending_classification'`。
2. `irrelevant` → 按 `details.filter` 映射 `filtered` / `no_trigger`；生命周期按 `valid_until` / `validity_mode` 推断。
3. 有 `risk_event_signals` → `analyzed`（覆盖）。
4. 有 `market-eval-v1` 记录且未 analyzed → `no_trigger`。
5. `validity_reason.code='classification_failed'` → `classification_failed`。

### 兼容与回滚

- `validity_state` 的旧值（`pending_classification`/`irrelevant`）与宽松约束保留一个版本；新代码不再写入。
- 新列有默认值，回退镜像时旧代码可忽略；迁移 `0066` 的 downgrade 可恢复旧语义。
- 稳定运行一周后再收紧约束、删除旧值分支。

### 涉及文件

- 写入/迁移：`signals/validity.py`、`signals/models.py`、`alembic/versions/0066_*`、`signals/ingestion.py`、`signals/prefilter.py`、`scheduler/jobs.py`、`risks/signal_validity.py`、`risks/service.py`、`ai/service.py`、`signals/{lifecycle,supersession,membership_repository}.py`
- 读取/API：`risks/query_validity.py`、`scheduler/health.py`、`risks/{router,schemas}.py`、`signals/{router,schemas}.py`
- 前端：`api.ts`、`ValidityStateBadge.tsx`、`SourceSignalsTable.tsx`、`SourceSignalsView.tsx`、`CurrentRisksView.tsx`、`RiskDetailView.tsx`、`RiskDetailEvidenceSections.tsx`、`types.ts`


---

**改动文件清单（P0）**

```
新增  backend/app/signals/prefilter.py
新增  backend/app/scheduler/consume.py
改动  backend/app/signals/relevance.py      （拆纯函数，逻辑不变）
改动  backend/app/signals/service.py        （返回 created ids + 调预筛）
改动  backend/app/signals/ingestion.py      （RETURNING id）
改动  backend/app/scheduler/jobs.py         （抽 batch 函数、删 3 处调用）
改动  backend/app/scheduler/main.py         （独立 executor + consume job）
改动  backend/app/scheduler/health.py       （队列按源汇总）
改动  backend/app/config.py                 （4 个开关）
新增  backend/tests/test_signal_prefilter.py
新增  backend/tests/test_consume_loop.py
```

P1 额外：`alembic/versions/0065_*.py`、`signals/models.py`、`signals/priority.py`、`scheduler/jobs.py`（排序/aging）。
