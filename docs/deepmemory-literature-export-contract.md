# deepmemory 契约修订 v0.3（literature 专属对接：归档带原始对话）

> 版本：contract-lib v0.3 · 2026-09-02 · 依据：dsh-literature 模型 v0.3（完整知识库+归档体系）
> 关联：`dsh-literature-model-v03.md`（literature 侧）+ 本文档（deepmemory 侧修订点）
> 定位：deepmemory 是 literature 的**原料来源之一**——按裁决"deepmemory 应只具备访问库的能力，
> 向 literature 查询、发起申请；库管理归 literature"，deepmemory 的契约修订目标是
> **开放"带原始对话的归档导出"** 供 literature 专属对接拉取。

---

## 0. 现状（已核实）

- `sources` 表已存原文：`(id, memory_id, content, created_at, protected_source_id, source_type, source_ref, trace_id, task_id, sensitivity_level)`，`source_type='message'`，content 最长 2000 字符截断，共 529 条
- `memory_archives` 表：`(id, memory_id, archive_kind, summary, source_refs, period_start, period_end, created_at)` —— 存归档**摘要**(summary)+source_refs，**不含原始对话全文**
- 无"归档导出"HTTP API；`memory_archives` 目前 0 条（归档机制未启用过）

---

## 1. 修订目标

让 literature（或任何对接方）能通过 deepmemory 拉取 **记忆 + 其原始对话来源**，作为原料归档：

```
对接方调用：
  GET /v1/memories/export-archive?ids=...&with_sources=true&workspace_id=...
        ↓
返回（JSON）：
  {
    "memories": [
      {
        "id": 360,
        "content": "…提炼后记忆…",
        "type": "decision", "scope": "workspace",
        "library": "core", "importance": 0.85,
        "created_at": …,
        "sources": [                    ← 新增：原始对话（修订核心）
          {"source_type": "message", "content": "用户说：…完整原始对话…",
           "created_at": …}
        ],
        "archives": [                   ← 若有归档
          {"archive_kind": "compressed", "summary": "…", "period_start": …}
        ]
      }
    ]
  }
```

---

## 2. 修订点清单

| # | 修订点 | 位置 | 内容 |
|---|---|---|---|
| R1 | **sources 原文完整导出** | server.py 新端点 `GET /v1/memories/export-archive` | 导出记忆时 JOIN sources 取原文（source_type='message'），完整返回（或配置分段） |
| R2 | **source 原文截断放开** | server.py add_memory `_save_source` | 2000 字符截断 → 可配置（`source.max_chars`，默认放宽到 8000 或按对话整段） |
| R3 | **归档含来源** | migrate_memory（cold/archive 落库） | 写 memory_archives 时，source_refs 附带 source id 列表（现只存单个 source_ref） |
| R4 | **归档还原带原文** | restore 流程 | 恢复时如归档带来源 id，重新关联 sources（溯源不断链） |
| R5 | **export-archive 的鉴权与隔离** | 新端点 | 沿用 Bearer token + workspace 硬过滤；仅导出 status/tier 可选范围 |

---

## 3. 对接协议（literature 消费侧）

```
触发场景：
  A. literature 手动/定时拉取：POST/GET export-archive（带 token）
     → 归档层保存原文 + 元数据（archive.dir 可配置）
  B. deepmemory 记忆归档时主动推送（可选，Webhook/回调，P2）
     → literature 接收后入缓存区待夜间加工

数据流（与 literature 模型 v0.3 §2 对齐）：
  deepmemory 记忆 + sources 原文 → literature 归档层（原料）
  → 缓存区 → 夜间管线（flash-0731）→ 知识 → 挂载（溯源回 sources 原文归档）
```

---

## 4. 验收标准

1. `export-archive` 返回记忆时**带完整原始对话**（source_type='message' 全文，非 2000 截断）
2. workspace 硬过滤生效（只能导出自己有权限的 workspace）
3. Bearer token 鉴权（401 无 token）
4. 归档（cold/archive）写入 memory_archives 时 source_refs 含 sources id 列表
5. 归档还原后溯源链仍完整（知识挂载点 → sources 原文）
6. 与 literature 对接后：拉取 → 归档 → 缓存 → 夜间加工全链路跑通

---

## 5. 范围外（本修订不包含）

- 不做文献/知识管理（那是 literature 职责）
- 不做"库管理"（分类树/派生归 literature，deepmemory 仅存标签）
- 不破坏既有记忆检索/注入行为

---

## 6. 实施归属

按前期分工（deepmemory 用契约移交、不直接改生产）：本修订交付为**契约文档**，
由管理 deepmemory 的团队/会话在 deepmemory 源码仓库实施；literature 侧对接模块
在 dsh-literature 仓库实施（6260 演进）。
