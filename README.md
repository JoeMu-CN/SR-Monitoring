# 供应商风险监控平台

本项目用于在本机收集外部供应风险信息，匹配重点供应商、生产地点和供应产品，并通过 localhost 网页显示分级风险提醒。

当前已完成两周 MVP 的 D1 基础骨架、D2 供应商主数据、D3 手工风险信号采集、
D4 AI 结构化解析、D5 风险纵向链路、D6 确定性供应商匹配、D7 可配置评分、
D8 前端页面、D9 真实数据源与独立调度器，以及 D10 验收收口，当前正在完成第二个真实来源的现场采集验证。

## 当前能力

- Vue 3 当前风险提醒查询页和 P1 至 P4 本页分布。
- FastAPI 健康检查接口。
- PostgreSQL 16 + PostGIS。
- Alembic 初始迁移。
- Docker Compose 本地编排。
- Pytest、Vitest、Ruff、mypy、ESLint 和类型检查基线。
- 三工作表供应商 Excel 模板、原子导入和逐行错误报告。
- 供应商查询、新增、修改和启停接口。
- 标准 JSON 风险信号导入、SHA-256 指纹去重和采集运行追踪。
- 数据源和采集运行记录查询接口。
- 可替换的 Fake/OpenAI 兼容 AI Provider、结构化输出校验和失败重试。
- 原始信号 AI 分析接口及成功、失败、耗时记录。
- 确定性风险事件归并和信号关联。
- 注册编号、法人全称精确匹配及可读匹配理由。
- 供应商别名标准化精确匹配。
- 事件坐标与生产地点的 PostGIS 范围匹配，以及地区/城市/地点名称匹配。
- 受影响产品与供应产品名称、人工关键词匹配。
- 结构化匹配证据，包括供应商、别名、生产地点、距离、影响半径和产品。
- 可配置评分引擎，各维度分值和分级阈值可通过 `RISK_SCORING_CONFIG` 环境变量调整。
- 强制规则，供应商主体直接命中制裁/合规事件时自动提升为 P1。
- 模块化监控维度，支持工作台运行时启停和参数热更新；新增代码维度仍随应用镜像发布。
- 风险大类与风险细类分离；只有明确的制裁、出口管制等细类可以触发对应强制规则。
- 受影响产品与受影响行业独立提取、匹配，避免行业规则借用产品字段。
- 工作台配置经过后端范围和阈值顺序校验，生效配置生成可追溯规则版本。
- 提醒自动失效，事件结束超过保留期后提醒自动标记为已失效。
- 同一供应商和事件只保留一条当前提醒，重复处理自动更新。
- `POST /api/v1/signals/{signal_id}/process` 完整处理接口。
- `GET /api/v1/risk-alerts` 当前提醒查询接口。
- `POST /api/v1/risk-alerts/expire` 手动触发提醒失效检查。
- 中央气象台天气预警数据源（`nmc-weather`），HTTP 拉取、官方公开数据、指纹去重。
- OFAC SDN 制裁名单数据源（`ofac-sdn`），官方公开 CSV 拉取、实体编号去重和原始来源留存。
- `POST /api/v1/sources/{id}/run` 手动触发拉取式数据源采集。
- 独立 Scheduler 进程（APScheduler）：定时采集、AI 解析、事件归并、评分、提醒失效和数据保留清理。
- 数据保留清理：原始信号/AI 分析 90 天、事件与提醒失效后 90 天、采集运行 30 天（均可配置）。

## 本地启动

前置条件：Docker 守护进程已经启动。

首次启动：

    docker compose up --build

打开：

    http://127.0.0.1:8080

健康检查：

    Invoke-RestMethod http://127.0.0.1:8080/api/v1/system/health

接口文档：

    http://127.0.0.1:8080/api/docs

供应商导入模板：

    http://127.0.0.1:8080/api/v1/suppliers/import-template

手工风险信号通过接口文档中的 `POST /api/v1/signals/import` 上传。JSON 格式：

```json
{
  "version": "1.0",
  "signals": [
    {
      "external_id": "NOTICE-001",
      "title": "港口临时管制",
      "content": "受大风影响，部分港区临时停止装卸作业。",
      "url": "https://example.com/notices/001",
      "published_at": "2026-08-05T09:00:00+08:00"
    }
  ]
}
```

文件必须是 UTF-8 编码的 `.json`，最大 5MB、最多 5000 条信号；`title` 和
`content` 必填，`published_at` 填写时必须包含时区。重复上传不会重复创建信号。

默认使用不访问网络的 `FakeAIProvider`。导入信号后，可在接口文档调用
`POST /api/v1/signals/{signal_id}/analyze`。查看当前 AI 配置状态：

    http://127.0.0.1:8080/api/v1/ai/status

切换到支持 OpenAI Chat Completions 协议的模型厂商时，在本地 `.env` 中设置：

```dotenv
AI_PROVIDER=openai-compatible
AI_BASE_URL=https://模型厂商的兼容接口地址/v1
AI_MODEL=模型名称
AI_API_KEY=本地密钥
AI_TIMEOUT_SECONDS=90
AI_MAX_RETRIES=2
```

`AI_API_KEY` 只通过环境变量传入容器，禁止写入代码、文档或提交到 Git。

D5 完整处理接口会优先复用该信号最近一次成功的 AI 分析；没有成功记录时才调用
当前配置的 AI Provider。处理结果会生成或复用风险事件，并仅在注册编号或法人全称
精确匹配启用中的供应商时生成风险提醒。当前提醒页面：

    http://127.0.0.1:8080

停止：

    docker compose down

默认配置足以在 localhost 开发。需要修改配置时，将 .env.example 复制为 .env；.env 已被 Git 忽略，禁止提交真实密钥。

## 备份与恢复

备份前保持 PostgreSQL 容器运行。以下命令在容器内生成 PostgreSQL 自定义格式备份，
再复制到项目的 `backups` 目录，不会把 `.env` 或密钥写入备份文件：

```powershell
$backupDir = Join-Path $PWD 'backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$backupPath = Join-Path $backupDir ("supplier-risk-{0}.backup" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/supplier_risk.backup'
docker compose cp postgres:/tmp/supplier_risk.backup $backupPath
Write-Output $backupPath
```

恢复演练默认写入新数据库 `supplier_risk_restore`，不会覆盖当前业务库：

```powershell
$backupPath = 'D:\path\to\supplier-risk-yyyyMMdd-HHmmss.backup'
docker compose cp $backupPath postgres:/tmp/supplier_risk_restore.backup
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" supplier_risk_restore'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d supplier_risk_restore --no-owner /tmp/supplier_risk_restore.backup'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d supplier_risk_restore -c "SELECT count(*) AS suppliers FROM suppliers"'
```

如果 `supplier_risk_restore` 已存在，`createdb` 会失败并停止；先确认数据库用途，禁止直接覆盖
当前 `supplier_risk` 数据库。

## 演示流程

1. 执行 `docker compose up -d --build`，等待 `docker compose ps` 中 app、postgres、scheduler 正常运行。
2. 打开 http://127.0.0.1:8080 ，确认总览页显示本机服务正常。
3. 从 http://127.0.0.1:8080/api/v1/suppliers/import-template 下载模板，填写供应商、生产地点和供应产品三张工作表。
4. 在 http://127.0.0.1:8080/api/docs 调用 `POST /api/v1/suppliers/import` 导入模板。
5. 调用 `POST /api/v1/signals/import` 导入标准 JSON 风险信号，再调用 `POST /api/v1/signals/{signal_id}/process` 完成分析、归并、匹配、评分和提醒。
6. 回到网页检查总览、当前风险、风险详情、供应商、数据源和规则引擎页面；风险详情应能看到原始来源、匹配理由和评分明细。
7. 可调用 `POST /api/v1/sources/{id}/run` 手动触发中央气象台或 OFAC SDN 采集，并在数据源页面确认运行结果。

D10 的逐项证据和未通过项见 [D10验收记录.md](D10验收记录.md)。

## Scheduler 定时任务

Scheduler 是独立容器，与 Web 服务共用同一镜像，启动时自动执行数据库迁移：

    docker compose up -d scheduler

默认任务（可通过 .env 的 `SCHEDULER_*_CRON` 调整）：

- 每 30 分钟：兜底采集未单独配置周期的数据源，并处理待解析信号（AI 解析 → 事件归并 → 匹配 → 评分 → 提醒）。
- 数据源控制台中填写 `schedule` 后，Scheduler 启动时会为该数据源注册独立 cron；修改周期或启停状态后重启 scheduler 容器使注册表刷新。
- 每小时：将超过 `expires_at` 的提醒标记为已失效。
- 每天 03:00：数据保留清理（信号/AI 分析 90 天、事件与提醒失效后 90 天、采集运行 30 天）。

也可以不依赖调度器，随时通过 API 手动触发单次采集：

    POST /api/v1/sources/{id}/run

## 验证

后端：

    docker compose run --rm app pytest
    docker compose run --rm app ruff check app tests
    docker compose run --rm app mypy app

前端测试和检查在镜像构建时执行类型检查；也可以在 frontend 目录安装依赖后执行：

    npm test
    npm run lint
    npm run typecheck

## 当前限制

- 只绑定 127.0.0.1，不允许局域网或公网访问。
- 已接入中央气象台与 OFAC 拉取式数据源；天眼查 MCP 作为统一数据源目录中的按需外部核查工具，运行密钥仅通过 `TYC_API_KEY` 环境变量注入；手工 JSON 导入作为合规补充。
- 当前只做确定性匹配；AI 模糊匹配不会直接生成提醒。
- 单独同国家不视为地点匹配；事件未明确披露坐标和影响半径时不会推测空间范围。
- 当前页面只查询风险提醒，不提供历史分析和风险处置动作。
- 电脑关机、休眠或 Docker 停止后不会执行监控（Scheduler 与 app 同机）。

## 已验证的候选数据源

2026-08-04 完成连通性和响应格式验证；OFAC SDN 已在 D10 中实现适配器，USGS 仍未接入：

- USGS Significant Earthquakes GeoJSON：HTTP 200，响应类型为 JSON。
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson

正式接入前仍需确认字段、更新频率、使用条款和异常策略。

完整范围、架构和开发计划见 技术方案.md。

## 数据源控制台（0013）

数据源页面现已支持管理员配置数据源编码、官方入口、可信度、5 段 cron 调度周期、认证方式、登录配置、密钥引用和启停状态，并提供新增、编辑、删除及修改日志查询。写接口必须携带 `X-User-Role: admin`，只读请求不需要角色头；当前该请求头是统一身份系统接入前的最小角色边界，生产环境应由网关/SSO 注入，不能把前端“管理员模式”当作身份认证。

API Key 不以明文写入数据库，仅保存 SHA-256 指纹和末四位；实际连接器应通过 `credential_ref` 对接部署环境的密钥管理服务。新增的商务部清单、BIS Entity List、联合国综合制裁清单、EUR-Lex、应急管理部通报均默认停用，需完成适配器、授权与格式验收后再启用；它们的官方入口和覆盖维度见《风险监控维度扩展规划.md》及迁移 `0013`。

## 智能数据源接入与实时查询（0014）

管理员可以在数据源页面或平台内置 Agent 中创建声明式 JSON/CSV 适配器，并在保存前执行实时联网预览。适配器只支持受控 HTTPS GET、简单点号字段路径、标准信号字段映射和确定性指纹；预览最多返回 10 条标准化结果且不写入业务表。

数据源接入状态依次为 `draft`、`published`；中央气象台、OFAC 和手工 JSON 标记为 `builtin`。新来源创建和发布后均保持停用，管理员确认启用后才可执行正式采集。Scheduler 每分钟刷新一次已启用且已发布/内置来源的 cron 注册表，无需登录服务器重启调度进程。

平台 Agent 只在管理员会话中加载 `preview_source_adapter`、`create_source_adapter_draft`、`publish_source_adapter` 和 `run_source_now` 工具。发布和正式采集分别要求当前消息明确包含“确认发布”和“立即采集”。认证来源仅接受 `env:VARIABLE_NAME` 凭据引用，明文密钥不得进入适配器配置、Agent 上下文或审计日志。

受控联网会拒绝 HTTP、内嵌凭据、重定向、本机/私网/链路本地地址、超大响应和静态认证请求头。验证码、浏览器自动化、OAuth 交互、自定义签名、PDF/OCR 等复杂来源仍需要开发扩展。完整设计和验收边界见《数据源智能接入与实时查询实施计划.md》。
