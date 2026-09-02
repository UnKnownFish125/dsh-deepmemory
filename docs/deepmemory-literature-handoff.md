# deepmemory ← literature 专属对接移交包

> 2026-09-03 · 转交 deepmemory 维护方
> 组成：① 对接契约（已完成 v0.3.1）② literature 依赖 deepmemory 的接口面（保持兼容）③ 待 deepmemory 实施点
> 依据：用户裁决（deepmemory 只做记忆存取、literature 做知识组织）+ glm 两轮审核结论

---

## 一、对接契约（权威文档）

**绝对路径**：`/www/deepseek harness workspace/harness-memory-archive/docs/deepmemory-literature-export-contract.md`
（Gitea：`LazyFish/dsh-deepmemory` main，commit `3f8b14a`；副本：`/www/dsh-literature-repo/docs/`）

**一句话**：deepmemory 提供"带原始对话的归档导出"接口供 literature 拉取原料；按审核修正：
- **G1**：2000 截断在生产插件 `plugin-v3.js:547`（`slice(0,2000)` → 放宽 8000 并同步部署），server 侧已 8000 无需动；存量 229 条顶格行不可恢复
- **G2**：导出返回**脱敏文本** + `protected_source_id` 标记；原文展开走既有 `/v1/sensitive/expand` 审批流，不绕过
- **G3**：过滤 = `workspace_id 匹配 OR scope='global'`（防漏全局 bias 约束）
- **G5**：基于已有 `GET /v1/memories/<id>/source`（`get_sources()`）扩展，勿重复造轮子
- **G6**：加 `since` 增量参数（夜间只拉增量）
- **G7**：实施以**活跃开发树** `/www/deepseek harness workspace/harness-memory-archive` 为准（勿用落后 Gitea 克隆）

**修订点 R1-R5**：详见契约文档（R1 批量导出端点 / R2 插件截断放宽 / R3 归档 source_refs 列表化 / R4 防御性还原 / R5 鉴权隔离过滤）。

---

## 二、literature 依赖 deepmemory 的接口面（kb 工具链，需保持稳定兼容）

literature 的 kb 服务（`/www/dsh-literature-deploy/kb-server`，6262，经 `/lit-api` 代理）**只读调用** deepmemory 以下端点：

| deepmemory 端点 | 调用方 | 语义依赖 |
|---|---|---|
| `POST /v1/memories/search` | kb_query / kb_recall / constraints / contracts | 接受 `library` / `include_archived` / `workspace_id` 参数（分库已上线，直接透传）；workspace 隔离（无 workspace_id 时不可见全局记忆） |
| `GET /v1/memories/libraries` | kb_browse | 库目录计数（bias/core/eco/project/runtime） |
| `GET /v1/memories/list?workspace_id=&status=&limit=` | kb_constraints / kb_contracts | **枚举通道**（语义 search 对库内召回不全时全量枚举 + 本地过滤——已实测 list 支持 workspace_id/status 过滤） |
| `GET /v1/graph/memories` | kb_graph | 图谱检索 |
| `GET /v1/health` | health | 上游可达性 |

**兼容要求**：
1. `search` 的 `library` 透传与 workspace 隔离语义保持不变
2. `list` 端点的 workspace_id/status 过滤保持不变（literature 依赖它枚举 bias/core）
3. 新增 export-archive 不改变上述既有端点行为

**deepmemory 记忆归类现状**（已由 literature 侧完成，作为 kb 数据基础）：
bias 4（scope 已全 global，触发器已重建强制）/ core 45 / project 34 / runtime ~254（总计 337）

---

## 三、待 deepmemory 实施点（按 B→A）

1. **R2（插件侧）**：生产插件 `plugin-v3.js:547` 的 `source: redactSensitive(dialog).slice(0, 2000)` → 放宽到 8000，同步部署生产+测试机（G1——这是**空修高危**，只改 server 不生效）
2. **R1+R5+R6**：实现 `GET /v1/memories/export-archive?since=&workspace_id=`（复用 get_sources；过滤含 scope='global'；返回脱敏文本 + protected 标记）
3. **R3**：migrate_memory 归档写入 source_refs 列表化（含 sources id）
4. **G4 已由 literature 侧完成**（存量库触发器重建 + bias 3 条 scope 修正），无需重复

---

## 四、验收对照（供 deepmemory 自测）

1. `export-archive?since=` 返回增量记忆 + 脱敏原文（非 2000 截断），受保护行带标记
2. 过滤含 `scope='global'`（某 workspace 拉取不漏 bias）
3. 无 token 401
4. 新写入 source 不再 2000 截断（插件放宽后）
5. 既有 search/libraries/list/graph 行为不变

---

## 五、文献库（literature 6260）工具面（如需要）

| 工具 | 位置 | 说明 |
|---|---|---|
| literature_add / search / attach / link_evidence / update | `/www/dsh/home/.agent-presets/_literature-plugin/plugin-v1.js` | 文献原料采集（6260） |
| kb_query / browse / constraints / contracts / graph | `/www/dsh/home/.agent-presets/_literature-kb-plugin/plugin-v1.js` | 知识查询（6262，依赖上表 deepmemory 接口） |
