# 供应商风险监控 - 项目约定

## 协作与决策流程
- 默认模式是「先排查、再汇报、后执行」：用户提出需求或疑问后，先只做只读排查（读代码、查库、查日志、复现问题），汇总结论与方案给用户决定，不得直接改代码。
- 需要复跑天眼查或其他外部接口以确认问题时，无需另行征得同意，可直接调用；在汇报中说明调用次数与额度消耗。
- 排查汇报固定包含：现象与结论、根因与证据（文件/行号/原始数据片段）、可选方案与取舍、涉及文件、验证方式、额度或时间成本。
- 只有用户明确同意某个方案（或明确说「直接改」「不用问」）后才开始修改；即使用户授权跳过询问，动手前仍用一两句话说明将改哪些文件。
- 修改完成后的汇报必须同时给出两件事，等用户一次答复：① 生效方式与建议（重建镜像、重启容器或热更新代码，由执行者视情况选择并说明理由）；② 是否提交 git，以及提交范围与提交信息。
- 未经用户确认，不修改文件、不启动或停止服务、不执行 git 提交。

## 提交纪律
- 每个分项任务完成后，先汇报实现、验证结果与证据，并在汇报中明确请求用户确认是否提交 git；未经用户确认不得提交。
- 提交时按任务边界做原子提交，只暂存该任务允许的路径，绝不纳入无关改动或未跟踪杂项。
- 提交信息用简体中文，详细描述变更内容。

## 验证与质量检查
- 任何代码修改完成后，在汇报前按改动范围运行对应验证，并在汇报中列出执行命令与结果；未运行必须说明原因。
- 后端（`backend/`，Python）：
  - 静态检查：`ruff check .`（必要时 `ruff format --check .`）
  - 类型检查：`mypy`（按项目配置，可只检查改动模块）
  - 单元测试：`pytest`（按改动范围指定测试文件；跨模块改动才跑全量）
- 前端（`frontend/`，TypeScript）：
  - 类型检查：`npm run typecheck`（即 `tsc --noEmit`）
  - 单元测试：`npm run test:unit`（按改动范围）
  - 端到端：涉及页面交互时运行相关 `npm run test:e2e`，避免无差别全量
- 验证强度与风险成比例：单文件或小改动只跑相关定向检查；跨模块、高风险或发布前才跑全量。
- 不得用“测试通过”代替证据：汇报中给出命令、退出码或结果摘要；跳过项说明理由。
- 验证通过后仍按“提交纪律”请求用户确认，不自动提交。

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
