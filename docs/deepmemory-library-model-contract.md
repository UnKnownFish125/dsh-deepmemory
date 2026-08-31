# deepmemory 知识库模型扩展契约（面向 agent 调度 · 分库 + 归档 + 文档溯源）

> 版本：contract-lib-v0.2 · 2026-08-31 · 移交 deepmemory 维护方实现
> 变更记录：v0.2 依据维护前审核修订——① restore 改为新增 `restore-tier`（扩域层跳转表 archive→active，明确 memory_class 还原），既有 status 级 `restore` 不动；② 库归档改新增 `archive-library`（直接批量 tier 更新，不走 migrate），既有 `archive` 路由语义保留；③ 触发器补 BEFORE UPDATE；④ 注入改双轨（bias 空则回退 type 分组）；⑤ G1 修复范围扩为 search+graph_search+BM25 三处；⑥ 代码基统一为源码仓库（v063 副本仅参照）；⑦ 修正加权位置行号、单测数 33、GET search 需新写。
> 定位：deepmemory 的**增量扩展契约**（不重建、不迁移既有数据）。由主模型设计并审核通过，供维护方（人/子代理）在既有 codebase 上按本契约实现。
> 派生背景：与 `dsh-literatum`（literatum 契约 v0.2）同源——literatum 解决"文献→证据→知识"的结构化知识管理，本契约解决 deepmemory 侧"面向 agent 的分库调度 + 死记忆归档 + 文档↔记忆溯源"。

---

## 0. 一句话

把 deepmemory 从"一个混着所有知识的库"升级为**面向 agent 的命名库体系**：行为约束进 bias 库（常驻、永不归档），主体/生态/项目知识分库（agent 按需调度），死记忆进归档（默认不召回、可显式查、可恢复），文档与记忆双向可溯源。

---

## 1. 现状基线（已实现，**禁止重复开发**，契约只在其上增量）

| 能力 | 现状（已核实） | 位置 |
|---|---|---|
| 生命周期分层 | `storage_tier ∈ {active, cold, archive}` + `migrate_memory`（active→cold→archive，archive 时压缩摘要写 `memory_archives`） | v2_domain.py:1316 |
| 自动衰减归档 | `run_decay`：importance 指数衰减；`archiving.auto_archive_enabled` + `archive_importance_threshold`(0.1) + `archive_days_threshold`(30) → `status='archived'` + 移出 BM25 | server.py:1606 |
| 手动归档/恢复 | `archive_memories(ids)` / `restore_memory(mid)`（status 级） | server.py:1680 |
| 会话软归档 | purge/import-replace 时 `status='archived'` 可恢复 | v2_domain.py:1007 |
| 检索隔离 | `search_memories`/`graph_search`/`apply_weighting` 过滤 `status='active'` | server.py 多处 |
| 来源留存 | `sources` 表（来源消息全文，审计）+ `documents.source_ref` 自由字符串 | server.py:932 |
| 注入分组 | plugin-v3.js:202 按 type 分组（规则与偏好/决定与目标/事实）；**保底加权在 server.py:723-725**（preference≥0.7 额外 +0.35·importance，其余≥0.7 +0.18） | plugin-v3.js:202 / server.py:723 |
| topic 标签 | 抽取 prompt 第 6c 条已输出 topic；`documents.topic_id` 有列有索引，**不参与检索过滤** | plugin-v3.js:72 |
| workspace 硬过滤 | `scope_allows` 召回后强制过滤（global/workspace/session） | server.py:740 |

---

## 2. 缺口清单（本契约要补的全部内容）

| # | 缺口 | 现状后果 |
|---|---|---|
| G1 | **检索漏过滤 storage_tier**：`migrate_memory` 到 archive tier 不改 `status`，archive 记忆若 `status` 仍 active 会**漏进召回**，污染 agent 上下文 | 归档形同虚设 |
| G2 | topic 不参与过滤、无词汇表约束、无库层级 | "所有知识混在一起"，无法按 项目开发/deepmemory生态/主体 分开 |
| G3 | 无库级归档（`archive_library`）、无归档检索入口（`include_archived`） | 项目结束的整组记忆无法批量进归档 |
| G4 | bias 库（总行为约束）概念缺失，约束与其他记忆同流 | 行为约束靠加权保底"碰运气"，无结构保证 |
| G5 | 文档↔记忆双向对应缺失（`document_links` 不存在，`source_ref` 不指文档） | "这条记忆对应哪份契约/这份契约有哪些记忆"查不出 |

---

## 3. 领域模型扩展

```
文档（文件系统，一等公民）         记忆（documents 表）
   dsh-literatum-contract.md  ←─ derived_from ─   "契约冻结 id 对口径"
        │                                                 ↑
        └─ superseded_by ── dsh-literatum-contract-v0.2   │ summarized_by
                                        │                 │
                                        └─── document_links（双向索引）──┘

library ∈ {bias, core, eco, project, runtime}
  bias     总行为约束：scope=global，常驻注入，永不归档
  core     deepmemory 主体：架构决策/接口契约，supersede 演进
  eco      deepmemory 生态：派生插件/集成点（literatum…）
  project  具体开发项目：任务看板/记忆系统等，结束即整体归档
  runtime  运行时会话事实：默认落点（现状行为，向后兼容）
topic = 库内子主题（词汇表约束），如 project 库内 "任务看板"/"记忆系统"
storage_tier ∈ {active, cold, archive}（沿用）；status ∈ {active, archived}（沿用）
  ── 语义澄清：storage_tier 管"生命周期层级"，status 管"是否可召回"
  ── 检索硬规则：可召回 = status='active' AND storage_tier='active'（新增约束）
```

---

## 4. 冻结接口

### 4.1 存储（sqlite，增量 DDL）

```sql
-- ① documents 加 library 列（默认 runtime，向后兼容既有数据）
ALTER TABLE documents ADD COLUMN library TEXT NOT NULL DEFAULT 'runtime';
CREATE INDEX IF NOT EXISTS idx_documents_library ON documents(library);
-- 校验触发器：library ∈ 词汇表；bias 库强制 scope='global' 且 importance≥0.8
--（BEFORE INSERT + BEFORE UPDATE 双份：重分类 library 不得绕过校验）
CREATE TRIGGER IF NOT EXISTS trg_documents_library_check
BEFORE INSERT ON documents
WHEN NEW.library NOT IN ('bias','core','eco','project','runtime')
     OR (NEW.library='bias' AND (NEW.scope!='global' OR NEW.importance<0.8))
BEGIN SELECT RAISE(ABORT, 'invalid library or bias constraint'); END;
CREATE TRIGGER IF NOT EXISTS trg_documents_library_check_update
BEFORE UPDATE OF library ON documents
WHEN NEW.library NOT IN ('bias','core','eco','project','runtime')
     OR (NEW.library='bias' AND (NEW.scope!='global' OR NEW.importance<0.8))
BEGIN SELECT RAISE(ABORT, 'invalid library or bias constraint on update'); END;

-- ② 文档↔记忆双向索引（新表，方案 A）
CREATE TABLE IF NOT EXISTS document_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL REFERENCES documents(id),
  doc_path TEXT NOT NULL,              -- 绝对路径（遵循绝对路径基线纪律）
  doc_kind TEXT NOT NULL DEFAULT 'note'
    CHECK(doc_kind IN ('contract','plan','proposal','code','note')),
  doc_version TEXT DEFAULT '',         -- 如 'v0.2'；空=未版本化
  relation TEXT NOT NULL
    CHECK(relation IN ('derived_from','summarized_by','superseded_by')),
  workspace_id TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_document_links_doc ON document_links(doc_path);
CREATE INDEX IF NOT EXISTS idx_document_links_mem ON document_links(memory_id);
```

relation 语义：
- `derived_from`：记忆源自该文档（读契约产生的决策/约束记忆）
- `summarized_by`：该文档被这条记忆概括（文档摘要记忆）
- `superseded_by`：**文档级版本更替**（doc_path 记新文档路径，如 contract-v0.2 取代 v0.1），对应记忆随之迁移归属

### 4.2 API（增量路由，沿用既有鉴权/404/401/403 模式）

| 方法/路径 | 行为 |
|---|---|
| `POST /v1/memories/search`（仅 POST，既有路由；**GET 同名需新写解析，勿遗漏**） | 请求体加 `library`（精确，缺省=全部）、`include_archived`（bool，缺省 false）；**默认排除 storage_tier='archive'**（修复范围：`search_memories` + `graph_search` + BM25 重建三处同改，见 §4.2a） |
| `GET /v1/memories/for-doc?path=<abs>&workspace_id=` | 文档→记忆反查：返回该 doc_path 全部 document_links 关联记忆（含 relation/doc_kind/version） |
| `GET /v1/memories/<id>` | 响应加 `doc_links`（该记忆全部 document_links） |
| `POST /v1/memories/archive` | **保持既有 status 级语义不变**（server.py:2582 现状：`{ids}` → `UPDATE status='archived'` + 移出 BM25）。契约**不重定义**此路由 |
| `POST /v1/memories/restore` | **保持既有 status 级语义不变**（server.py:2585 现状：`{id}` → status 恢复）。契约**不重定义**此路由 |
| 🔧 `POST /v1/memories/archive-library` | **新路由**（tier 级库归档）：`{library, topic?, reason, actor}`——批量置 `storage_tier='archive'` + 写 `memory_archives` 摘要；**bias 库拒绝（403）**。见 §4.2b 域层通道 |
| 🔧 `POST /v1/memories/restore-tier` | **新路由**（tier 级恢复）：`{id, reason, actor}`——archive→active。**需同步扩展域层跳转表**（见 §4.2c），否则域层抛 InvalidTransition |
| `GET /v1/memories/libraries` | 库目录：各 library 条目数/最新更新时间/topic 分布（供 kb_browse 用） |
| `POST /v1/memories/doc-link` | 建/改 document_links：`{memory_id, doc_path, doc_kind, doc_version, relation}` |
| `POST /v1/memories/add` | 请求体接受 `library`（校验词汇表 + bias 约束，不合法 400） |

### 4.2a G1 修复范围（三处同改，缺一漏档）

| 路径 | 现状 | 修复 |
|---|---|---|
| `search_memories` WHERE | 仅 `status='active'` | 加 `AND storage_tier='active'`（`include_archived=true` 时放开 tier 条件） |
| `graph_search` 两处 SQL | `WHERE status='active'`（server.py:650 与 :704 同源） | 同样加 `AND storage_tier='active'` |
| BM25 索引 | `migrate_memory` 到 archive **不移除 BM25**（仅 `archive_memories` 有 `get_bm25().remove()`） | `migrate_memory` 及 `archive-library` 落 archive 后补 `get_bm25().remove(id)`；`restore-tier` 补 `get_bm25().add(...)` |

### 4.2b 批量归档的域层通道（放弃"走 migrate 语义"）

migrate 强制单跳（active→cold→archive 两跳）且 active→cold 仅限 `memory_class ∈ (short_term, process)`——普通长时记忆进不了 archive。**契约冻结为**：`archive-library` 不调用 migrate，新增直接批量 tier 更新通道：

```sql
-- 域层新增（v2_domain 提供方法 archive_library(library, topic, reason)）：
UPDATE documents SET storage_tier='archive', memory_class='compressed_archive',
       compressed_at=?, updated_at=?
 WHERE library=? AND (topic=? OR ? IS NULL) AND storage_tier!='archive'
   AND library!='bias';
-- 之后逐条/批量写 memory_archives 摘要 + get_bm25().remove()
```

> 说明：这是**直接批量 tier 更新**，不改 migrate 的单跳语义（migrate 保持供单条/冷层使用）；域层新增独立方法而非复用 migrate，避免双重阻挡。

### 4.2c restore-tier 的域层扩展（必须二选一，本契约选此项）

`restore_memory = migrate_memory(id, "active")`，现跳转表 `{"active":{"cold"}, "cold":{"archive","active"}, "archive":set()}`——archive 是终态，archive→active 直接 InvalidTransition。**契约冻结为**：

```python
# v2_domain.migrate_memory 跳转表扩展：
valid = {"active": {"cold"}, "cold": {"archive", "active"},
         "archive": {"active"}}   # ← 新增：archive 可回 active
# 恢复分支（current=archive, target=active）：
#   storage_tier='active'；memory_class 从 'compressed_archive' 还原为
#   原 class（migrate 进 archive 前记忆的原 memory_class 存于 memory_archives
#   或按语义还原为 'semantic'——契约冻结为还原为 'semantic'，摘要保留在
#   memory_archives 供审计，不物理删除）
```

> 边界：`restore-tier` 走此扩展；既有 `POST /v1/memories/restore`（status 级）不动。两者语义明确分离：**status 级 = 会话软归档恢复，tier 级 = 生命周期层恢复**。

### 4.3 注入与 agent 工具（plugin-v3.js + agent 侧）

**注入（前缀缓存纪律：进 system 的必须会话内冻结，会变的走对话流）**
- 新增独立小节「总行为约束」（`injection.inject_group='category'` 下置于最前）：查询 `library='bias' AND scope='global'`，**每会话开始生成一次、会话内冻结**（内容低频变动，天然满足前缀稳定）
- **回退兜底（修复应修复项）**：现有"规则与偏好"组改为「优先从 bias 库取；**bias 库为空时回退按 type 分组**（保留现逻辑）」。理由：现库 9 条 preference 仅 3 条满足 bias 约束（scope=global 且 importance≥0.8），P1 上线当天若直接切源，注入段规则组肉眼可见缩水——必须双轨过渡
- 其余库（core/eco/project/runtime）不进 system，走 L3 检索按需注入（现状不变）

**agent 工具签名（冻结）**
| 工具 | 签名 | 说明 |
|---|---|---|
| `memory_search` | `{query, k?, library?, topic?, include_archived?}` | library/topic 收窄，缺省全库 |
| `kb_browse` | `{library, topic?}` | 返回库目录（条目/最新/分布），供调度决策 |
| `archive_memory` | `{ids? \| library?, topic?, reason}` | agent 主动归档（任务完成/决策废弃时） |
| `memory_recall` | 既有 + `include_archived` 参数 | 对齐 v2 目标 3（可查活跃/冷/归档） |

**会话绑定（可选，P1）**：任务/会话创建时可选绑定 library，注入与检索默认按绑定库收窄（复用任务看板"目标工作区/会话选择"交互模式）。

### 4.4 抽取（plugin-v3.js prompt 第 6c 条改造）

- 原"topic 自由概括"改为：**先选 library（从词汇表）→ 再选库内 topic（从该库 topic 集合，未命中则归 'other'）**
- 新增识别：对话中引用文档（绝对路径/契约名）时，输出 `doc_ref: {path, kind, version}`，服务端自动建 `document_links(derived_from)`
- bias 约束：抽取判为"对 agent 行为的约束/用户硬性指示"→ `library='bias'`（服务端校验兜底：scope≠global 或 importance<0.8 则拒绝入库并提示）

### 4.5 配置（config_schema.json 增量）

```json
"library": { "items": {
  "vocabulary": { "type": "list", "default": ["bias","core","eco","project","runtime"], "description": "库词汇表，服务端校验" },
  "bias_min_importance": { "type": "number", "default": 0.8 },
  "bias_protected": { "type": "boolean", "default": true, "description": "bias 库永不归档" }
}},
"archiving": { "items": {
  "retention_days": { "type": "number", "default": 365, "description": "archive tier 保留天数（超期可清理，默认不自动清理）" }
}}
```

---

## 5. 验收标准

1. **G1 修复**：`migrate_memory` 到 archive 的条目，`search` **且 `graph`（图谱节点/边）** 默认**不返回**；`include_archived=true` 返回且带 archive 标记；`restore-tier` 后重新可召回（含 BM25 重建）
2. **G2 分库**：`library='core'` 写入"架构决策"、`library='project'` 写入"任务看板"——`search(library='core')` 不含 project 记忆；`search` 不带 library 返回全部（向后兼容）
3. **G4 bias**：写入 `library='bias'` 的记忆在**新会话**注入段「总行为约束」可见；`POST /v1/memories/archive-library {library:'bias'}` 返回 403；bias 且 scope≠global 的写入返回 400；**bias 库为空时注入回退按 type 分组（双轨过渡）**
4. **G5 溯源**：对 `docs/dsh-literatum-contract.md` 建 `derived_from` 链接后，`GET /v1/memories/for-doc?path=...` 返回该记忆；`GET /v1/memories/<id>` 带 `doc_links`；`superseded_by` 链接后反查含新旧两代
5. **G3 库级归档**：`POST /v1/memories/archive-library {library:'project', topic:'任务看板'}` 批量归档 → `search`/`graph` 默认不含、`kb_browse(project)` 显示已归档计数、`restore-tier` 单条可恢复；**既有 `POST /v1/memories/archive`（status 级）行为不变**
6. **回归**：6230 health 正常；无 token 401 / 带 Origin 403 / 根路径 404 行为不变；**既有 33 个单测全绿**（源码仓库实测）+ 新增契约单测（library 校验含 UPDATE、归档过滤三路径、for-doc、restore-tier、archive-library 域层通道）

---

## 6. 实现顺序与子代理分工

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **P0（最小可交付）** | G1 检索过滤 + G2 library 列/校验/API 参数 + 词汇表配置 | 无 |
| **P1** | G4 bias 注入段 + G5 document_links/for-doc/doc-link + 抽取改造 | P0 |
| **P2** | G3 库级归档 + kb_browse/archive_memory 工具 + 会话绑定 | P1 |

分工建议（如交子代理）：SA-A 存储+DDL（G1/G2 表与双触发器 + 域层 archive_library/restore-tier 通道）、SA-B API（archive-library/restore-tier/for-doc/libraries/doc-link，**不碰既有 archive/restore 路由**）、SA-C 插件（注入段双轨/抽取/工具）、SA-D 测试与回归——**接口以 §4 为 frozen 契约**，各 SA 独立可测。

---

## 7. 风险与边界

| # | 风险 | 处置 |
|---|---|---|
| R1 | bias 注入进 system 破坏前缀缓存 | 遵循 memory-architecture-proposal §1 原则：bias 段**会话内冻结**（低频变动，会话开始生成一次）；变动经 L0 固化流程，不每轮刷新 |
| R2 | 词汇表发散 → 分库失效 | 服务端触发器 + add 校验兜底；新增库需改配置并迁移既有 topic |
| R3 | archive tier 数据膨胀 | `archiving.retention_days` 配置化保留期；默认不自动物理清理（软删可恢复优先） |
| R4 | 与 literatum 重复造轮子（文档溯源） | 本契约先落地 deepmemory 侧；literatum 的 Document 实体落地后，通过 doc_path 同源桥接，不另建映射表 |
| R5 | 既有数据迁移 | library 默认 'runtime' 向后兼容，**零迁移**；历史记忆可经 `POST /v1/memories/doc-link` 或批量 update 渐进归类 |

---

## 8. 移交信息（发给 deepmemory 维护方）

- **交付物**：本契约（§3 模型 + §4 冻结接口 + §5 验收）+ 既有代码位置索引（§1 表格）
- **代码基（实现基线，唯一事实源）**：**源码仓库** `/www/deepseek harness workspace/harness-memory-archive/memory-server/{server.py, v2_domain.py, config_schema.json}` + `agent-preset/memory-plugin/plugin-v3.js`
- **部署副本仅作参照**：`/www/deepmemory-v063-deploy/memory-server/`（生产 6230 运行副本，**不得直接修改**；实现/测试一律在源码仓库，经 B 测试机 → A 生产同步流程落地，防重演同步失效）
- **派生参照**：`dsh-literatum` 契约 v0.2（同源哲学，接口风格一致）
- **边界**：不重建 schema、不迁移既有数据、不动 6230/6240 端口与鉴权模式；**不修改既有 `/v1/memories/archive|restore` 路由语义**（新增 archive-library/restore-tier 承载 tier 语义）；实现顺序 P0→P1→P2，P0 验收过即可先行合入
