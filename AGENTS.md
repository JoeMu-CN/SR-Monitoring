# 供应商风险监控 - 项目约定

## 提交纪律
- 每个分项任务完成后，先汇报实现、验证结果与证据，并在汇报中明确请求用户确认是否提交 git；未经用户确认不得提交。
- 提交时按任务边界做原子提交，只暂存该任务允许的路径，绝不纳入无关改动或未跟踪杂项。
- 提交信息用简体中文，详细描述变更内容。

## 工作区整理
- 本地工作产物与归档已加入根 `.gitignore`，不进入版本库：`.omo/`、`.workbuddy/`、`backend/app/static/`、`backend/_wip_backup_engine/`、`backend/supplier_risk_monitoring.egg-info/`、`backend/data/`、`frontend/test-results/`、`docs/archive/`。
- 冻结文件（不得修改）：`DESIGN.md`、`frontend/src/components/ResearchView.tsx`、`frontend/src/components/SourceOnboardingAgentView.tsx`。
