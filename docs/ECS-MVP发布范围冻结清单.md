# ECS MVP 发布范围冻结清单

> 状态：PostGIS 残余风险已由负责人临时接受；仍待完成其他发布门禁后冻结 commit/tag（2026-08-12）

## 冻结目标

首次 ECS 发布只承载监控轨 MVP。研究轨代码可以随候选源码存在，但生产运行时必须关闭研究路由、研究权限、研究调度和搜索 Provider；研究能力不属于本次生产验收范围。

## 当前候选基线

| 项目 | 值 |
| --- | --- |
| 发布分支 | `codex/ecs-mvp-release` |
| 基线父提交 | `06c7e7060a4fcde30bfad07d9827c962d2b840c5` |
| 候选镜像 | `supplierriskmonitoring-app:ecs-mvp-candidate` |
| 候选镜像 digest | `sha256:c51702ff7ed8eb8768cd1ef30bab91f779fb0b2b3dcdf7100b934414c1062e14` |
| Nginx 镜像 | `nginx:1.30-alpine-slim@sha256:45c3810793fe3e982fb614c67e1b696816aff3ec742620e1ef7cd9d3184185ef`；Scout Critical/High `0/0` |
| PostgreSQL/PostGIS 镜像 | `postgis/postgis:16-3.5-alpine@sha256:d2fe6296c8ed5b21b31a426f51b9176b4d89f80a0a380632a7a833d604951273`；Scout Critical/High `1/17`，仍阻断发布 |
| 生产迁移目标 | `0027` |
| 本地/阶段 0 迁移目标 | `head`（当前为 `0028`） |
| 生产研究开关 | `RESEARCH_TRACK_ENABLED=false` |
| Scout 结果 | app `0/0`；Nginx `0/0`；PostGIS `1/17`，已按条件临时接受并限期复核 |

## 允许纳入首次 ECS MVP 的变更

- 监控轨后端、认证/RBAC、CSRF、数据源采集、AI 分析、规则评分、提醒和待复核链路。
- React/Vite 前端及其生产构建所需源文件。
- `Dockerfile` 的 Alpine 运行层和可配置 Alembic 迁移目标。
- `compose.yaml`、`compose.prod.yaml` 的四服务生产编排：`postgres`、`app`、`scheduler`、`nginx`。
- `deploy/.env.production.example` 的生产占位配置；真实 `.env`、密钥和证书不进入 Git。
- 监控轨迁移 `0027`；完整研究迁移链不作为生产升级目标。
- 发布台账、SBOM 和部署清单等不含密钥的审计文档。

## 明确排除首次 ECS 发布的内容

- `backend/app/research/`、`frontend/src/components/ResearchView.tsx` 及研究测试：本次不启用、不作为生产验收能力。
- 研究迁移 `0022`～`0026`、合并节点 `0028`：生产新库只执行到 `0027`。
- RSSHub、Crawl4AI、搜索 Provider、research-worker、自动日报/周报。
- `.env`、`deploy/.env.production`、真实 API Key、数据库密码、Session 密钥、证书和私有配置。
- `backend/app/static/`、`backend/*.egg-info/`、`backend/data/` 等本地构建/运行生成物；生产镜像在 Docker 构建阶段生成前端静态文件。
- `.workbuddy/`、`backend/_wip_backup_engine/`、demo 删除/改版文件及其他未明确纳入 MVP 的用户工作区改动。

## 冻结前必须完成

- [ ] 负责人确认本清单的纳入/排除范围。
- [ ] 从干净工作树或明确暂存集合重新构建候选镜像，不从混合工作区直接打生产 tag。
- [ ] 冻结 commit 和不可变镜像 tag，并记录 digest。
- [ ] 处理 PostGIS 镜像 Critical `1`、High `17`：升级到兼容的已修复镜像；在补丁可用前按下方记录执行临时风险接受。
- [x] 风险接受：已明确漏洞来源为 `gosu`/Go stdlib、暴露面为内部网络、补偿控制、责任人、有效期和补丁镜像跟踪期限。

### 本次临时风险接受记录

| 项目 | 记录 |
| --- | --- |
| 接受范围 | 仅接受 `postgis/postgis:16-3.5-alpine@sha256:d2fe6296…51273` 中 `gosu`/Go stdlib 引起的 Critical `1`、High `17`；不扩展到新的漏洞、镜像或网络暴露。 |
| 接受人/责任人 | 项目负责人（用户于 2026-08-12 明确确认） |
| 有效期 | 临时有效，至兼容补丁镜像可用并完成复扫；最迟首次 ECS 上线后 30 天复核，未完成则停止发布推进并重新评估。 |
| 补偿控制 | PostgreSQL 仅 internal 网络、无宿主端口；ECS 安全组拒绝 5432；数据库容器不挂 Docker socket/特权模式；仅运行固定 digest 可信镜像；完成备份恢复演练。 |
| 复核要求 | 每次数据库镜像更新、网络边界变化或新增同机容器前重新执行 Scout；出现公网数据库端口、非可信容器或备份恢复失败时立即撤销接受。 |

### 风险接受判断口径

- **不能直接忽略**：漏洞存在于随数据库镜像交付的真实二进制，不是扫描器误报。
- **可考虑临时接受**：仅当 PostgreSQL 不映射公网端口、ECS 安全组拒绝数据库端口、Docker socket 不挂载到数据库容器、Nginx/app/scheduler 按最小权限运行，并由负责人写明责任人、有效期和补丁跟踪日期。
- **必须阻断**：若数据库端口对公网开放、存在不受信任容器可加入同一网络、需要运行未经审计的数据库扩展，或无法提供回滚/备份恢复证据。
- [ ] 提供 ECS、ACR、HTTPS 证书、安全组、备份和监控配置。
- [ ] 注入生产密钥并完成首次管理员初始化、认证联调、真实数据源联调和备份恢复演练。

## 运行时门禁

生产 Compose 必须满足：

```text
服务：postgres / app / scheduler / nginx
RESEARCH_TRACK_ENABLED=false
ALEMBIC_UPGRADE_TARGET=0027
PostgreSQL、app、scheduler 不映射公网端口
```

研究轨只能在 MVP 稳定、单独审批后通过独立覆盖编排启用；不得通过修改生产主题为空、手动调用研究 API 或在线替换镜像绕过本清单。
