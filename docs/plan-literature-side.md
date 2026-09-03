# literature 侧改动方案（供 GLM 审核）

> 架构定位（已确认）：文献本体=归档；文献进来→加工拆解出**证据**（带对原文的指向）→
> **知识**（提炼产物，**向量库保存** = literature 自有 FAISS）；知识→证据→文献三层溯源链。
> 核心：文献不做向量（FTS + 归档用于溯源/引用），**知识走向量**。

## 0. 现状（6260 literature_server / 6262 kb-server）
| 层 | 现状 | 说明 |
|---|---|---|
| documents（文献） | ✅ 表+FTS（documents_title_fts）+ attachments（PDF 签名下载） | 归档完整 |
| evidence（证据/主张） | ✅ evidence 表（doc_id 指向原文献 + chapter_anchor/page 锚点字段） | 指向已备 |
| knowledge_items（知识） | ⚠️ 只有 **FTS**（knowledge_fts）+ knowledge_relations | **无向量** ← 本次核心 |
| 工具面 | ✅ literature_add/search/attach/link_evidence/update（6260）；kb 五工具（6262 走 /lit-api） | — |
| 夜间加工管线 | ⚠️ 设计在案（低谷价+uuapi v4-flash-0731） | 未实现（本次落地） |

## 1. 待实施改动

### 1.1 knowledge 向量库（literature 自有 FAISS）⭐ 核心
- **新建**：`data/knowledge.faiss` + `dim.json`（与 deepmemory 记忆 FAISS **完全独立**——两套向量分库分索引，绝不混掺）
- **模型**：`jinaai/jina-embeddings-v2-base-zh`（768 维中文专项；fastembed 支持已验证）——与 deepmemory 记忆模型**可同款**但索引独立
- **检索**：`kb_query` 语义走**知识向量**（top-k 语义 + FTS 兜底融合——RRF 排序）
- **写入**：知识创建/加工时向量化入库（异步批量；重建接口 `POST /v1/literature/knowledge/rebuild`）

### 1.2 夜间加工管线（文献→证据→知识，向量化入库）
- **时机**：凌晨低谷（03:00 后窗口；与 deepmemory 夜间批处理错峰 30min——不抢 token）
- **模型**：`deepseek-v4-flash-0731`（谷价；已有 llm_chat fallback 模式：0731→flash 兜底）
- **流程**（每个"待加工文献"）：
  1. 读文献（文本/解析后）→ **拆解**：LLM 产出证据列表（每证据含 claim/原文摘录/**锚点**（chapter_anchor/page））
  2. 证据入库（evidence，doc_id=文献 + 锚点——**溯源指向强约束：无锚点证据不入库**）
  3. **提炼知识**：LLM 从证据产 knowledge（single-atomic 单一性——一条知识一件事）+ 关联（knowledge_relations）
  4. **知识向量化**（jina）→ 写 knowledge.faiss
- **人工入库触发**：`POST /v1/literature/knowledge`（直接建知识）→ 同管线向量化（即时）

### 1.3 kb_query 语义改知识向量
- 现状：kb_query 语义走 deepmemory 记忆向量（透传）——**改为 literature 知识向量**（知识≠记忆——检索不借记忆向量）
- 结构：`kb_query(q)` → 知识向量 top-k →（可选）证据回溯（sources→evidence→doc）

### 1.4 G1 轨 B：[约束前提] 常驻段（kb 插件）
- bias/契约类约束（knowledge library=bias）→ kb 插件 assemble 时拉取 → 渲染 `[约束前提]` 段（**预算外**，独立于 deepmemory L2/L3）
- 与 deepmemory 轨 A（bias 记忆恒并）**双轨合并**：轨 A=记忆层约束回顾；轨 B=知识库约束（最新契约）——两轨互补（记忆可能滞后）

### 1.5 G2：kb_archive_library 工具
- `kb_archive_library(library, reason?)` → 库级归档（知识库标签级：bias/constraint 拒归档）
- `kb_browse(archived=true)` 枚举归档库（计数显示）

### 1.6 G3：kb_browse/contracts/constraints 结构化
- bias/契约知识按 `knowledgen_relations`/tags 输出结构化（供 agent 引用）

## 2. 验收
- 知识向量：`kb_query` 语义命中（样本 5 问 >90% 相关）
- 溯源：知识→evidence→doc 三层可导航（API 返回 sources+evidence+doc 路径）
- 锚点：evidence 无锚点不入库（强制）
- 缓存/注入：轨 B 段回合内冻结；不破坏命中率（check_cache_health）

## 3. 边界与风险
- **向量双库**：记忆（deepmemory）与知识（literature）**分库分索引**——换模型各自独立迁移（deepmemory 迁移工具已备；literature 用同款工具模式）
- 加工质量：v4-flash-0731 产出证据/知识——**人工抽检**（首 20 条）；质量不足升级模型（fallback 链已备）
- 夜间错峰：deepmemory 03:00 批处理 ↔ literature 03:30 加工——**错开避免并发 LLM**

## 4. 影响面
- 6260 literature_server（知识 FAISS + 加工管线）
- 6262 kb-server（kb_query 改向量）
- agent kb 插件（轨 B 注入 + kb_archive_library 工具）
- **无 deepmemory 改动**（除已述轨 A——独立方案）

---

## 审核修订 v1.1（GLM 2026-09-03）
- §0 修正：`literature_update` 工具不存在（那是服务端 PATCH API——工具面仅 add/search/attach/link_evidence 四件）
- **N2 已实施**：knowledge_items `library/archived/source_memory_id` 列 + library 校验/偏置拒归档触发器（生产库 ALTER+显式迁移 9 列；DDL 同步 SCHEMA——新建库同构）
- 1.3 过渡策略（N3）：**RRF 融合**——知识向量（新通道）+ deepmemory 透传（旧通道），**知识 ≥50 条后再切纯知识检索**（或保留 deepmemory 显式 fallback）
- 1.4 轨 B 挂 source_memory_id；两段合计总预算（N4）
- 错峰保险：deepmemory 批处理未完成信号 → literature 加工推迟（30 分钟 + 信号双保险）
- 验收：≥20 问（>90% 相关）；锚点验收"原文可定位"（正确性非仅存在）；小笔误 knowledgen→knowledge
