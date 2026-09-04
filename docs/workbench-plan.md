# 工作台方案（workbench-plan v0.1——待审）

> 需求（用户）：工程项目需要**完整项目结构树**告诉其他 agent 项目的结构/接口/契约；
> 工作台 = **统一会话管理器**（树节点挂会话，**子节点继承上层上下文**）；直观查看项目结构。
> 参考：OpenViking vfs（树状聚合）+ matt-skills（文档/SKILL/ADR——AI 读）。

## 0. 核心设计（三层载体——回答"文档还是知识库"）

```
┌─ ① 结构树（骨架）── 结构化存储（tree 表/vfs——AI 读"结构"）
│    节点: {id, parent_id, kind(component|service|interface|contract|session), name,
│           desc,  contract_ref(→文档), status,  open_sessions[]}
├─ ② 契约/接口详情 ── 文档（contract.md/SKILL/ADR——节点 contract_ref 指向——AI 读"细节"）
└─ ③ 状态/记忆 ── deepmemory（已有——节点可挂记忆（doc-link））
```

- **结构树**：结构化（查/遍历/继承——**程序化**）——不是自由文本
- **契约详情**：文档（确定性——AI 逐字读——评审用——glm 审同款）
- **状态**：deepmemory 记忆（语义）
- **工作台 UI**：树（①）+ 会话卡片（③ 操控）——一体化

## 1. 结构树数据模型（vfs 式）

```sql
CREATE TABLE worktree_nodes (
  id TEXT PRIMARY KEY,            -- 'deepmemory/memory-server'
  parent_id TEXT,                 -- 父节点（根=null）
  kind TEXT CHECK(kind IN ('root','component','service','interface','contract','doc')),
  name TEXT NOT NULL,             -- 显示名
  desc TEXT,
  contract_ref TEXT,              -- → docs/xxx-contract.md（文档型详情）
  meta TEXT,                      -- JSON：端口/路径/接口面/依赖
  updated_at REAL
);
```
- 示例（用户给的）：
  ```
  deepmemory(root)
    ├─ 主体(component)── 插件/preset/状态卡
    │   └─ memory-server(service)── 6230/库/索引
    └─ 后端服务(service)── deepmemory 后端（doc-link/export-archive...）
  ```
- **注册通道**：`POST /v1/worktree/nodes`（手动/agent 调用）+ `POST /v1/worktree/import/git`（从仓库 AGENTS.md/docs 自动导入骨架——**项目结构自动生成**）

## 2. 会话管理器（工作台核心）

| 能力 | 实现 |
|---|---|
| **节点挂会话** | 会话 `worktree_node_id` 字段（树节点 ↔ 会话多对多） |
| **子节点继承上层上下文** | 会话注入 = **祖先链上下文**（root→node 的 desc/contract 摘要聚合——**继承语义**：子节点会话自动携带父层契约/结构信息） |
| **多会话操控** | 树节点面板：会话卡片（开/停/切换/给指令——DSH API/RPC） |
| **看项目结构** | 树渲染（折叠/展开——粗粒度视图——WebUI 组件/或 dsh 插件 client.js） |

## 3. AI 读的三种方式
1. **注入**（会话开在节点 → 祖先链 context 注入——模型直接看到结构/契约）
2. **工具**（`worktree_descendants(id)` / `worktree_contract(id)`——按需查询）
3. **文档**（contract_ref 原文——评审/深度引用）

## 4. 实施模块
| 模块 | 位置 | 规模 |
|---|---|---|
| worktree 存储+API（树 CRUD/继承查询） | memory-server（或独立 worktree-server） | 中 |
| 工作台 WebUI（树+会话面板） | dsh 插件（client.js——新视图） | 大 |
| 导入（AGENTS.md/docs→树） | worktree/import | 中 |
| 会话继承注入（祖先前缀） | memory-plugin（注入段） | 小 |

## 5. 与 OpenViking 差异
- OpenViking：通用 vfs（记忆+资源）——**我们聚焦项目结构+会话管理**（语义更窄、与 deepmemory/literature/livetaskboard 整合即用）
- 参考其：树渲染（client.mjs）、vfs 元数据模型（节点 meta JSON）

## 6. 待审问题
1. 结构树存储：**memory-server 扩展** vs **独立 worktree-server**（推荐独立——与记忆解耦；或 info registry 扩展（快））
2. 继承注入：**祖先链全量** vs **摘要**（推荐摘要：root→node 每层 1-2 行——注入小）
3. 导入：AGENTS.md 自动解析（YAML frontmatter 树）——**首版手动注册**（简单）还是自动？
