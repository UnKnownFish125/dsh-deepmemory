# 非线性工作台（workbench）完整计划书 v0.2——移交审核版

> 状态：待 GLM 审核（本计划书自包含，评审后进新会话实施）
> 背景一句话：工程项目需要一个**完整的项目结构树**告诉其他 agent 项目的结构/接口/契约；
> 工作台 = **统一会话管理器**（树节点挂会话，**子节点继承上层上下文**）；直观查看项目结构。
> 参考（已调研）：OpenViking（vfs 聚合）、dsh-synapse（会话地图 canvas）、mattpocock/skills（文档型技能+ADR）。

---

## 1. 目标与范围

### 1.1 要解决什么
| 痛点 | 本方案 |
|---|---|
| 其他 agent 不了解项目结构（翻代码/摸索/遗漏接口契约） | **结构树**：组件/服务/接口/契约树——一次性结构化呈现 |
| 会话之间上下文割裂（新会话从头讲背景） | **继承注入**：子节点会话自动带祖先结构/契约摘要 |
| 多会话分散（同时进行多项工作难管理） | **会话管理器**：树节点挂会话，一屏操控 |
| 契约分散在文档/代码（AI 难找） | 节点 `contract_ref` → 契约文档；AI 三通道读取 |

### 1.2 非目标
- 不替代 DSH（会话日志仍是唯一事实源——**同 dsh-synapse 哲学**）
- 不做记忆/知识检索（那是 deepmemory/literature）
- 不做自由画布（若需要会话自由地图——**用 dsh-synapse**（可选并存）

---

## 2. 架构（三层载体）

```
┌─ ① 结构树（worktree）── 结构化存储（worktree_db——AI 程序化读"结构"）
│    节点：{id, parent_id, kind, name, desc, contract_ref, meta, status}
├─ ② 契约/接口详情 ── 文档（contract.md/SKILL/ADR——节点 contract_ref 指向）
└─ ③ 状态/记忆 ── deepmemory（已有——节点可挂记忆（doc-link））
```
**设计原则**：结构树=**结构化数据**（可遍历/继承/程序化）；契约=**文档**（逐字读/评审）；状态=**语义记忆**。

---

## 3. 数据模型与 API

### 3.1 worktree_nodes（独立 worktree.db——与记忆/知识解耦）
```sql
CREATE TABLE worktree_nodes (
  id TEXT PRIMARY KEY,              -- 'deepmemory/memory-server'（路径式 id）
  parent_id TEXT,                   -- 根=null
  kind TEXT CHECK(kind IN ('root','component','service','interface','contract','doc')),
  name TEXT NOT NULL,
  desc TEXT,
  contract_ref TEXT,                -- 契约文档路径（docs/xxx.md）
  meta TEXT,                        -- JSON：端口/路径/接口面/依赖/status
  updated_at REAL
);
CREATE TABLE node_links (           -- 节点 ↔ 文档/会话/记忆 关联
  node_id TEXT, kind TEXT CHECK(kind IN ('doc','session','memory')),
  ref TEXT, created_at REAL
);
```

### 3.2 API（worktree 服务——P0 与 memory-server 同栈，后续可抽离）
| 端点 | 说明 |
|---|---|
| `POST /v1/worktree/nodes` | 建/更新节点 |
| `GET /v1/worktree/tree?root=` | 子树（含 meta 摘要） |
| `GET /v1/worktree/ancestors/<id>` | **祖先链**（继承注入取数） |
| `GET /v1/worktree/contract/<id>` | 节点契约（读文档/或返回 ref） |
| `POST /v1/worktree/import` | AGENTS.md/docs 自动导入（P3） |

---

## 4. 继承注入（核心机制）

### 4.1 语义
- 会话挂节点 → 注入 = **祖先链摘要**（root→node 每层一行）：
  ```
  [WORKTREE] deepmemory > 主体 > memory-server
    · memory-server: 6230 / data/memory.db / export-archive+get_sources
    · 契约: docs/deepmemory-literature-export-contract.md
    · 子节点(可挂会话): memory-server > 后端服务 …
  ```
- **继承**：子节点会话**天生携带全链**（不用重复问/不会漏）

### 4.2 实现（P1：`_worktree-plugin`——preset 组件）
- `systemPrompt.context({name:'worktree', order:46, text})`（D3/轨 B 同源机制——**注意 order 必填**）
- 数据：`GET /v1/worktree/ancestors/<node>`（TTL 300s 缓存——**回合内冻结**稳定）
- 预算：祖先链 ≤5 层 ×2 行（≈200-400 token——**小**；超 5 层截断（保留 root 与近端）

---

## 5. 会话管理器（多会话）

| 能力 | 实现 |
|---|---|
| 会话挂节点 | `node_links(kind='session')` + 会话元数据（worktree_node_id） |
| 多会话操控 | 树节点 → 会话卡（DSH API：列表/创建/切换/发消息——`session.rpc`/`api/session.*`） |
| 继承注入 | 会话壳（上节 4）——**打开即继承** |
| 会话可视化 | **dsh-synapse 可选**（会话地图——canvas——独立插件并存；worktree 树为结构层） |

---

## 6. UI（P2：workbench 视图——DSH client 插件）

```
┌─ 工作台面板（dsh 插件 client.js—新视图）───────────────┐
│ 左：结构树（折叠/展开——React 树）                      │
│    deepmemory › 主体 › memory-server › 后端服务          │
│ 右：选中节点的 会话卡列（开/停/切换/新建/给指令）          │
│     ├ 会话 card 1（标题/状态/消息数……）[打开] [停]      │
│     └ 会话 card n                  [打开] [停]         │
└──────────────────────────────────────────────────┘
```
- **自绘**（React——树+卡片——不依赖 synapse 画布）
- 【可选扩展】synapse 会话地图作为另视图（工作区/会话——非结构）

---

## 7. AI 读取三通道
1. **注入**（祖先前缀——自动——P1）
2. **工具**（`worktree_descendants(id)` / `worktree_contract(id)`——按需深挖——P3）
3. **文档**（contract_ref 原文——评审/引用）

---

## 8. 实施阶段（含依赖/工作量）

| 阶段 | 内容 | 依赖 | 工作量 |
|---|---|---|---|
| **P0** | worktree 表+API（CRUD/祖先链/契约）——memory-server 同栈 | 无 | 0.5 天 |
| **P1** | _worktree-plugin 继承注入（context order:46 + 冻结 + 预算） | P0 | 2h |
| **P2** | WebUI 工作台（树+会话卡——dsh client 插件） | P0 | 1-2 天 |
| **P3** | 工具接口（worktree_descendants/contract）+ AGENTS.md 自动导入 | P0 | 0.5 天 |

**上线顺序**（两阶段铁律——AGENTS.md 已固化）：
`测试机(3091) 全验 → 生产`。每阶段：先测试机（sync-test-env.sh 每日镜像保持最新）→ 门禁（ESM/session.models 断言）→ 生产。

---

## 9. 验收标准
1. **结构树**：注册 deepmemory 三层示例（root/主体/服务——含端口/契约 ref）——树 API 遍历正确
2. **继承注入**：memory-server 节点开会话 → 注入含全链摘要（[WORKTREE] 段出现）——**回合内冻结**（命中率不回退——check_cache_health PASS）
3. **多会话**：树节点挂 2+ 会话——卡片开/停/切换——会话独立运行
4. **AI 读**：agent 用 `worktree_contract(memory-server)` 拿到契约（无需人述）
5. **预算**：注入增量 ≤500 token（祖先链摘要）；大节点（>10 子）不拖慢
6. **synapse 兼容**（若启用）：会话地图与 worktree 树并存——无冲突

---

## 10. 风险与回滚
| 风险 | 缓解 |
|---|---|
| 继承注入影响缓存命中 | 回滚：**回合内冻结 + 稳定排序**（同 L2/L3 纪律）；注入变化仅 TTL 边界；回退=关插件（context 删除——单点） |
| 树数据漂移（结构变更） | 手动 ST 更新 + AGENTS.md 导入（P3 起半自动）；node_links 审计 |
| UI 复杂度 | P2 自绘简洁树（初始）；canvas 交给 synapse（可选） |
| worktree 与记忆耦合 | P0 独立 worktree.db（解耦——后续抽离独立服务） |

---

## 11. 参考（已调研）
- **OpenViking**（volcengine/OpenViking）：vfs 聚合记忆/资源/技能——**借鉴其"统一树状视图"理念**；dsh-memory-plugin 结构（client+index+cordis.patch）
- **dsh-synapse**（liangmianya，npm v0.4.1）：**会话非线性地图**（canvas——纯呈现层，DSH 会话为事实源）——作为**会话视图可选件**；安装已于测试机验证
- **mattpocock/skills**：SKILL.md + **ADR**（决策记录）——**契约/方案文档型**先例

---

## 12. 待审问题（GLM）
1. worktree 存储：P0 同栈（memory-server）→ 后续独立——**可接受？** 还是首版即独立服务？
2. 继承注入：**常驻摘要**（100% 回合携带）vs **工具按需**（触发才问）——我推荐**常驻小摘要 + 工具深挖**（关键信息不靠模型主动问）
3. UI：P2 自绘树 vs 直接扩展 dsh-synapse（树层并入画布）——我推荐**独立自绘**（解耦、清晰）
4. 导入：AGENTS.md 自动解析（frontmatter 树）——首版手动注册（P3 再自动）——**OK？**
