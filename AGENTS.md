# 供应商风险监控 - 项目约定

## 提交纪律
- 每个分项任务完成后，先汇报实现、验证结果与证据，并在汇报中明确请求用户确认是否提交 git；未经用户确认不得提交。
- 提交时按任务边界做原子提交，只暂存该任务允许的路径，绝不纳入无关改动或未跟踪杂项。
- 提交信息用简体中文，详细描述变更内容。

## 工作区整理
- 本地工作产物与归档已加入根 `.gitignore`，不进入版本库：`.omo/`、`.workbuddy/`、`backend/app/static/`、`backend/_wip_backup_engine/`、`backend/supplier_risk_monitoring.egg-info/`、`backend/data/`、`frontend/test-results/`、`docs/archive/`。
- 冻结文件（不得修改）：`DESIGN.md`、`frontend/src/components/ResearchView.tsx`、`frontend/src/components/SourceOnboardingAgentView.tsx`。

## 后台服务与长驻进程
- 禁止在 shell 工具调用中前台运行长驻进程，包括但不限于：`npx vite dev|preview`、`npm run dev`、`docker compose up`（不带 `-d`）、`docker compose logs -f`、`.\scripts\start-research-worker.ps1`（不带 `-Detached`）、交互式 `psql`（不带 `-c`）、`git log`（不带 `--no-pager`）。
- 原因：调用方读取子进程 stdout 管道直到 EOF；`Start-Process` 启动的进程会继承该管道句柄，只要服务存活，调用就永远挂住，且命令正文其实早已执行完毕（输出出现 ≠ 命令返回）。
- 正确启动方式：本机非 Docker 的长驻进程一律使用 `.\scripts\start-detached.ps1`（内部经 WMI `Win32_Process.Create` 创建，不继承调用方管道句柄）：
```
.\scripts\start-detached.ps1 -FilePath node.exe -ArgumentList @("node_modules\vite\bin\vite.js","preview","--host","127.0.0.1","--port","4183","--strictPort") -WorkingDirectory "D:\SupplierRiskMonitoring\frontend" -Name t7-preview -ReadyPort 4183
```
- 停止方式：`.\scripts\stop-detached.ps1 -Name t7-preview`（内部 `taskkill /T /F` 终止整棵进程树）。
- 启动与验证必须拆成两次独立调用：先启动（命令立即返回，输出 `PID=`、`LOG=`、`READY=` 等行），再另起一次调用做 HTTP 探测确认服务可用。
- Docker 服务：一律带 `-d`；查看日志用 `docker compose logs --tail=50 <service>`。
- 清理：确认不再需要时用 stop 脚本清理，禁止留下孤儿进程；`--strictPort` 场景下残留进程会导致下次启动直接失败。
