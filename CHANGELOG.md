# Changelog

All notable changes to dsh-deepmemory are documented here.

## [v0.4.2.14] - 2026-08-22

### Added
- 状态卡自动生成暴露为全局/会话配置；Host 为 cordis 等非 deepmemory preset 提供可选会话卡生成
- 无状态卡时提供“初始化”按钮，可从最近会话历史立即生成第一版卡片
- 自动衰减间隔支持按小时配置，默认 24 小时（每天一次）

### Changed
- 任务看板按 Workspace 隔离，每个任务绑定并可改绑同工作区 Session；状态卡改为 Session 独立归属
- 会话状态卡修订历史从状态卡正文拆出，每个 revision 独立展示，默认显示最近三版
- 每日记忆衰减改由常驻 memory-server 调度，不再依赖 task/daily preset 是否挂载
- 记忆整合接通每日与手动触发；启用 LLM 摘要时通过 UUAPI `deepseek-v4-flash` 生成 canonical summary
- 维护页对“无候选组”和整合结果提供结构化反馈，并修复配置页 boolean/number/string 类型编辑
- 状态卡与对话压缩统一为五轮节拍：每完成 5 个 turn，先同步状态卡，再压缩最近 3 轮之前的完整历史
- compaction 压力线调整为 0.55、原文保留比例为 0.2；记忆 system section 改为会话级事件驱动缓存

### Fixed
- 记忆抽取固定使用 UUAPI `deepseek-v4-flash`，移除官方模型列表的隐式选择
- token 同时支持 `MEMORY_API_TOKEN_FILE`、`DSH_HOME` 与 `HOME` 回退，避免运行 home 不一致导致 401
- 修复衰减与检索并发修改 BM25、手动/自动重复衰减、整合错误误报无候选及 24 小时错误门闩
- 修订历史支持区域展开、单版本展开及逐字段修改前/修改后对比

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
