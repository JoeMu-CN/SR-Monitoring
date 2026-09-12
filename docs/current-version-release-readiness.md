# 当前版本发布就绪台账（隔离候选）

> 所属计划：`.omo/plans/current-version-hardening.md` 任务 12（对应 M7 发布与恢复）。
> 生成日期：2026-09-12。当前门禁 run_id：`20260912T032041201Z-022978a3`。
> 机器可读证据：[readiness-check.json](.omo/evidence/task-12-current-version-hardening/20260912T032041201Z-022978a3/readiness-check.json)、[summary.json](.omo/evidence/task-12-current-version-hardening/20260912T032041201Z-022978a3/summary.json)。

## 1. 结论摘要

状态词仅使用三档且互不替代：**实现完成**、**隔离候选验收通过**、**生产现场验收通过**。

| 结论项 | 状态 | 说明 |
| --- | --- | --- |
| 实现完成（M1–M7） | 实现完成 | 代码/测试/脚本已按计划交付 |
| 隔离候选验收通过 | 隔离候选验收通过 | 专用 Compose 项目 + 临时镜像 + 独立测试库；全量门禁复跑全绿（第 3 节） |
| 生产现场验收通过 | pending | 无生产现场证据，不宣称 |
| 备份/恢复（当前候选自恢复） | 通过 | 任务 11 恢复演练 `status=passed`、`verification.passed=true` |
| 回滚兼容 smoke | blocked | 无可用上一已验证候选镜像（`no-previous-verified-candidate-image-available`） |
| `release_candidate_ready` | **false** | 因回滚 blocked |
| `production_release_authorized` | **pending** | 未获生产发布授权 |

回滚 blocked 是当前唯一阻止 `release_candidate_ready=true` 的硬缺口。**不得**据此台账宣称可回滚或生产现场验收通过。

## 2. 代码/工作区与构建指纹

| 项 | 值 |
| --- | --- |
| Git HEAD | `aa3b015912c88fd157b197ddc3cea990051ee2fa` |
| 构建源指纹 source_sha256 | `5818621c8686e79f4ba11a4e8ac13aa9a5e2b65b1d06d651275f082d5f189689` |
| 候选镜像 reference | `supplierriskmonitoring-hardening-test:20260912T032041201Z-022978a3` |
| 候选镜像 ID | `sha256:ba69bc9f21609f2c26bdf65e39773283ec117b5469b0ff2c66d17bc4ee4d44ff` |
| 迁移 head | `0049_scheduler_runtime` |
| 门禁 run_id | `20260912T032041201Z-022978a3` |
| 临时镜像清理 | 已删除（`cleanup.image_removed=true`） |

说明：source_sha256 由门禁脚本对 `Dockerfile`、`backend`、`frontend` 的跟踪文件全集计算；本台账与 README 的文档改动不在该指纹范围，且按约束未提交 Git（本工作区改动仅这两个允许路径）。

## 3. 隔离候选全量门禁（当前证据）

真实命令：

```
pwsh -File scripts/test-current-version-hardening.ps1 -Suite all
```

本轮结果（exit 0，started 2026-09-12 11:20:42 +08:00，finished 11:35:50）：

| 阶段 | 计数 | 失败 | 跳过 |
| --- | --- | --- | --- |
| 后端（baseline 1 + legacy 829 + hardening 9） | 839 | 0 | 0 |
| 前端单测 | 269 | 0 | 0 |
| 浏览器 E2E（8 个 spec） | 26 | 0 | 0 |

- 前端 `typecheck` 与 `build` 均通过；`current-version-hardening.spec.ts` 11/11 通过。
- 原始证据：[summary.json](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/summary.json)、[后端 legacy JUnit](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/junit-backend-legacy.xml)、[后端 baseline JUnit](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/junit-backend-baseline.xml)、[后端 hardening JUnit](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/junit-backend-hardening.xml)、[前端 JUnit](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/frontend-junit.xml)、[Playwright 报告](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/playwright-current-version-hardening.json)。
- 门禁结束清理核验：专用项目容器/网络/卷均为 0，临时镜像已删除，`127.0.0.1:18080` 空闲（见 summary.json 的 cleanup 字段与 readiness-check.json 的 cleanup 记录）。

## 4. 开关清单（仅名称与状态，不含任何密钥或 URL）

| 开关 | 状态 | 范围/说明 |
| --- | --- | --- |
| `AI_PROVIDER` | fake | 隔离候选与本地默认；未接真实模型 |
| `SEARCH_PROVIDER` | none | 研究搜索停用 |
| `RESEARCH_TRACK_ENABLED` | true（本地默认） | 生产发布候选必须显式关闭（尚未执行）；控制研究路由/权限/研究调度 |
| `RESEARCH_WORKER_ENABLED` | false | research-worker 不启动 |
| `RESEARCH_MONTHLY_ENABLED` | false | 月报研究调度停用 |
| `RESEARCH_DAILY_TOPIC` / `RESEARCH_WEEKLY_TOPIC` / `RESEARCH_SCHEDULE_OWNER_USERNAME` | 未配置（空） | 日/周研究调度停用 |
| `RESEARCH_CRAWL4AI_ENABLED` / `MONITOR_CRAWL4AI_ENABLED` | false | 浏览器回退停用 |
| `NOTIFY_ENABLED` | 未设置（默认 false） | 无真实外发 |
| `NOTIFY_DINGTALK_ENABLED` / `NOTIFY_FEISHU_ENABLED` / `NOTIFY_SERVERCHAN_ENABLED` / `NOTIFY_PUSHPLUS_ENABLED` | false | 隔离栈显式关闭；仅 FakeProvider 参与验收 |
| `SCHEDULER_COLLECT_CRON` / `SCHEDULER_EXPIRE_CRON` / `SCHEDULER_CLEANUP_CRON` | 默认示例值 | 兜底采集 / 提醒失效 / 保留清理；ECS 现场值待确认 |
| `RETENTION_SIGNAL_DAYS` / `RETENTION_EVENT_DAYS` / `RETENTION_RUN_DAYS` | 90 / 90 / 30 | 保留策略默认矩阵，本期不变 |
| `SESSION_SECURE_COOKIE` | false | 本地/隔离；生产必须 true（待 HTTPS 现场配置） |
| `APP_ENV` | development（本地）/ test（隔离栈） | 生产环境校验另有门禁 |
| `ALLOWED_ORIGINS` | 未配置 | 生产必须配置为 HTTPS 来源（待现场配置） |

## 5. M1–M7 能力、证据与真实命令

### M1 无损编辑（任务 2）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_supplier_lossless_edit.py,tests/test_suppliers_api.py,tests/test_supplier_import.py`
- 证据：[junit.xml](.omo/evidence/task-2-current-version-hardening/20260909T042217758Z-5fcb9be9/junit.xml)（22/0/0）、[before-after.json](.omo/evidence/task-2-current-version-hardening/20260909T042217758Z-5fcb9be9/before-after.json)、[component.txt](.omo/evidence/task-2-current-version-hardening/20260909T042217758Z-5fcb9be9/component.txt)、[metadata.json](.omo/evidence/task-2-current-version-hardening/20260909T042217758Z-5fcb9be9/metadata.json)
- 结论：完整详情 + `expected_updated_at` 并发令牌，未编辑子项 ID 不变，冲突 409 `supplier_changed`。

### M2 统计真实性（任务 4、5）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_dashboard_summary.py,tests/test_current_query_validity.py`；前端 `npm run test:unit -- src/components/OverviewView.test.tsx src/App.test.tsx`
- 证据：[junit.xml](.omo/evidence/task-4-current-version-hardening/junit.xml)（19/0/0）、[counts.json](.omo/evidence/task-4-current-version-hardening/counts.json)、[component.txt](.omo/evidence/task-5-current-version-hardening/component.txt)
- 结论：`days=7|30|90` 服务端全量汇总，超过 100 条不截断；当前统计与期间新增（含已失效）口径分离。

### M3 删除保护（任务 3）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_supplier_delete_guard.py,tests/test_suppliers_api.py,tests/test_permissions.py`
- 证据：[junit.xml](.omo/evidence/task-3-current-version-hardening/20260909T051028728Z-3b5a30aa/junit.xml)（25/0/0）、[audit.json](.omo/evidence/task-3-current-version-hardening/20260909T051028728Z-3b5a30aa/audit.json)、[run-summary.json](.omo/evidence/task-3-current-version-hardening/20260909T051028728Z-3b5a30aa/run-summary.json)
- 结论：存在保留风险关联时删除被原子拒绝并写审计；日常退出使用暂停。

### M4 证据保留（任务 6）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_retention_evidence.py,tests/test_risk_validity_compatibility.py,tests/test_source_membership_validity.py,tests/test_sources_collection.py`
- 证据：[junit.xml](.omo/evidence/task-6-current-version-hardening/20260909T082559909Z-1a56de01-GREEN/junit.xml)（50/0/0）、[summary.json](.omo/evidence/task-6-current-version-hardening/20260909T082559909Z-1a56de01-GREEN/summary.json)、[closure-before-after.json](.omo/evidence/task-6-current-version-hardening/closure-before-after.json)
- 结论：有效及保留期内提醒的证据闭包受保护；90/90/30 默认保留矩阵不变。

### M5 监控健康（任务 7、8）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_monitoring_health.py,tests/test_scheduler_registry.py,tests/test_scheduler_validity_job.py,tests/test_health.py`
- 证据：[acceptance-summary.json](.omo/evidence/task-7-current-version-hardening/acceptance-summary.json)（56/0/0）、[junit-green.xml](.omo/evidence/task-7-current-version-hardening/junit-green.xml)、[health-cases.json](.omo/evidence/task-7-current-version-hardening/health-cases.json)、[component.txt](.omo/evidence/task-8-current-version-hardening/component.txt)
- 结论：`GET /api/v1/system/monitoring-health` 报告心跳与来源新鲜度/积压；前端区分正常/降级/未知/未开启；`/api/v1/system/health` 保持 DB 存活语义。

### M6 通知可靠性（任务 9）— 隔离候选验收通过

- 命令：`pwsh -File scripts/test-current-version-hardening.ps1 -Suite backend -Tests tests/test_notification_reliability.py,tests/test_notifications.py,tests/test_risk_validity_e2e.py`
- 证据：[delivery-matrix.json](.omo/evidence/task-9-current-version-hardening/delivery-matrix.json)（40/0/0）、[junit.xml](.omo/evidence/task-1-current-version-hardening/20260910T123827930Z-381af035/junit.xml)、[summary.json](.omo/evidence/task-1-current-version-hardening/20260910T123827930Z-381af035/summary.json)
- 结论：扫描结果显式提交；按 alert×channel 去重；摘要保留成员关联；详情链接指向 `/risks/{id}`；隔离环境仅 FakeProvider。

### M7 发布与恢复（任务 10、11、12）— 混合状态

- 全量门禁（任务 10）：命令 `pwsh -File scripts/test-current-version-hardening.ps1 -Suite all`；证据 [summary.json](.omo/evidence/task-10-current-version-hardening/20260912T032041201Z-022978a3/summary.json)（839/269/26，见第 3 节）— 隔离候选验收通过。
- 备份/恢复（任务 11）：命令 `pwsh -File scripts/test-hardening-recovery-contract.ps1`、`pwsh -File scripts/test-hardening-recovery.ps1`；证据 [recovery.json](.omo/evidence/task-11-current-version-hardening/20260912T030100607Z-13224a86/recovery.json)、[verify.json](.omo/evidence/task-11-current-version-hardening/20260912T030100607Z-13224a86/verify.json)、[contract.json](.omo/evidence/task-11-current-version-hardening/contract-20260912T030013068Z-ef7f6242/contract.json)。结果：契约 9/9；恢复 run `20260912T030100607Z-13224a86` `status=passed`、`verification.passed=true`；迁移 0049；14 表 1316 行摘要一致；恢复库证据闭包 `status=ok`、`orphan_alert_ids=[]`；六项比较 6/6 `passed`。
- 回滚兼容 smoke（任务 11 子项）：**blocked** — 无可用上一已验证候选镜像；证据 [rollback.txt](.omo/evidence/task-11-current-version-hardening/20260912T030100607Z-13224a86/rollback.txt)（`{"status":"blocked","reason":"no-previous-verified-candidate-image-available","previous_candidate_reference":null}`）。该子项未完成，不宣称可回滚，也不将任务 11 整项标记完成。

## 6. 备份/恢复与回滚状态

| 项 | 结果 |
| --- | --- |
| 恢复演练目标 | 隔离项目内新库 `supplier_risk_restore_test`（源库 `supplier_risk_test`） |
| 备份格式/校验 | `pg_dump -Fc`，SHA-256 记录于 `dump-sha256.txt` |
| 一致性比较 | `table_counts` / `row_digests` / `evidence_closure` / `paused_suppliers` / `user_role_status` / `migration_version` 全部 passed |
| 回滚 smoke | blocked（无上一已验证候选镜像） |
| 生产回滚 | 未验证、未授权；正式库不得执行破坏性回退 |

## 7. legacy 摘要未关联记录与通知预计补投计数

来源：[delivery-matrix.json](.omo/evidence/task-9-current-version-hardening/delivery-matrix.json)（任务 9 QA 轮）。

| 指标 | 值 | 说明 |
| --- | --- | --- |
| legacy 摘要未关联记录数（`legacy_unlinked`） | 1 | 已有 NULL 历史不反向猜测恢复；不伪造成员 |
| 通知预计补投计数（`backfill_pending`） | 2 | 按 (alert, channel) 对计数：1 个陈旧 alert × 2 渠道 = 2 |
| 实际补投 | 0 | 隔离环境未开启真实渠道、未实际补发；补投前需单独授权 |

## 8. 待现场证据项（pending，未获证据前不判通过）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| HTTPS/TLS 与 Secure Cookie | pending | 需部署证书、`SESSION_SECURE_COOKIE=true`、HTTPS 登录冒烟 |
| 真实通知渠道推送 | pending | 需真实渠道凭据与推送授权；含补投策略确认 |
| ECS 配置 | pending | 归入需独立验收的 ECS 部署验证阶段0 |
| 真实生产备份/回滚 | pending | 需在真实环境完成备份、恢复与回滚演练 |
| 安全风险接受复核 | pending | 镜像漏洞与风险接受需现场评审记录 |

## 9. E1–E7 待选增强登记（未执行，本页不设执行复选框）

以下为计划 Scope 中的待选增强登记，本期**未执行、未实现**；未来选中需单独批准实施范围。本页故意不使用复选框，避免被误认为本期任务。

| 编号 | 成果 | 依赖 | 当前边界 |
| --- | --- | --- | --- |
| E1 | 主数据质量与监控覆盖缺口 | 任务 2、7、8 | 不承诺全风险覆盖；不重复既有规则页信源状态 |
| E2 | 多地点、多产品、别名完整维护 | 任务 2、3 | 不涉及外购件 BOM 或二级供应商 |
| E3 | 固定质量案例与人工反馈 | 任务 4、6、9、10 | 不自动训练、不自动改分、不变成工单 |
| E4 | 历史查询与变化解释 | 任务 3、6 | 详细快照粒度与长期保留期另批 |
| E5 | 通知管理与降噪体验 | 任务 9 | 复用已有合并/限频/免打扰，不当作新开发 |
| E6 | 供应商归档恢复 | 任务 3、6 | 状态模型另批；本期只暂停 |
| E7 | 可复核导出 | 任务 4、6；历史导出依赖 E4 | 不伪造缺失快照，不宣称审计认证 |

## 10. 二期风险处置工单（已纳入规划、未实现）

- 状态：**已纳入规划、未实现**（`phase2_workorder.status = planned-not-implemented`）。
- 本期**无**工单表、无工单 API、无工单页面、无自动派单；提醒过期不自动关闭工单等隔离约束见 [当前计划 Scope](.omo/plans/current-version-hardening.md)。
- 二级供方与工单是两条独立演进线，见 [tier2-material-level-monitoring.md](.omo/tier2-material-level-monitoring.md)。

## 11. 已知限制与待授权操作

已知限制：

- 回滚兼容 smoke blocked：无上一已验证候选镜像；在补齐前不得宣称可回滚。
- 外部通知渠道无幂等协议：发送成功但提交前进程崩溃存在重复窗口；只承诺数据库内去重与有界重试，不承诺 exactly-once。
- 监控健康为观测信息，不改变匹配/评分/有效期结果；单 Scheduler 部署边界不变。
- 研究轨（RSSHub/Crawl4AI、真实搜索 Provider、research-worker）不属于本候选已交付能力；`RESEARCH_TRACK_ENABLED` 生产候选必须显式关闭。
- 当前统计与期间新增口径已在页面明确区分；历史可能已被清理时汇总会标注 `history_may_be_partial`。
- 备份/恢复脚本产物为本地/隔离证据，不构成生产操作授权。

待授权操作：

- 生产 HTTPS/TLS 配置、`SESSION_SECURE_COOKIE=true`、`ALLOWED_ORIGINS` HTTPS 来源。
- 开启真实通知渠道并发起补投。
- ECS 部署、容量验证与 48 小时观察。
- 真实生产库备份/恢复与回滚演练。
- 安全风险接受复核与发布授权（`production_release_authorized` 保持 pending）。

## 12. 引用路径核验

本台账引用的 30 条 JSON/JUnit/文本证据路径已由脚本在生成 `readiness-check.json` 时逐条 `Test-Path` + `Get-Item` 校验（`evidence_validation.all_exist=true`，见 [readiness-check.json](.omo/evidence/task-12-current-version-hardening/20260912T032041201Z-022978a3/readiness-check.json)）；本页所有引用路径均为**仓库根目录相对路径**，并已校验存在：`.omo/plans/current-version-hardening.md`、`.omo/tier2-material-level-monitoring.md`、[README.md](README.md)、迁移 head 文件 [0049_scheduler_runtime.py](backend/alembic/versions/0049_scheduler_runtime.py)。
