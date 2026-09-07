# 风险基线受控重建运维手册

> **适用范围：** v1 仅允许隔离测试库 `supplier_risk_reset_test`。本文不授权开发、staging、生产或任何未知数据库的清理。

## 0. 边界与禁止项

- 工具只提供 PowerShell 本地编排，**不提供 Web API**。
- 仅接受 `-Environment test -DatabaseName supplier_risk_reset_test`；`staging`、`production` 和其他库名无条件硬阻断。
- 脚本不读取、打印或改写 `.env`、`DATABASE_URL`、密码、Token 或其他密钥；不要执行 `docker compose config`，它可能展开敏感变量。
- PowerShell 不内嵌 SQL、`DELETE`、`TRUNCATE CASCADE` 或序列重置；风险事务和 FK 逻辑只由 `app.maintenance.reset_risk_baseline` 核心处理。
- 工具不自动 migration、不在线重算旧数据、不启动 Scheduler/research-worker、不自动重新采集，也不访问公网。
- 备份、恢复收据、维护锁和证据仅放入被忽略的 `backups/` 或 `.omo/`，不得提交 Git。
- 没有成功的独立恢复演练、精确目标指纹、停止的写进程、FK 盘点、维护锁和精确确认短语时，禁止执行 `-Execute`。

## 1. 脚本接口与退出码

入口：`scripts/reset-risk-baseline.ps1`。

| 参数 | 说明 |
| --- | --- |
| `-Environment` | 必填，接受 `test`、`staging`、`production`；v1 只有 `test` 可继续。 |
| `-DatabaseName` | 必填，必须精确为 `supplier_risk_reset_test`。 |
| `-DryRun` | 显式只读预检。省略 `-Execute` 时默认等价于此模式。 |
| `-Execute` | 显式提交请求；必须同时通过所有执行门禁。 |
| `-BackupPath` / `-RestoreReceiptPath` | `-Execute` 必填；前者限 `backups/`，后者限 `backups/` 或 `.omo/`。 |
| `-ConfirmPhrase` | `-Execute` 必填，大小写和空格精确匹配 `RESET RISK BASELINE supplier_risk_reset_test`。 |
| `-MaintenanceLockPath` | 可选；默认 `.omo/risk-baseline-rebuild.lock`。 |
| `-ComposeProjectName` | 可选；默认 `supplierriskmonitoring`。 |
| `-EvidenceDirectory` | 可选；默认 `.omo/evidence/risk-signal-validity/task-12`。 |

`-DryRun` 与 `-Execute` 同时出现会返回 `2`，不会猜测操作者意图。

| 退出码 | 含义 |
| --- | --- |
| `0` | dry-run 已真实连接指定隔离库，确认 Compose 写进程全部停止并通过核心 preflight、FK 盘点、目标指纹和 `plan_sha256`；或 Execute 收据通过全部校验。 |
| `2` | 参数、Docker、隔离库、核心 preflight、FK、收据、写进程、锁、目标指纹、计划摘要或执行收据不可用/不一致。`2` 是 **blocked/inconclusive**，不会调用破坏性核心操作。 |

每次调用都输出机器可读 JSON 摘要；有效目标还会以无 BOM UTF-8 写入 `reset-risk-baseline-summary.json`。真实 preflight 和执行时分别保留核心 JSON 收据。

## 2. 已实现的后端核心 CLI 契约

脚本通过一次性 Compose 容器调用核心，所有值均作为参数数组传递，不经 shell 拼接。

### 2.1 只读 preflight

```text
python -m app.maintenance.reset_risk_baseline preflight \
  --environment test \
  --database-name supplier_risk_reset_test \
  --output json
```

PowerShell 对应的容器调用使用 `docker compose run --rm --no-deps --no-TTY --entrypoint python app ...`。`--no-deps` 防止脚本隐式启动 PostgreSQL、app、scheduler 或 worker；核心必须自行验证连接实际指向 `supplier_risk_reset_test`。

preflight 标准输出是单一 JSON 对象，至少包含既有的 `schema`、`status=passed`、`mode=preflight`、`target`、`fk_inventory` 和 `plan_sha256`：

```json
{
  "schema": "supplier-risk-monitoring/risk-baseline-preflight/v1",
  "status": "passed",
  "mode": "preflight",
  "target": {
    "environment": "test",
    "database_name": "supplier_risk_reset_test",
    "fingerprint": "<64 位 SHA-256>",
    "migration_version": "0048_source_membership_state",
    "critical_table_counts": {
      "raw_signals": 0,
      "ai_analysis_records": 0,
      "risk_events": 0,
      "risk_alerts": 0,
      "notification_deliveries": 0,
      "notification_runtime_state": 0,
      "collection_runs": 0,
      "source_member_states": 0,
      "research_claims": 0,
      "research_claims_promoted_nonnull": 0
    }
  },
  "plan_sha256": "<64 位 SHA-256>",
  "fk_inventory": {
    "policy": "supplier-risk-monitoring/risk-baseline-fk-allowlist/v1",
    "status": "passed",
    "observed_edges": [],
    "missing_edges": [],
    "unexpected_edges": []
  }
}
```

`fk_inventory` 必须零漂移，并至少观察到：

```text
notification_deliveries.alert_id -> risk_alerts.id ON DELETE SET NULL
research_claims.promoted_signal_id -> raw_signals.id ON DELETE SET NULL
```

其他批准的风险域 FK 同样必须由核心的版本化允许列表覆盖；任何新增、缺失或变更均阻断。

### 2.2 Execute 与只读 mounts

执行时，PowerShell 先将已解析的绝对路径安全地组成两个**独立 Docker 参数**：

```text
--volume <backupFull>:/run/risk-baseline/backup:ro
--volume <receiptFull>:/run/risk-baseline/restore-receipt.json:ro
```

然后调用：

```text
python -m app.maintenance.reset_risk_baseline execute \
  --environment test \
  --database-name supplier_risk_reset_test \
  --output json \
  --expected-fingerprint <preflight.target.fingerprint> \
  --expected-plan-sha256 <preflight.plan_sha256> \
  --confirm-phrase "RESET RISK BASELINE supplier_risk_reset_test" \
  --backup-path /run/risk-baseline/backup \
  --restore-receipt-path /run/risk-baseline/restore-receipt.json \
  --active-writer-count 0
```

核心必须在**单一事务**内再次验证目标、指纹、`plan_sha256`、FK 集合、备份 SHA-256 与大小、恢复演练收据、写进程计数和确认短语；全部通过后才按 FK 安全顺序执行删除，任一失败立即回滚并返回 `2`。

执行收据（schema `supplier-risk-monitoring/risk-baseline-execution-receipt/v1`）必须至少提供：

- `status=completed`、`dry_run=false`、`environment=test`、`database_name=supplier_risk_reset_test`；
- `pre_reset_fingerprint` 与 `pre_reset_plan_sha256`（清空前目标指纹与计划摘要）；
- `claim_provenance_sha256`（清空前 research claim 溯源清单摘要）；
- 完整 `deleted_counts`，覆盖 notification deliveries、alerts、matches、events、AI、signals、collection runs 和 `source_member_states`；
- `preserved_counts`，至少包含 `research_claims`；
- `claim_count`（pre-reset research claim 行数）与 `promoted_claim_count=0`。

编排脚本会将删除计数与 preflight 的关键表计数逐项对比，验证 `notification_deliveries` 的总数（包括 `alert_id IS NULL` 摘要行）被完整清除，并验证 `research_claims` 行数保留、promoted link 归零。它不会接受只输出“成功”文本的核心响应。

## 3. 制作 PostgreSQL 自定义格式备份

以下命令只适用于已经明确承载测试库的 `postgres` 服务。数据库名是固定允许值，不从用户输入拼接。

```powershell
$backupDirectory = Join-Path $PWD 'backups'
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
$backupPath = Join-Path $backupDirectory (
    'supplier_risk_reset_test-{0}.backup' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
)

docker compose --project-name supplierriskmonitoring exec -T postgres sh -c `
  'pg_dump -U "$POSTGRES_USER" -d supplier_risk_reset_test -Fc -f /tmp/supplier_risk_reset_test.backup'
if ($LASTEXITCODE -ne 0) { throw 'pg_dump 失败。' }

docker compose --project-name supplierriskmonitoring cp `
  postgres:/tmp/supplier_risk_reset_test.backup $backupPath
if ($LASTEXITCODE -ne 0) { throw '复制备份失败。' }

$backup = Get-Item -LiteralPath $backupPath
if ($backup.Length -le 0) { throw '备份文件为空。' }
$backupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath).Hash.ToLowerInvariant()
```

仅记录备份路径、字节数和 SHA-256；不要记录用户名、密码或连接串。

## 4. 独立恢复演练与收据

恢复演练不能覆盖目标库或业务库。每次使用新建、经过名称审查的库，例如 `supplier_risk_restore_task12_20260907`。如果 `createdb` 提示已存在，停止；不要覆盖或 drop 该恢复库。

```powershell
docker compose --project-name supplierriskmonitoring cp `
  $backupPath postgres:/tmp/supplier_risk_restore_task12_20260907.backup
if ($LASTEXITCODE -ne 0) { throw '复制恢复演练备份失败。' }

docker compose --project-name supplierriskmonitoring exec -T postgres sh -c `
  'createdb -U "$POSTGRES_USER" supplier_risk_restore_task12_20260907'
if ($LASTEXITCODE -ne 0) { throw '恢复演练数据库已存在或无法创建。' }

docker compose --project-name supplierriskmonitoring exec -T postgres sh -c `
  'pg_restore -U "$POSTGRES_USER" -d supplier_risk_restore_task12_20260907 --no-owner --exit-on-error /tmp/supplier_risk_restore_task12_20260907.backup'
if ($LASTEXITCODE -ne 0) { throw 'pg_restore 恢复演练失败。' }
```

通过已批准的只读核心检查分别取得源库和恢复库的迁移版本、目标指纹和关键表计数。恢复库可有不同的目标指纹；但迁移版本和全部关键计数必须与源库一致。将结果写入无 BOM JSON 收据：

```json
{
  "schema": "supplier-risk-monitoring/risk-baseline-restore-receipt/v1",
  "status": "passed",
  "backup": {
    "sha256": "<备份的小写 64 位 SHA-256>",
    "size_bytes": 12345
  },
  "source_target": {
    "environment": "test",
    "database_name": "supplier_risk_reset_test",
    "fingerprint": "<源库目标指纹>",
    "migration_version": "0048_source_membership_state",
    "critical_table_counts": { "...": 0 }
  },
  "restored_target": {
    "environment": "test",
    "database_name": "supplier_risk_restore_task12_20260907",
    "fingerprint": "<恢复库目标指纹>",
    "migration_version": "0048_source_membership_state",
    "critical_table_counts": { "...": 0 }
  }
}
```

`critical_table_counts` 必须包含 §2.1 的十个键，不能使用省略号。用以下方式写入，以保证无 BOM：

```powershell
[System.IO.File]::WriteAllText(
    $receiptPath,
    "$($receipt | ConvertTo-Json -Depth 12)$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)
```

## 5. 真实 dry-run

dry-run 不是离线语法演示；它必须真实连接目标隔离库，确认 Compose 写进程全部停止，并以只读方式完成核心 preflight：

```powershell
pwsh -NoProfile -File scripts/reset-risk-baseline.ps1 `
  -Environment test `
  -DryRun `
  -DatabaseName supplier_risk_reset_test `
  -EvidenceDirectory .\.omo\evidence\risk-signal-validity\task-12\happy
```

省略 `-DryRun` 的默认模式相同。可接受的 `0` 输出必须包含：

- `status=dry-run`；
- `compose_writers=passed`（app、scheduler、research-worker 均已停止）；
- `core_preflight=passed`、`fk_inventory=passed`；
- `target_fingerprint=passed`、`plan_sha256=passed`；
- `plan.preflight_plan_sha256` 为 64 位摘要。

Docker 不可用、隔离库不存在、app/scheduler/worker 未停、核心 CLI 不可用、预检 JSON 不完整或 FK 漂移均返回 `2`；这些结果只能保存到 `failure/` 或 `inconclusive/`，不能作为 happy 证据。

## 6. 停写与显式 Execute

在维护窗口先停止所有写进程，不要删除 PostgreSQL volume：

```powershell
docker compose --project-name supplierriskmonitoring stop app scheduler
if ($LASTEXITCODE -ne 0) { throw '停止 app 或 scheduler 失败。' }

docker compose --project-name supplierriskmonitoring --profile research-local-test stop research-worker
if ($LASTEXITCODE -ne 0) { throw '停止 research-worker 失败。' }
```

之后执行：

```powershell
pwsh -NoProfile -File scripts/reset-risk-baseline.ps1 `
  -Environment test `
  -DatabaseName supplier_risk_reset_test `
  -Execute `
  -BackupPath .\backups\supplier_risk_reset_test-20260907-120000.backup `
  -RestoreReceiptPath .\backups\supplier_risk_reset_test-20260907.restore-receipt.json `
  -ConfirmPhrase 'RESET RISK BASELINE supplier_risk_reset_test' `
  -MaintenanceLockPath .\.omo\risk-baseline-rebuild.lock `
  -ComposeProjectName supplierriskmonitoring `
  -EvidenceDirectory .\.omo\evidence\risk-signal-validity\task-12\execute
```

执行前脚本会在独占锁内重新查询写进程，运行真实 preflight，比较恢复收据中的源目标指纹/迁移/计数，并将 preflight 的 `plan_sha256` 与目标指纹传给核心。任一失败立即返回 `2`，不调用 execute。

执行语义必须保持：

- `notification_deliveries` **全量**删除，包含所有 nullable `alert_id` 摘要行；
- 通知发送游标、聚合状态和 `source_member_states` 随风险基线清理；
- `research_claims` 与其他保留域不重置，`promoted_signal_id` 全部归零；
- identity 不重置；不自动联网或启动重新采集。

## 7. 回滚与离线假数据重采集

**事务提交前失败：** 保持写进程停写，不手工补删；保留预检/错误收据，确认事务回滚后重新做备份和恢复演练。

**提交后发现错误：** 仅针对隔离测试库，停止所有写进程，按已演练的自定义格式备份做整体恢复。不要逐表逆向修补，也不要对业务库执行恢复。

**离线假数据重采集：** 它是执行成功后的独立批准步骤：

1. 保持 scheduler 和 research-worker 停止，确认测试目标及迁移版本；
2. 使用 `backups/task-12-fake-signals.json` 等固定本地夹具，记录 SHA-256；夹具不含真实敏感数据或公网 URL；
3. 仅启动不自动 migration 的临时本地 app，强制 `AI_PROVIDER=fake`、`SEARCH_PROVIDER=none`，并停用拉取式信源；
4. 通过既有的已认证 localhost 手工 JSON 导入能力提交夹具，不调用 `POST /api/v1/sources/{id}/run`；
5. 仅处理新导入的 fake 数据，不在线重算旧数据；完成后停止临时 app。

## 8. 证据与清理收据

最低证据：

- `backups/` 中的自定义格式备份、SHA-256 和无 BOM 恢复演练收据；
- `.omo/evidence/risk-signal-validity/task-12/happy/script-dry-run.txt`：仅真实隔离库 preflight 返回 `0` 时允许存在；
- `.omo/evidence/risk-signal-validity/task-12/failure/`：参数、核心、Docker 和目标不可用的 blocked/inconclusive 结果；
- 核心 preflight/execute JSON、`plan_sha256`、删除计数、保留摘要和 claim 溯源 SHA-256；
- 最终摘要 `cleanup.maintenance_lock=removed`。出现 `cleanup-failed` 时停止后续操作并人工核实锁。
