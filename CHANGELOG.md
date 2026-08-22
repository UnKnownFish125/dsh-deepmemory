# Changelog

All notable changes to dsh-deepmemory are documented here.

## [v0.2.2] - 2026-08-18

### Changed
- 任务看板改为按 Workspace 隔离；创建任务必须绑定当前 Session，任务卡支持打开或改绑同工作区对话
- 状态卡改为按 Session 独立，不再依赖 Workspace 或 task_id；旧 v3 CHECK 约束自动重建迁移
- preset 插件停止读写 legacy workspace_cards，统一使用 `/v1/v2/cards/<kind>/<session_id>`

## [v0.2.1] - 2026-08-17

### Added
- blank preset 模板：供第三方开发子插件使用的空白模板（不挂 deepmemory 业务）
- agent-preset 目录结构统一，task/daily/blank 三套 preset 共用 memory-server

## [v0.2] - 2026-08-17

### Changed
- 重新设计 v2 记忆与对话状态模型（docs/v2-plan.md）
- 状态卡采用 Git 式修订历史：一轮回复一个版本，永久保留元数据与差异

## [v0.1.0] - 2026-08-16

### Added
- DeepSeek Harness 长期记忆系统初版（对齐 AstrBot livingmemory，AGPL-3.0）
- README 设计依据：Generative Agents 三因子 + 2025 记忆综述 + HiMem 分层记忆
- 上架规范：package.json description/repository/license/exports 补全
