# Changelog

All notable changes to dsh-deepmemory are documented here.

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
