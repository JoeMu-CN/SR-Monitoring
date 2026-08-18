# ECS 试用实例初始化操作手册

> 适用实例：杭州华东 1、Ubuntu 22.04 64 位、2 vCPU、4 GiB、试用公网 IP。首次上线只部署监控轨 MVP，研究轨保持关闭。

## 0. 先确认的边界

- 不在 ECS 上从当前混合工作区构建生产镜像。
- 生产新库迁移目标固定为 `0033`。
- `RESEARCH_TRACK_ENABLED=false`。
- 只有 Nginx 对外；PostgreSQL、app、Scheduler 不映射宿主端口。
- 不把密码、API Key、Session 密钥、证书私钥写进 Git 或聊天记录。
- 当前 2 vCPU/4 GiB 只承诺 MVP 基线和低并发验证；研究轨需要单独执行 ECS部署验证阶段0容量测试。
- 技术验证阶段不要求先购买正式域名；可使用 ECS 公网 IP 加临时自签名 HTTPS 证书登录验证。正式域名和受信任证书留到生产上线阶段。

## 0.1 为什么生产配置需要域名

域名不是 ECS 或 Docker 的硬性要求。当前生产安全配置要求访问来源为 HTTPS，因此技术验证阶段使用“公网 IP + IP SAN 自签名证书”，正式上线阶段再切换到域名证书：

- `PUBLIC_ORIGIN` 必须是实际的 `https://` 来源；
- `SESSION_SECURE_COOKIE=true`，登录 Cookie 只通过 HTTPS 发送；
- Nginx 监听 443 并加载与访问地址匹配的证书。

正式域名用于提供稳定访问地址和受浏览器信任的 TLS 证书。技术验证时可以把 `PUBLIC_ORIGIN` 和 `ALLOWED_ORIGINS` 设置为 `https://<ECS公网IP>`，登录 Cookie 和 CSRF 仍然走 HTTPS。

### 没有域名时的选择

1. **当前技术验证**：为 ECS 公网 IP 生成带 IP SAN 的自签名证书，以 `https://<ECS公网IP>` 访问；浏览器会出现不受信任警告，需要手动继续访问。仅使用测试数据和受控额度，不作为正式业务入口。
2. **正式上线**：申请域名并取得受信任证书，替换 `PUBLIC_ORIGIN`、`ALLOWED_ORIGINS` 和 Nginx 证书。
3. **不采用 HTTP**：不要为了省略证书而降级到 HTTP；生产认证配置仍要求 Secure Cookie 和 HTTPS 来源。

## 1. 阿里云控制台先做

### 1.1 安全组入站规则

先删除或禁用不必要的入站规则。由于办公网络会在家庭宽带、公司 Wi-Fi 和公共 Wi-Fi 之间切换，固定个人公网 IP 白名单不适合作为主要访问方式。当前采用“业务网页公网 HTTPS、服务器运维走云控制台”的过渡方案。

当前过渡期只保留以下规则：

| 端口 | 协议 | 来源 | 用途 |
| --- | --- | --- | --- |
| 22 | TCP | 不开放公网；通过 ECS Workbench/云助手运维 | SSH 运维 |
| 80 | TCP | `0.0.0.0/0`（仅用于跳转 HTTPS） | HTTP 跳转 HTTPS |
| 443 | TCP | `0.0.0.0/0`，技术验证使用 IP SAN 自签名证书；正式上线使用域名证书 | HTTPS 业务入口 |

明确拒绝/不放行：`22`、`5432`、`8080`、`3000`、`8000`、Docker API 端口和所有研究轨旁路端口。安全组是第一层边界，容器 Compose 的无宿主端口配置仍必须保留。若控制台必须临时使用 SSH，可只添加个人当前公网 IP 的 `/32`，操作结束后立即删除。

业务 `443` 公网开放会增加登录攻击面，因此必须同时满足：HTTPS 证书有效、登录限流开启、Cookie Session/RBAC/CSRF 生效、生产密钥已更换、默认管理员引导变量已移除。后续可用 VPN/零信任网络进一步收紧访问范围。

### 1.2 公网 IP 与域名

记录当前公网 IP，仅用于过渡期访问。若使用正式域名，先在 DNS 控制台添加 A 记录并确认解析；正式生产应使用域名证书，不要把自签名证书当作长期方案。

### 1.3 云监控与快照

启用 CPU、内存、磁盘和公网流量告警；创建每日 ECS 快照，至少保留 7 天。数据库逻辑备份仍需单独执行并同步到 OSS，快照不能替代 `pg_dump` 恢复演练。

## 2. 进入 ECS 主机后初始化（推荐云助手）

以下命令必须在 ECS 主机内执行。可以选择：

1. **云助手**：阿里云控制台 → ECS 实例 → 云助手 → 发送命令。推荐此方式，SSH `22` 可以保持关闭。
2. **Workbench**：在 ECS 控制台打开终端；如果当前连接模式依赖公网 SSH `22`，只临时放行个人当前公网 IP `/32`。
3. **普通 SSH**：仅在临时维护时使用，完成后删除安全组和 UFW 的临时 `22` 规则。

不要把下面命令直接粘贴到 CloudShell 后认为它们会自动作用于 ECS。

CloudShell 是阿里云提供的独立临时终端；命令默认运行在 CloudShell 自己的环境中。只有显式执行 `ssh` 登录 ECS，或调用云助手/云 API 发送命令，才会作用于 ECS。本手册的命令应通过云助手、Workbench 或 SSH 终端执行。不要把真实密钥粘贴到仓库或聊天中。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git jq ufw fail2ban

# 确认 Docker 与 Compose 插件版本
docker version
docker compose version

# 创建非 root 部署用户（若控制台已创建则跳过 useradd）
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/supplier-risk-monitoring

# 主机防火墙：业务允许 HTTP/HTTPS；SSH 默认不开放公网
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo systemctl enable --now fail2ban
```

启用 UFW 前必须确认云助手或当前 Workbench 连接能够执行命令，避免把自己锁在 ECS 外；不要在唯一的 SSH 会话中直接执行 `ufw --force enable` 后再测试。阿里云安全组和 UFW 两层规则都要复核。如果 Workbench 当前连接模式依赖 SSH，先通过控制台加入当前公网 IP `/32`，确认新会话可用后再操作，完成后删除安全组和 UFW 临时规则。

## 3. 获取发布文件

推荐方式是从 ACR 拉取已经冻结的 app 镜像，ECS 只保存 Compose、Nginx 配置、生产环境文件和证书：

```bash
sudo -u deploy git clone <受控代码仓库地址> /opt/supplier-risk-monitoring
cd /opt/supplier-risk-monitoring
git checkout <冻结commit或tag>
```

如果采用 ACR 镜像交付，先登录 ACR，再确认 digest；不要使用 `latest` 作为生产版本：

```bash
docker login <你的ACR实例地址>
docker pull <你的ACR实例地址>/supplier-risk-monitoring/app:<不可变tag>
docker image inspect <你的ACR实例地址>/supplier-risk-monitoring/app:<不可变tag>
```

Compose 中的 `APP_IMAGE` 必须指向该不可变 tag 或 digest。当前候选 app digest 为：

```text
sha256:c51702ff7ed8eb8768cd1ef30bab91f779fb0b2b3dcdf7100b934414c1062e14
```

## 4. 注入生产配置

在 ECS 创建权限为 `600` 的 `deploy/.env.production`，从密钥管理工具或安全终端注入真实值：

```bash
sudo -u deploy install -m 600 /dev/null /opt/supplier-risk-monitoring/deploy/.env.production
sudo -u deploy editor /opt/supplier-risk-monitoring/deploy/.env.production
```

技术验证阶段即可创建配置文件，但 `PUBLIC_ORIGIN` 必须填写实际 ECS 公网 IP；正式上线前再替换为域名。至少需要配置：

安全注意：不要把完整的 `docker compose config` 输出发送到聊天或工单，它会展开并显示环境变量。若输出已经包含数据库密码、Session 密钥、数据源加密密钥或管理员初始密码，必须先轮换这些值，再启动容器。生产 Compose 已限制 PostgreSQL 只接收数据库变量，其他密钥不应进入数据库容器。

### 配置项来源速查

| 配置项 | 当前技术验证阶段的来源 |
| --- | --- |
| `POSTGRES_DB` / `POSTGRES_USER` | 项目固定值：`supplier_risk` |
| `POSTGRES_PASSWORD` | 在 ECS 本地用 `openssl rand -hex 24` 生成；与 `DATABASE_URL` 中密码保持一致 |
| `DATABASE_URL` | 使用同一个随机数据库密码拼入连接串；随机 hex 不含需 URL 编码的特殊字符 |
| `SESSION_SECRET` | ECS 本地用 `openssl rand -hex 32` 生成；必须与数据源密钥不同 |
| `DATA_SOURCE_SECRET_KEY` | ECS 本地生成 Fernet 密钥；用于加密数据库中的天眼查等数据源凭据，必须备份保存 |
| `APP_IMAGE` | 使用已核验的 ACR digest，不使用 `latest` |
| `PUBLIC_ORIGIN` | 技术验证填写 `https://120.26.0.76`；正式上线再改成域名 |
| `BOOTSTRAP_ADMIN_USERNAME` | 自己指定，例如 `platform-admin` |
| `BOOTSTRAP_ADMIN_PASSWORD` | ECS 本地随机生成的一次性管理员密码；首次登录后立即移除 |
| `AI_PROVIDER` / `AI_API_KEY` | 当前技术验证使用 `fake`，`AI_API_KEY` 留空；接入千问时才改为真实 Provider 和 Key |
| `SEARCH_PROVIDER` / `SEARCH_API_KEY` | 当前研究轨关闭，使用 `none`，Key 留空 |
| 天眼查 API Key | 不写入 `.env`；后续在平台数据源控制台加密录入 |

因此当前不需要向任何平台申请或填写的密钥包括：`SEARCH_API_KEY`、天眼查环境变量 Key，以及技术验证阶段的 `AI_API_KEY`。

- `POSTGRES_PASSWORD`、`DATABASE_URL`
- `SESSION_SECRET`
- `DATA_SOURCE_SECRET_KEY`
- 技术验证：`PUBLIC_ORIGIN=https://<ECS公网IP>`；正式上线：`PUBLIC_ORIGIN=https://实际域名`
- `AI_PROVIDER`、`AI_BASE_URL`、`AI_MODEL`、`AI_API_KEY`
- `RESEARCH_TRACK_ENABLED=false`
- `SEARCH_PROVIDER=none`
- `ALEMBIC_UPGRADE_TARGET=0033`

首次初始化管理员时才临时设置 `BOOTSTRAP_ADMIN_USERNAME` 和 `BOOTSTRAP_ADMIN_PASSWORD`。登录验证成功后立即清空这两个变量并重建 app。

## 5. HTTPS 证书

将正式证书文件放入 ECS 的 `deploy/certs/`，文件权限最小化：

```bash
sudo -u deploy mkdir -p /opt/supplier-risk-monitoring/deploy/certs
sudo chmod 700 /opt/supplier-risk-monitoring/deploy/certs
# 上传后：
sudo chmod 600 /opt/supplier-risk-monitoring/deploy/certs/privkey.pem
sudo chmod 644 /opt/supplier-risk-monitoring/deploy/certs/fullchain.pem
```

证书文件名必须与 `deploy/nginx/nginx.conf` 一致：`fullchain.pem`、`privkey.pem`。没有证书时不要启动生产 Nginx 443。

## 6. 首次启动顺序

```bash
cd /opt/supplier-risk-monitoring
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml config
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml pull app scheduler
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml up -d postgres
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml run --rm app alembic upgrade 0033
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml up -d --no-build app nginx
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml up -d --no-build scheduler
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml ps
```

检查结果必须满足：

- `postgres`、`app`、`scheduler`、`nginx` 均为 healthy/running；
- 只有 Nginx 有宿主端口；
- app 和 scheduler 日志显示迁移目标 `0033`；
- 研究 API 不出现在 OpenAPI；
- `/api/v1/system/health` 返回数据库正常。

## 7. 首次验收与止损

按顺序完成：

1. HTTPS 访问、登录、退出和 CSRF 写请求。
2. 四角色权限：`viewer`、`risk_analyst`、`risk_admin`、`platform_admin`。
3. 导入 10~20 家试点供应商，再做可重复导入验证。
4. 接入两条真实数据源和有限千问样例；先观察额度和错误率，再开启常规 Scheduler。
5. 执行一次数据库备份，并恢复到临时 volume/临时实例验证可读。
6. 记录镜像 digest、迁移版本、容器状态和关键验收日志。

额度异常或调度失控时：

```bash
docker compose --env-file deploy/.env.production -f compose.yaml -f compose.prod.yaml stop scheduler
```

需要同时阻止手动 AI/助手调用时，再停止 app。不要使用 `docker system prune`、广泛删除镜像或删除数据库 volume。

## 8. 当前不执行

- 不启动 RSSHub、Crawl4AI、research-worker。
- 不启用自动日报/周报。
- 不开放 PostgreSQL、app、Scheduler 端口。
- 不在 ECS 上直接修改代码后现场构建生产镜像。
- 不创建公网数据库端口或把 Docker socket 挂载给数据库容器。
