# 供应商风险监控平台

本项目用于在本机收集外部供应风险信息，匹配重点供应商、生产地点和供应产品，并通过 localhost 网页显示分级风险提醒。

当前阶段为两周 MVP 的 D1 基础骨架。

## 当前能力

- Vue 3 最小页面。
- FastAPI 健康检查接口。
- PostgreSQL 16 + PostGIS。
- Alembic 初始迁移。
- Docker Compose 本地编排。
- Pytest、Vitest、Ruff、mypy、ESLint 和类型检查基线。

## 本地启动

前置条件：Docker 守护进程已经启动。

首次启动：

    docker compose up --build

打开：

    http://127.0.0.1:8080

健康检查：

    Invoke-RestMethod http://127.0.0.1:8080/api/v1/system/health

停止：

    docker compose down

默认配置足以在 localhost 开发。需要修改配置时，将 .env.example 复制为 .env；.env 已被 Git 忽略，禁止提交真实密钥。

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
- 当前没有真实数据源、AI 调用、供应商导入或风险评分。
- 电脑关机、休眠或 Docker 停止后不会执行监控。

## 已验证的候选数据源

2026-08-04 仅完成连通性和响应格式验证，尚未开发数据源适配器：

- USGS Significant Earthquakes GeoJSON：HTTP 200，响应类型为 JSON。
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson
- OFAC SDN List CSV：HTTP 200，响应类型为 CSV。
  https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV

正式接入前仍需确认字段、更新频率、使用条款和异常策略。

完整范围、架构和开发计划见 技术方案.md。
