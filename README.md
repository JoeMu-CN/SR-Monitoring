# 供应商风险监控平台

本项目用于在本机收集外部供应风险信息，匹配重点供应商、生产地点和供应产品，并通过 localhost 网页显示分级风险提醒。

当前已完成两周 MVP 的 D1 基础骨架、D2 供应商主数据、D3 手工风险信号采集、
D4 AI 结构化解析、D5 风险纵向链路、D6 确定性供应商匹配、D7 可配置评分、
D8 前端页面和 D9 真实数据源与独立调度器，形成发布候选版本。

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
- 提醒自动失效，事件结束超过保留期后提醒自动标记为已失效。
- 同一供应商和事件只保留一条当前提醒，重复处理自动更新。
- `POST /api/v1/signals/{signal_id}/process` 完整处理接口。
- `GET /api/v1/risk-alerts` 当前提醒查询接口。
- `POST /api/v1/risk-alerts/expire` 手动触发提醒失效检查。
- 中央气象台天气预警数据源（`nmc-weather`），HTTP 拉取、官方公开数据、指纹去重。
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

## Scheduler 定时任务

Scheduler 是独立容器，与 Web 服务共用同一镜像，启动时自动执行数据库迁移：

    docker compose up -d scheduler

默认任务（可通过 .env 的 `SCHEDULER_*_CRON` 调整）：

- 每 30 分钟：采集所有启用的拉取式数据源（`nmc-weather`），并处理待解析信号（AI 解析 → 事件归并 → 匹配 → 评分 → 提醒）。
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
- 已接入两个真实来源：中央气象台天气预警（拉取式）与天眼查 MCP（Agent 核查工具）；手工 JSON 导入作为合规补充。
- 当前只做确定性匹配；AI 模糊匹配不会直接生成提醒。
- 单独同国家不视为地点匹配；事件未明确披露坐标和影响半径时不会推测空间范围。
- 当前页面只查询风险提醒，不提供历史分析和风险处置动作。
- 电脑关机、休眠或 Docker 停止后不会执行监控（Scheduler 与 app 同机）。

## 已验证的候选数据源

2026-08-04 仅完成连通性和响应格式验证，尚未开发数据源适配器：

- USGS Significant Earthquakes GeoJSON：HTTP 200，响应类型为 JSON。
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson
- OFAC SDN List CSV：HTTP 200，响应类型为 CSV。
  https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV

正式接入前仍需确认字段、更新频率、使用条款和异常策略。

完整范围、架构和开发计划见 技术方案.md。
