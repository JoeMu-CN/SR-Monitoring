# ECS MVP 发布候选文件清单

> 版本：2026-08-12；用途：从混合工作区提取首次 ECS MVP 发布集合。本文只描述文件范围，不包含任何密钥。

## 1. 发布原则

首次生产只启用监控轨。研究轨即使随候选源码存在，也必须由 `RESEARCH_TRACK_ENABLED=false`、`ALEMBIC_UPGRADE_TARGET=0027` 和生产 Compose 共同关闭；研究 API、研究页面、研究 Worker、搜索 Provider、自动日报/周报不纳入本次生产验收。

候选集合必须从当前 Git 基线和明确的工作区改动提取，禁止直接把整个工作区构建成生产镜像。真实生产环境只拉取冻结 commit 对应的镜像或不可变 digest。

## 2. 必须纳入候选集合

### 运行与编排

- `Dockerfile`
- `compose.yaml`
- `compose.prod.yaml`
- `deploy/.env.production.example`
- `deploy/nginx/nginx.conf`
- `deploy/backup-postgres.sh`

### 后端监控轨与生产认证

- `backend/app/` 中现有监控轨、认证/RBAC、CSRF、数据源、AI 分析、规则评分、提醒、待复核和保留清理代码
- `backend/alembic/env.py`
- `backend/alembic/versions/0001~0021` 已提交的监控轨历史迁移
- `backend/alembic/versions/0027_mvp_signal_review_state.py`
- `backend/pyproject.toml`
- 监控轨对应的 `backend/tests/` 测试及发布前验证脚本

### 前端与发布审计

- `frontend/` 源码、锁文件和生产构建所需配置
- `docs/ECS-MVP发布基线执行记录.md`
- `docs/ECS-MVP发布范围冻结清单.md`
- 本文件及不含密钥的 SBOM、漏洞扫描和验证记录

## 3. 运行时排除项

以下内容不得在首次 ECS 生产运行中启用或作为验收能力：

- 研究 API、研究页面、研究 Worker 和研究自动调度
- RSSHub、Crawl4AI、搜索 Provider、自动日报/周报
- 研究迁移 `0022`～`0026` 及合并节点 `0028`（可随源码保留，但生产启动固定到 `0027`）
- 未经人工确认的研究结论进入风险评分链

研究代码若因源码导入、测试或后续阶段 0 叠加需要随候选源码保留，仍必须满足生产开关关闭；其存在不代表研究能力已上线。

## 4. 明确排除项

- 根目录 `.env`、`deploy/.env.production`、真实 API Key、数据库密码、Session 密钥和证书
- `.workbuddy/`
- `backend/_wip_backup_engine/`
- `backend/app/static/`、`backend/*.egg-info/`、`backend/data/` 等本地生成物
- `backups/`、临时导出物、日志和缓存目录
- demo 原型、未列入本清单的实验文件和无关 UI/文档改动

## 5. 发布前机械检查

在创建 commit/tag 前，必须逐项确认：

1. `git status --short` 中没有 `.env`、证书、备份、`.workbuddy` 或生成物进入暂存区。
2. 生产 Compose 渲染结果只有 `postgres`、`app`、`scheduler`、`nginx` 四个服务。
3. 渲染结果满足 `RESEARCH_TRACK_ENABLED=false`、`ALEMBIC_UPGRADE_TARGET=0027`，且 PostgreSQL、app、Scheduler 无宿主端口。
4. 新库迁移到 `0027`；本地完整 head `0028` 仅用于阶段 0/开发验证。
5. app、Nginx、PostGIS 镜像均使用已记录的 digest；PostGIS 当前临时风险接受条件仍然有效。
6. 完成 SBOM、Scout 扫描、全量测试、备份恢复演练和回滚演练后，才允许创建不可变生产 tag。

## 6. 当前阻断

本清单已经落盘，但以下外部条件尚未具备，因此本轮不创建生产 tag、不推送 ACR、不连接 ECS：

- ECS、ACR、HTTPS 证书和安全组参数尚未提供；
- 生产密钥、天眼查授权和千问生产额度尚未注入；
- 备份恢复与回滚演练尚未在真实 ECS 完成；
- 工作区仍有未提交改动，候选集合尚未完成暂存审阅。

完成上述条件后，先由负责人审阅 `git diff --cached`，再创建发布 commit 和不可变 tag。
