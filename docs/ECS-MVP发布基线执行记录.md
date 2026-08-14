# ECS MVP 发布基线执行记录

> 状态：ECS MVP 已上线；监控轨基线已进入观察期，研究轨仍关闭。阶段 0 叠加测试尚未启动（2026-08-13）

## 目标

在阿里云 ECS 上发布已冻结的监控轨 MVP，并把研究轨作为后续可关闭、可回滚的阶段 0 叠加能力。首次生产部署不承担研究轨正式发布职责。

发布范围冻结清单见 [`docs/ECS-MVP发布范围冻结清单.md`](ECS-MVP发布范围冻结清单.md)。在负责人确认并从混合工作区提取干净发布集合前，不创建生产 tag、不推送 ACR。
候选文件提取规则见 [`docs/ECS-MVP发布候选文件清单.md`](ECS-MVP发布候选文件清单.md)。

## 当前证据

| 项目 | 当前状态 | 证据/说明 |
| --- | --- | --- |
| 当前分支 | `codex/ecs-mvp-release` | 已完成当前 ECS MVP 发布提交；后续研究轨改动不得直接覆盖生产镜像 |
| 工作区 | 发布工作区已提交，仍有运行/生成物未跟踪 | `.workbuddy/`、`backend/data/`、前端静态构建物等不属于发布输入 |
| 生产 Compose | 已上线 | `compose.yaml` + `compose.prod.yaml`；线上记录显示 `postgres/app/scheduler/nginx` 均已启动 |
| 生产认证 | 已实现 | Cookie Session、bcrypt、CSRF、四角色；候选 Alpine 镜像内全量 pytest 已实测 |
| 研究轨开关 | 线上保持关闭 | `RESEARCH_TRACK_ENABLED=false`；研究 API、研究页面权限、研究调度和 Provider 不进入 MVP 生产路径 |
| 研究迁移 | 已建立双轨目标 | 新增幂等 `0027`（直接依赖 `0021`）作为 MVP 目标；`0028` 合并 `0026/0027` 供本地/阶段 0 使用 |
| 域名/证书 | 技术验证待配置 | 技术验证阶段使用 ECS 公网 IP + IP SAN 自签名证书；正式上线前再申请域名和受信任证书 |
| 生产密钥 | 未配置 | 仅模板占位；不读取或输出任何真实凭据 |
| ECS 实测 | MVP 已完成上线，阶段0实测未开始 | 引用部署任务记录了 HTTPS、登录、Nginx、App、PostgreSQL、Scheduler 和基础网络边界；仍需从 ECS 采集一次可归档的命令输出作为本记录附件 |

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

1. 后续研究轨改动不得直接覆盖已经上线的 MVP 镜像；必须使用独立覆盖编排并可单独停止。
2. 生产新库必须使用 `ALEMBIC_UPGRADE_TARGET=0027`；阶段0研究组件不得擅自把生产数据库升级到完整研究 head。
3. PostGIS 数据库镜像 Critical `1`、High `17`；负责人已于 2026-08-12 临时接受，条件、补偿控制和复核期限见范围冻结清单；补丁镜像可用后必须复扫。
4. 阶段0尚缺 ECS 现场资源基线、固定旁路镜像的正式 SBOM/漏洞扫描、研究执行器实际链路和 48 小时浸泡证据。

## ECS MVP 上线确认（2026-08-13）

引用任务《规划ECS MVP部署计划》的上线记录已确认以下线上基线：

- Nginx、App、PostgreSQL、Scheduler 已启动；App 与 PostgreSQL 健康检查正常。
- 公网入口使用 HTTPS；网站登录、首位管理员初始化和 bootstrap 配置移除已完成。
- PostgreSQL、App、Scheduler 未对公网发布业务端口；外部入口为 Nginx。
- ECS Workbench/SSH 与 UFW 基础访问已恢复，业务端口按 80/443 对外。
- App 镜像已按不可变 digest 更新过登录页和默认浅色主题修复版本。

以上是部署任务中的操作证据摘要，不替代本次阶段0的 ECS 现场采样。阶段0开始前仍需在 ECS 保存以下脱敏输出：

```bash
cd /opt/supplier-risk-monitoring
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml ps
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml config --services
docker stats --no-stream
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml exec -T app alembic current
```

命令输出不得包含环境文件内容、密码、API Key、Cookie 或 Token；保存后再进入研究轨叠加审批。

## 阶段0叠加执行边界（当前）

1. 先完成上节 ECS 现场采样，并确认 MVP 观察期无异常。
2. 仅使用独立的 `compose.stage0.yaml` 叠加固定 digest 的 RSSHub 与 Crawl4AI；真实研究执行器完成前不启动空壳 `research-worker`。
3. 所有研究容器仅加入内部网络，不映射宿主端口；Crawl4AI 浏览器并发固定为 1。
4. 首轮只允许一条人工批准的研究任务；不启用日报/周报，不启动自动调度，不进入风险评分链。
5. 若内存、CPU、错误率或监控轨出现异常，立即停止叠加组件，不改动 MVP 四服务。

## ECS 现场基线采样（2026-08-13）

已通过 `deploy` 专用 SSH 公钥完成只读现场采样，未读取生产环境文件内容，未修改或重启任何容器：

| 检查项 | 实测结果 |
| --- | --- |
| Compose 服务 | `postgres`、`app`、`nginx`、`scheduler` 四项 |
| 容器状态 | App/PostgreSQL healthy；Nginx/Scheduler running |
| App 健康 | `{"status":"ok","database":"ok"}` |
| Alembic | `0027` |
| 整机内存 | 3.4 GiB，总可用约 2.4 GiB；无 Swap |
| 根磁盘 | 40 GiB，已用约 5.1 GiB（14%） |
| 基线容器内存 | App 约 85 MiB、Scheduler 约 120 MiB、PostgreSQL 约 72 MiB、Nginx 约 4 MiB |
| CPU 复采 | App 两次低值约 0.41%/0.16%，一次短脉冲约 68.59%；进程累计约 0.4%，未见持续满载 |
| 镜像一致性 | App 为 `2c0c…bc0`；Scheduler 仍为旧 `c517…e14`，存在运行镜像漂移 |

### 监控轨新增阻断：待处理信号并发竞争

- Scheduler 过去 6 小时日志中统计到 23 次 `uq_risk_events_dedup_key` 唯一键冲突，共观察到 3 轮“处理新信号”。
- 日志显示中央“定时采集与处理”和 `nmc-weather` 独立采集任务在同一个 `*/30` 时间点运行；两条路径都会调用 `_process_pending_signals()`。
- 两个并发任务可能同时选中相同的待处理信号，并竞争创建相同 `risk_events.dedup_key`，其中一方回滚并记录失败。
- 当前任务最终仍记录为成功，健康接口也正常，但该竞争会制造错误日志，并可能造成重复模型调用、重复处理或部分信号延后，因此暂不满足阶段0“无重复计费、无持续积压”的前置条件。
- 在修复、测试并只重建 Scheduler 前，不启动 RSSHub、Crawl4AI、研究 Worker 或 48 小时浸泡。

## 阶段0旁路编排准备（2026-08-13）

- 新增 `compose.stage0.yaml`，只包含固定 digest 的 RSSHub 与 Crawl4AI；二者仅加入现有 `internal` 网络，未配置宿主端口映射。
- RSSHub 限制为 0.5 CPU / 512 MiB；Crawl4AI 限制为 1 CPU / 1536 MiB，并配置 256 MiB `/dev/shm`。
- Crawl4AI 强制从未提交的 `deploy/.env.stage0` 注入 `CRAWL4AI_API_TOKEN`；缺少 Token 时 Compose 渲染直接失败。
- `deploy/crawl4ai.stage0.yml` 将页面预算、全局页面池、后台队列 Worker 与单调用方并发均限制为 1，单次墙钟时间限制为 120 秒。
- 当前不增加 `research-worker`：代码中只有租约/状态骨架，没有真实搜索、单页读取和报告生成执行器；启动空壳服务不能形成有效浸泡证据。
- Compose 静态渲染通过：合并后为既有四服务加 RSSHub/Crawl4AI；只有 Nginx 发布 80/443，两个旁路服务的宿主端口数均为 0。
- Crawl4AI 镜像内实际加载阶段0配置通过：`max_pages=1`、队列 `workers=1`、`per_principal=1`、页面池 `max_pages=1`。
- 三份非敏感配置已通过 SSH 同步至 ECS，SHA-256 与本地一致；真实 `deploy/.env.stage0` 未创建，旁路容器未启动，既有四服务保持运行。
- Docker Scout 1.23.1 对这两个本地多架构 digest 的 OCI 临时导出失败，SBOM/漏洞概览未形成；本机也无 Trivy/Syft/Grype。该安全门禁仍为阻断项，不得将扫描失败误记为通过。
- 已在 ECS 固定并验证 Trivy `0.66.0` amd64 镜像 digest `sha256:adbf…fb8`。首次漏洞库下载访问 `mirror.gcr.io` 连接超时；改用官方备用 `public.ecr.aws/aquasecurity/trivy-db:2` 后可开始下载，但约 7.3% 时连接被重置。两次均未进入目标镜像漏洞判定阶段。
- ECS直拉 RSSHub/Crawl4AI 固定镜像在 5 分钟内未完成并超时，镜像未注册；本机 Docker 又被未完成拉取占用而持续无响应。本轮不重启 Docker Desktop、不反复消耗公网链路。
- Trivy临时容器均使用 `--rm`，ECS无遗留扫描容器；现有 App、PostgreSQL、Scheduler、Nginx保持运行。安全门禁仍未通过，旁路容器继续不启动。
- 本轮未执行抓取、搜索 Provider 或模型调用。

### 阶段0固定镜像安全门禁实测（2026-08-13）

- 本机固定使用 Trivy `0.66.0` amd64 镜像 digest `sha256:adbf…fb8`，漏洞库来自已缓存的 `public.ecr.aws/aquasecurity/trivy-db:2`；未读取或输出任何环境文件、Token 或 API Key。
- RSSHub 固定 digest `sha256:c762…a2cd` 已完成 rootfs 扫描，报告见 [`docs/stage0-rsshub-trivy.json`](stage0-rsshub-trivy.json)，CycloneDX SBOM 见 [`docs/stage0-rsshub.sbom.cdx.json`](stage0-rsshub.sbom.cdx.json)：共发现 Critical `17`、High `53`。结果包含 Node `tar` 的 Critical（当前版本 `7.5.16`，修复版本为 `7.5.19`）以及 `ip-address`、`undici`、`brace-expansion` 等 High/SSRF 或拒绝服务相关项；不能按“零高危”门禁放行。
- Crawl4AI 固定 digest `sha256:bd36…690` 的首次远程扫描曾因 Trivy 超时和 Docker Registry 层 `EOF` 失败；后续已改用 D 盘 rootfs 离线方式完成可信复扫，最终结果见下文“Crawl4AI 固定镜像复扫结果”。
- 为避免把本机 Docker Desktop 的多架构导出缺陷误判为安全通过，所有扫描均未启动 RSSHub/Crawl4AI；也未执行抓取、搜索 Provider 或模型调用。
- 结论：阶段0旁路安全门禁未通过，禁止创建真实 `deploy/.env.stage0`，禁止启动 RSSHub、Crawl4AI、research-worker 或自动研究调度；两个固定 digest 的漏洞报告与 SBOM 已形成，最终阻断原因见下文复扫结果。

### Crawl4AI 固定镜像复扫结果（2026-08-13 18:32 CST）

- 已将固定 digest `sha256:bd36…690` 导出到 D 盘临时归档，在 Docker 临时卷内解包后完成 Trivy `0.66.0` rootfs 扫描；临时容器始终保持 `Created`/未启动状态。
- 漏洞报告见 [`docs/stage0-crawl4ai-trivy.json`](stage0-crawl4ai-trivy.json)，CycloneDX SBOM 见 [`docs/stage0-crawl4ai.sbom.cdx.json`](stage0-crawl4ai.sbom.cdx.json)。扫描识别 Debian 12.15、583 个 Debian 包、2 个语言包清单，共发现 Critical `44`、High `604`。
- 主要风险集中在浏览器/媒体运行时与系统库（`linux-libc-dev` 215 项、`pillow` 26 项、`nltk` 22 项等）。镜像标签与实际 Python 元数据均为 Crawl4AI `0.9.2`；原始 Trivy 结果另出现无 `PkgPath` 的 `crawl4ai@0.7.8` 条目，属于需在候选复扫中核对的残留/第三方包记录，不能直接当作当前运行时版本证据。
- 该结果是完整可信扫描，不是扫描工具失败或误报豁免；阶段0安全门禁继续阻断，旁路组件不得启动。后续应先选定已修复且可复扫的 Crawl4AI 版本，再重新验证资源限制、SBOM、漏洞和最小抓取链路。

### 阶段0上游镜像核查补充（2026-08-14）

- RSSHub 上游的 `Docker Release` 工作流仍处于 active，最新可见成功运行是 #8221（2026-08-12，提交 `e086c17fa01bfbedf0dd4f4ee0b79c35a96fba61`）。工作流同时发布 Docker Hub 与 GHCR，并生成 amd64/arm64 多架构镜像；这证明上游近期有成功构建，不等于已有通过本项目安全门禁的新 digest。
- RSSHub 仓库当前没有正式 Git Tag；GHCR 版本 API 和 manifest 查询要求认证，当前 Docker Hub Registry 链路也无法完成读取。因此本轮没有把“上游存在构建”误记为“已核验新镜像”，也没有替换现有固定 digest。
- RSSHub `master` 当前提交（`7fcec5001ebb9b25fe9de1435f5aff216746ab84`，2026-08-13）晚于上述 Docker Release；尚未发现对应的新 Docker Release 成功证据或可公开核验的安全清零 digest。现有 RSSHub 固定 digest 的 Critical `17` / High `53` 扫描结果仍有效。
- Crawl4AI 上游最新正式 Release/Tag 仍为 `v0.9.2`（2026-07-15）；未发现比 `v0.9.2` 更新的正式版本。现有固定 digest `sha256:bd36…690` 的 Critical `44` / High `604` 复扫结果仍是阶段0阻断依据。
- 结论：本轮不启动旁路容器，不创建 `deploy/.env.stage0`，不更新 `compose.stage0.yaml`。下一步只能在“构建并重新扫描最小加固派生镜像”和“移除 RSSHub/Crawl4AI、改用已有受控读取路径”之间择一推进；任何候选都必须先完成 SBOM、漏洞、配置加载、资源限制和最小链路复验，再申请阶段0启动。

### 阶段0候选路径取舍（2026-08-14）

- RSSHub 报告的 17 个 Critical 中仅 1 个带有 Trivy 修复版本，Crawl4AI 报告的 44 个 Critical 中仅 12 个带有修复版本；其余主要来自基础系统、浏览器/媒体库和 Perl 等运行时，当前不能通过简单升级单个 Python/Node 依赖清零。
- Crawl4AI 报告中 `crawl4ai@0.7.8` 的若干 Critical/High 条目没有 `PkgPath`，而镜像元数据和 Python 元数据均显示 `0.9.2`。这些条目必须在候选镜像复扫中核对，不能直接当作误报，也不能在没有证据时据此放行。
- 因此“构建最小加固派生镜像”只能作为隔离候选：可尝试删除不需要的构建缓存、旧 Python/Node 元数据和非运行时工具，但在漏洞清零、功能回归和出网边界复验前不得替换固定 digest，更不得进入生产 Compose。
- 当前更稳妥的阶段0降级路径是：先使用现有 `backend/app/research/web.py` 的受控 HTTPS 单页读取器完成公开静态 HTML/RSS 的人工研究验证；它不支持依赖 JavaScript 渲染的页面，不能宣称等价于 Crawl4AI。Crawl4AI/RSSHub 仅保留为后续通过安全门禁或书面风险接受后的可选实验组件。
- 该取舍不改变现行生产 MVP，也不自动放宽漏洞门禁；若采用降级路径，必须单独记录“未验证 JS 页面、未启动旁路容器、无 48 小时旁路浸泡”的限制，阶段0只能标记为“受控直连 Spike 完成”，不能标记为旁路研究能力上线。

### 阶段0固定镜像临时风险接受与启动授权（2026-08-14）

- 负责人已明确接受当前 RSSHub（Critical `17` / High `53`）与 Crawl4AI（Critical `44` / High `604`）固定镜像的漏洞风险，仅授权用于本阶段0旁路 Spike；这不是生产长期放行，也不改变后续镜像必须固定、复扫和升级的要求。
- 接受范围严格限制为本 ECS、`compose.stage0.yaml` 中既有两个固定 digest、内部 `internal` 网络、单实例与现有资源上限；不得新增宿主端口、privileged 权限、主机目录挂载、数据库写入权限或任意出网配置。
- 运行期间不启动 `research-worker`、每日/每周调度、搜索 Provider、LLM 调用或批量采集；首轮只验证容器健康、内部网络边界、配置加载和整机资源，所有真实抓取/计费动作继续要求单独批准。
- 观察期自实际启动时刻起 48 小时；任一容器异常退出、OOM、监控轨健康异常、宿主端口暴露、资源余量不足或发现未经批准的外部调用时，立即停止 `rsshub` 与 `crawl4ai`，保留 MVP 四服务不变。观察期结束、上游发布可用修复镜像或需要扩大功能范围时，必须重新评审。
- 立即停止命令：`docker compose --env-file deploy/.env.production --env-file deploy/.env.stage0 -f compose.yaml -f compose.prod.yaml -f compose.stage0.yaml stop rsshub crawl4ai`。

### 阶段0旁路启动与48小时观察起点（2026-08-14）

- 启动前复核：ECS MVP 的 App/PostgreSQL 为 healthy，Scheduler/Nginx 正常；根盘可用约 `26 GiB`，可用内存约 `2.3 GiB`，仅 Nginx 发布宿主 `80/443`。固定 RSSHub/Crawl4AI 镜像已在 ECS 缓存，未再次拉取。
- 服务器侧创建 `deploy/.env.stage0`，权限 `600`；首次创建命令受 PowerShell 转义影响生成了非预期短值，已在任何验证流量前停止并重建 Crawl4AI。修复后的文件为 84 字节（变量名加 64 位十六进制随机 Token），Token 未输出、未同步到本地、未进入 Git。
- RSSHub 于 `2026-08-14T20:23:16+08:00` 启动；Crawl4AI 使用修复后 Token 于 `2026-08-14T20:24:00+08:00` 启动，后者作为 48 小时观察起点。计划观察截止为 `2026-08-16T20:24:00+08:00`，期间任何停止/重建均需重新计算观察期。
- 启动后实测：RSSHub root 经 App 容器内部网络返回 HTTP `200`；Crawl4AI `/health` 经内部网络返回 HTTP `200` 且 Docker health 为 healthy；两者都只在 `supplier-risk-monitoring_internal` 网络，`1200/tcp` 与 `11235/tcp` 均无宿主端口映射，重启计数均为 `0`。
- 首次资源快照：RSSHub 约 `195.7 MiB / 512 MiB`，Crawl4AI 约 `313.8 MiB / 1.5 GiB`；整机可用内存约 `1.8 GiB`，无 Swap；MVP `/api/v1/system/health` 为 HTTP `200`。本轮未发起网页抓取、搜索 Provider、LLM 或风险信号写入。

核查入口：

- RSSHub Docker Release 工作流：<https://github.com/DIYgod/RSSHub/actions/workflows/docker-release.yml>
- RSSHub 最新工作流运行：<https://github.com/DIYgod/RSSHub/actions/runs/31563109839>
- Crawl4AI v0.9.2 Release：<https://github.com/unclecode/crawl4ai/releases/tag/v0.9.2>

阶段0启停命令（必须显式同时提供生产与阶段0环境文件）：

```bash
docker compose --env-file deploy/.env.production --env-file deploy/.env.stage0 \
  -f compose.yaml -f compose.prod.yaml -f compose.stage0.yaml up -d --no-build rsshub crawl4ai

docker compose --env-file deploy/.env.production --env-file deploy/.env.stage0 \
  -f compose.yaml -f compose.prod.yaml -f compose.stage0.yaml stop rsshub crawl4ai
```

### 并发竞争修复（已部署并通过真实周期验收）

- 已在两个调度路径共享的 `_process_pending_signals()` 入口加入进程内非阻塞锁；一个批次运行时，重叠调用立即跳过，不再查询或处理同一批信号。
- 该实现覆盖当前单 Scheduler 容器，避免重复 AI 调用和 `risk_events.dedup_key` 并发写入；不改变采集频率、信号筛选和既有事务逻辑。
- 多 Scheduler 进程或多副本部署前，必须升级为 PostgreSQL advisory lock 或数据库级原子领取；当前不得扩容 Scheduler 副本。
- 定向并发测试 3 项通过；Scheduler、风险处理和数据源采集相关回归 20 项通过；Ruff 通过，`git diff --check` 通过。
- 生产代码 `app/scheduler/jobs.py` 的 mypy 检查通过；包含测试文件的定向检查仍被该文件原有的 4 处类型问题阻断（既有未标注 fixture 参数与过期 ignore），本次未扩大范围修复这些历史问题。
- 已以同一新 digest 同时重建 App 与 Scheduler，`RESEARCH_TRACK_ENABLED=false`；PostgreSQL 和数据卷未重建，Nginx 仅因 App 容器地址变化重启一次。
- 本地候选镜像 `supplier-risk-monitoring:ecs-mvp-20260813-scheduler-race` 构建成功，镜像内相关回归 21 项通过；已推送 ACR，不可变 digest 为 `sha256:ba7c44416f37f2927cf3022d9c6740968fd090b7bb35236763edebfdd214c1f4`。
- ECS 的 `deploy` 用户无 ACR 登录态，因此使用 SSH 将本地已验证镜像流式导入 Docker；远端 RepoDigest 与 ACR digest 一致。生产环境文件已备份为 `deploy/.env.production.bak.pre-scheduler-race-20260813`，`APP_IMAGE` 使用固定版本标签 `ecs-mvp-20260813-scheduler-race`。
- 2026-08-13 13:00 CST 真实重叠周期：中央任务处理 20 条信号，`nmc-weather` 独立任务记录一次“已有待处理信号批次运行，跳过本次重复处理”；部署后日志计数为唯一键冲突 0、锁跳过 1、处理失败 0、成功处理批次 1。
- 最终状态：App healthy，PostgreSQL healthy，公网健康接口 HTTP 200 且返回 `{"status":"ok","database":"ok"}`，Alembic 为 `0027`；本阻断项关闭。

### 阶段0前 ECS 现场基线复采（2026-08-13 18:00 CST）

- 生产 Compose 服务仍为 `postgres`、`app`、`nginx`、`scheduler` 四项；App/PostgreSQL 为 `healthy`，Nginx/Scheduler 正常运行。
- `https://127.0.0.1/api/v1/system/health` 返回 HTTP `200`，响应为 `{"status":"ok","database":"ok"}`；Alembic 当前为 `0027`。
- 根盘 40 GiB，已用约 12 GiB，可用约 26 GiB（32%）；内存总量约 3.4 GiB，可用约 2.3 GiB；未配置 Swap。
- 本次采样容器内存：App `85.1 MiB`、Scheduler `117.8 MiB`、PostgreSQL `82.68 MiB`、Nginx `3.84 MiB`；未观察到持续 CPU 满载。
- 端口边界保持不变：只有 Nginx 映射宿主 `80/443`；App、Scheduler、PostgreSQL 仅显示容器端口，不对宿主发布。
- 修复后 Scheduler 最近 5 小时日志计数：`uq_risk_events_dedup_key=0`、处理失败=0、并发锁跳过=9；旧记录中的 Scheduler 镜像漂移观察已由当前统一版本部署覆盖。
- 复采为只读操作，未读取生产环境文件内容，未修改或重启任何容器；RSSHub/Crawl4AI 仍未启动，未执行抓取、搜索 Provider 或模型调用。

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
