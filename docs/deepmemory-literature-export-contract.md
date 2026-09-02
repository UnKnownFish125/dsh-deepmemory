# deepmemory 契约修订 v0.3.1（literature 专属对接：归档带原始对话）

> 版本：contract-lib v0.3.1 · 2026-09-03 · 依据：dsh-literature 模型 v0.3 + glm 审核修订（G1-G7）
> 关联：`dsh-literature-model-v03.md`（literature 侧）+ 本文档（deepmemory 侧修订点）
> 定位：deepmemory 是 literature 的**原料来源之一**——按裁决"deepmemory 应只具备访问库的能力，
> 向 literature 查询、发起申请；库管理归 literature"，deepmemory 的契约修订目标是
> **开放"带原始对话的归档导出"** 供 literature 专属对接拉取。
> 修订：v0.3.1 吸收 glm 审核 G1（截断位置诊断修正）/G2（敏感策略）/G3（scope 过滤）/
> G5（复用 source 端点）/G6（since 增量）/G7（仓库钉死）。

---

## 0. 现状（已核实，截至 2026-09-03）

- `sources` 表已存原文：`(id, memory_id, content, created_at, protected_source_id, source_type, source_ref, trace_id, task_id, sensitivity_level)`，`source_type='message'`，共 543 条（当日 +6）
- **截断实证**：sources.content 每日 MAX(length) 恒顶在 **2000**（含 09-03），229 条顶格行 → **2000 截断发生在生产插件 plugin-v3.js:547 `source: redactSensitive(dialog).slice(0, 2000)`**；server 侧 `_save_source` 已是 `redacted[:8000]`（G1 修正：R2 原诊断指向 server 是错的）
- `memory_archives` 表：`(id, memory_id, archive_kind, summary, source_refs, period_start, period_end, created_at)` —— 存摘要+source_refs，不含原文；**0 条**（归档机制未启用）
- 已有单记忆 source 查询端点：`GET /v1/memories/<id>/source`（server.py:2593-2595，走 `get_sources()`）——**R1 应基于它扩展，勿重复造轮子**（G5）
- 无批量"归档导出"API
- 敏感链路：`_save_source` 先 `redact_text()` 脱敏 → sources.content 存**脱敏版**；原文敏感片段进 `protected_sources`（现 7 条带 protected_source_id，需 `/v1/sensitive/expand` 审批流展开）；插件发送前还有一层 `redactSensitive()`（G2 依据）

**环境基线（G7，deepmemory 侧）**：活跃开发树 = `/www/deepseek harness workspace/harness-memory-archive`（server.py=生产）；Gitea = `LazyFish/dsh-deepmemory`（本地克隆可能落后，**实施以活跃开发树为准**）。

---

## 1. 修订目标

让 literature（或任何对接方）能通过 deepmemory 拉取 **记忆 + 其原始对话来源**（脱敏版 + 敏感标记），作为原料归档。

---

## 2. 修订点清单（R1-R5，按 glm 审核修正）

| # | 修订点 | 位置 | 内容（v0.3.1） |
|---|---|---|---|
| R1 | **批量导出端点** | server.py 新端点 `GET /v1/memories/export-archive` | 基于 `get_sources()` 扩展为批量 + archives 聚合（G5）；响应中 sources 带 `protected_source_id` 标记；**加 `since` 增量参数**（G6，夜间拉取只取增量） |
| R2 | **截断修正（G1 修正诊断）** | **生产插件** `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js:547` | `slice(0, 2000)` → 放宽到 8000（与 server 侧对齐）+ **同步部署**；server 侧 8000 已就绪无需动；存量 229 条顶格行**如实标注不可恢复**（2000 以外从未落库），验收对存量改为"新写入不截断"而非"返回完整" |
| R3 | **归档含来源** | migrate_memory（cold/archive 落库） | source_refs 附带 sources id 列表（现状核实准确，单元素数组——补全为列表） |
| R4 | **归档还原带原文** | restore 流程 | 保留但注明：**现状 sources append-only 无 DELETE，关联天然不断，此项为防御性设计**（glm 小问题确认） |
| R5 | **鉴权/隔离/过滤（G3 修正）** | 新端点 | 过滤语义写死：**workspace_id 匹配 OR scope='global'**（与 deepmemory 召回语义对齐，防漏全局约束）；导出内容为**脱敏版**（sources.content 已脱敏）+ protected_source_id 标记 |

---

## 3. 敏感数据策略（G2，glm 审核新增——完整原文的结构性边界）

- **导出返回脱敏文本**：sources.content 本就是脱敏版（入库时 redact_text()），export-archive 直接返回该脱敏文本即可，不额外加工
- **protected 标记**：带 protected_source_id 的 sources 在响应中显式标记 `"protected": true` + `protected_source_id`
- **原文展开不在本契约范围**：受保护原文走既有审批流 `/v1/sensitive/expand`，literature 不绕过
- **验收 1 措辞修正**：改为"导出返回脱敏后的完整原始对话（非 2000 截断）"，对受保护行返回脱敏文本+标记，不承诺原文

---

## 4. 对接协议（literature 消费侧）

```
触发场景：
  A. literature 夜间定时拉取：GET export-archive?since=<上次游标>&workspace_id=
     → 只取增量（G6），归档层保存脱敏原文 + 元数据（archive.dir 可配置）
  B. deepmemory 记忆归档时主动推送（可选，P2）

数据流：deepmemory 记忆 + sources（脱敏）→ literature 归档层（原料）
  → 缓存区 → 夜间加工（deepseek-v4-flash-0731）→ 知识 → 挂载（溯源回归档）
```

---

## 5. 验收标准（v0.3.1 修正）

1. `export-archive?since=` 返回**新增量**记忆 + 脱敏原始对话（非 2000 截断）；受保护行带 `protected_source_id` 标记（G2）
2. 过滤：`workspace_id 匹配 OR scope='global'`（G3）——某工作区拉取不漏全局 bias 约束
3. Bearer token 鉴权（401 无 token）
4. 归档（cold/archive）写入 memory_archives 时 source_refs 含 sources id 列表
5. 新写入的 source 不再被 2000 截断（插件放宽后）；存量顶格行已知不可恢复（G1）
6. 与 literature 对接后：增量拉取 → 归档 → 缓存 → 夜间加工全链路跑通

---

## 6. 范围外

- 不做文献/知识管理（literature 职责）
- 不做库管理（分类树归 literature；deepmemory library 降级为回写标签——见模型 v0.3 §3.5 taxonomy 所有权）
- 不破坏既有记忆检索/注入/敏感审批流

---

## 7. 实施归属（G7 钉死）

- deepmemory 侧实施：**活跃开发树** `/www/deepseek harness workspace/harness-memory-archive`（勿用落后的 Gitea 克隆），按 B（6240）→ A（6230）推进，与文献库/kb 运维区分
- 前置独立项（G4，已由 literature 侧完成）：存量库触发器重建（bias scope=global 校验）+ bias 3 条 scope 脏数据修正——deepmemory 生产/测试库均已应用，实施时无需重复
- literature 侧对接模块：`/www/dsh-literature-repo` 6260 演进
