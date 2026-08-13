# ECS MVP 发布基线执行记录

> 状态：P0 执行中；PostGIS 残余风险已临时接受，发布仍被证书、凭据和范围冻结门禁阻断（2026-08-12）

## 目标

在阿里云 ECS 上发布已冻结的监控轨 MVP，并把研究轨作为后续可关闭、可回滚的阶段 0 叠加能力。首次生产部署不承担研究轨正式发布职责。

发布范围冻结清单见 [`docs/ECS-MVP发布范围冻结清单.md`](ECS-MVP发布范围冻结清单.md)。在负责人确认并从混合工作区提取干净发布集合前，不创建生产 tag、不推送 ACR。
候选文件提取规则见 [`docs/ECS-MVP发布候选文件清单.md`](ECS-MVP发布候选文件清单.md)。

## 当前证据

| 项目 | 当前状态 | 证据/说明 |
| --- | --- | --- |
| 当前分支 | `codex/ecs-mvp-release` | 已创建发布候选分支；尚未提交 |
| 工作区 | 未冻结 | 存在大量未提交改动及未跟踪研究轨文件 |
| 生产 Compose | 已具备基础编排 | `compose.yaml` + `compose.prod.yaml`，服务为 `postgres/app/scheduler/nginx` |
| 生产认证 | 已实现 | Cookie Session、bcrypt、CSRF、四角色；候选 Alpine 镜像内全量 pytest 已实测 |
| 研究轨开关 | 已加入并验证 | `RESEARCH_TRACK_ENABLED`；本地默认 `true`，生产模板为 `false`；关闭时研究路由、权限和调度均不注册 |
| 研究迁移 | 已建立双轨目标 | 新增幂等 `0027`（直接依赖 `0021`）作为 MVP 目标；`0028` 合并 `0026/0027` 供本地/阶段 0 使用 |
| 域名/证书 | 技术验证待配置 | 技术验证阶段使用 ECS 公网 IP + IP SAN 自签名证书；正式上线前再申请域名和受信任证书 |
| 生产密钥 | 未配置 | 仅模板占位；不读取或输出任何真实凭据 |
| ECS 实测 | 未开始 | 本地 Docker 隔离迁移/测试已完成；尚未连接 ECS |

## ECS 试用实例已创建（2026-08-12）

| 项目 | 当前值 |
| --- | --- |
| 地域 | 华东 1（杭州） |
| 操作系统 | Ubuntu 22.04 64 位 |
| 规格 | 2 vCPU / 4 GiB |
| 公网 IP | `120.26.0.76`（已由用户截图确认；后续如更换 EIP 需更新记录） |
| 私网 IP | `172.24.47.80`（已由用户截图确认） |
| Docker | 控制台显示 Docker 社区版已安装 |
| 部署状态 | 主机和镜像已准备；尚未配置临时 IP 证书/生产密钥，未启动平台 |

当前访问边界调整：负责人会在家庭宽带、公司 Wi-Fi 和公共 Wi-Fi 之间切换，固定个人公网 IP 白名单不适合作为主要访问方式。按已确认的技术验证方案，先使用 ECS 公网 IP + 临时自签名 HTTPS 证书，通过 `https://120.26.0.76` 验证登录和 MVP 链路；正式上线前再切换到域名和受信任证书。SSH `22` 默认不开放公网，使用 ECS Workbench/云助手运维；`5432/8080/3000` 继续禁止。

初始化操作手册见 [`docs/ECS-试用实例初始化操作手册.md`](ECS-试用实例初始化操作手册.md)。

## 继续执行记录（2026-08-12）

- 已新增发布候选文件清单，明确监控轨纳入范围、研究轨运行时排除项、生成物/密钥排除项和创建 tag 前的机械检查。
- `git diff --check`：通过。
- 后端 `compileall`：通过；当前主机没有可复用的 `pytest`、`ruff`、`mypy` 可执行环境，本轮未将缺失工具伪装成通过，需在候选容器或 CI 复跑。
- 前端通过独立 TypeScript 编译：`tsc --noEmit`；Vite/Vitest 在当前沙箱启动 esbuild 时返回 `spawn EPERM`，需在 Docker/CI 环境复跑。
- Docker 只读核验：Docker Desktop `desktop-linux` 可用；候选 app digest 为 `sha256:c51702ff7ed8eb8768cd1ef30bab91f779fb0b2b3dcdf7100b934414c1062e14`；生产 Compose 服务为 `postgres/app/scheduler/nginx`，仅 Nginx 暴露 `80/443`。
- 现有本地 Scheduler 容器当前为已停止状态；本轮未停止、删除或重建任何现有业务容器。
- `deploy/certs` 尚未提供，生产 Nginx 仍不能启动；未创建 `deploy/.env.production`，未读取或输出任何真实密钥。

## 已执行改动

- 在 `backend/app/config.py` 增加 `RESEARCH_TRACK_ENABLED`。
- 在 `backend/app/main.py` 按开关注册研究路由。
- 在 `backend/app/auth/security.py` 关闭开关时从 `/auth/me` 权限中移除研究权限，前端研究入口随之隐藏。
- 在 `backend/app/scheduler/main.py` 关闭开关时不注册每日/每周研究调度。
- 在 `deploy/.env.production.example` 将研究轨显式设为关闭，并清空研究调度主题。
- 将运行层切换为 `python:3.12-alpine`，并将 `cryptography` 约束提升至 `>=50,<51`；候选镜像已重新构建并生成 SBOM。
- 在 `阿里云部署实施清单.md` 增加 ECS MVP 发布计划和阶段 0 叠加门禁。
- 创建 `codex/ecs-mvp-release` 发布候选分支；保留当前工作区改动，尚未提交。

## P0 待执行清单

- [x] 创建发布分支；commit/tag 仍待最终验收后冻结。
- [x] 从监控轨拆出不依赖 `0022~0025` 的迁移链：幂等 `0027` 直接挂到 `0021`；`0028` 仅作为完整 head 的合并节点。
- [x] 生成 ECS MVP 候选镜像：`supplierriskmonitoring-app:ecs-mvp-candidate`，digest 见本轮验证结果。
- [x] 完成空库迁移、后端串行测试、前端生产构建、Ruff、mypy，并生成候选镜像 SBOM；Docker Scout Critical/High 扫描通过。
- [ ] 配置 ECS、ACR、HTTPS 证书、安全组、备份和监控。
- [ ] 进行首次部署、认证联调、两条真实数据源联调和备份恢复演练。

## 验收与止损命令

生产 Compose 渲染必须确认只有以下四个服务，且 app/scheduler 的 `RESEARCH_TRACK_ENABLED=false`、`ALEMBIC_UPGRADE_TARGET=0027`。真实部署时先创建 `deploy/.env.production`，不能直接使用模板文件：

```powershell
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml config
```

本地只读渲染占位模板时，需额外显式指定 `PROD_ENV_FILE=deploy/.env.production.example`；否则 Compose 会按默认值查找真实的 `deploy/.env.production`，这属于文件缺失提示，不是模板内容验证失败。

监控轨额度或调度异常时：

```powershell
docker compose stop scheduler
```

需要同时阻断手动 AI/助手调用时：

```powershell
docker compose stop scheduler app
```

## 阻断项

1. 当前工作区仍有大量用户未提交改动；候选镜像已构建，但尚未冻结发布 commit/tag。
2. 生产新库必须使用 `ALEMBIC_UPGRADE_TARGET=0027`；已有执行过 `0026` 的数据库升级到完整 head 时由 `0027` 幂等跳过重复字段，仍需核对 Alembic 状态。
3. `deploy/certs` 缺失，ECS Nginx 443 尚不能启动。
4. 生产密钥、天眼查授权、千问生产额度和 ECS 账号权限需要在部署时由负责人注入；本记录不保存密钥。
5. PostGIS 数据库镜像 Critical `1`、High `17`；负责人已于 2026-08-12 临时接受，条件、补偿控制和复核期限见范围冻结清单；补丁镜像可用后必须复扫。
6. ECS/ACR、生产密钥、天眼查授权、千问生产额度尚未配置；本轮没有连接外部 ECS。

## 本轮验证结果（2026-08-12）

- Python `compileall`：通过。
- `git diff --check`：通过。
- TypeScript `tsc --noEmit`：通过。
- Vite 生产构建：通过（2094 modules；存在单 chunk 超过 500 kB 的非阻断警告）。
- 生产 Compose 服务渲染：通过，服务为 `postgres/app/nginx/scheduler`。
- 生产 Compose 关键变量：`RESEARCH_TRACK_ENABLED=false`、`ALEMBIC_UPGRADE_TARGET=0027`。
- MVP 隔离空库迁移：通过，Alembic `0027`。
- 完整 head 隔离空库迁移：通过，Alembic `0028 (head)`；`needs_review/review_reason` 各存在一次。
- 研究轨关闭运行时：通过，OpenAPI 不暴露 `/api/v1/research/tasks`。
- 研究轨开启运行时：通过，OpenAPI 暴露 `/api/v1/research/tasks`。
- 后端 pytest：通过，全部测试通过；仅有 FastAPI/Starlette 弃用警告。
- Ruff：通过。
- mypy：通过，76 个源文件。
- 前端生产依赖 `npm audit --omit=dev --audit-level=high`：通过，0 个漏洞。
- ECS MVP 候选镜像（旧候选）：`sha256:13a5943af1ca52c27d739e66f2c4076e02f09823a90c8abb1bf3a43ba9dcf164`。
- Alpine 候选镜像：`supplierriskmonitoring-app:ecs-mvp-candidate`，本地镜像 ID/digest：`sha256:c51702ff7ed8eb8768cd1ef30bab91f779fb0b2b3dcdf7100b934414c1062e14`。
- Alpine 候选镜像内 `cryptography` 为 `50.0.0`，运行层为 `python:3.12-alpine`。
- SBOM：`docs/ecs-mvp-candidate.sbom.spdx.json`、`docs/nginx-1.30-alpine-slim.sbom.spdx.json`、`docs/postgis-16-3.5-alpine.sbom.spdx.json` 已生成。app 扫描 Critical `0`、High `0`；共索引 `111` 个包，未检测到漏洞。
- Alpine 候选镜像隔离验证：`0027` 迁移成功；随后完整 `head=0028` 迁移成功；镜像内全量 pytest 通过（271 项）。验证使用一次性 PostGIS PostgreSQL 和 Docker 网络，已清理。
- 追加扫描发现旧 Nginx `nginx:1.27-alpine` 存在 Critical `6`、High `27`，已切换为固定 digest 的 `nginx:1.30-alpine-slim@sha256:45c3810793fe3e982fb614c67e1b696816aff3ec742620e1ef7cd9d3184185ef`；新镜像扫描为 Critical `0`、High `0`，共 26 个包。
- PostGIS `postgis/postgis:16-3.5-alpine@sha256:d2fe6296c8ed5b21b31a426f51b9176b4d89f80a0a380632a7a833d604951273` 扫描为 Critical `1`、High `17`，共 147 个包，主要来自镜像内 Go stdlib；Scout 推荐的 `postgres:16-alpine` 同样仍有 Critical/High，暂未找到兼容且清零的替代版本，因此数据库镜像漏洞成为新增发布阻断。
- PostGIS 漏洞来源进一步定位为 `/usr/local/bin/gosu`（入口脚本用于切换到 `postgres` 用户）携带的 Go stdlib `1.24.6`；官方 `tianon/gosu:1.19` 同样为 Critical `1`、High `16`，未发现可直接替换的 `gosu:1.20/1.21` 标签。直接删除或替换该二进制会破坏官方数据库入口脚本，因此本轮不构建未经验证的派生数据库镜像。
- 补偿控制核验：PostgreSQL 仅加入 `internal` 网络且不映射宿主端口；app/scheduler 不对外发布端口；Nginx 固定 digest `sha256:45c381…185ef` 的精确扫描为 Critical `0`、High `0`。这些控制不能替代数据库漏洞修复或书面风险接受。
- 风险分级判断：不属于“可直接忽略”的漏洞。当前可利用性被显著降低（`gosu` 仅在容器入口切换用户时使用，PostgreSQL 不对公网暴露，外部入口只有 Nginx）；负责人已于 2026-08-12 按范围冻结清单作出“临时有条件风险接受”，不等同于误报或永久豁免。

## 下一步

下一步完成发布范围审阅并冻结 commit/tag，再申请 ECS/ACR、证书和生产密钥等外部资源操作；PostGIS 按临时风险接受条件执行，并在补丁镜像可用或首次 ECS 上线后 30 天内复核。生产新库使用 `0027`，本地/阶段 0 使用默认 `head`。
