# dsh-literatum —— 文献 · 证据 · 知识管理插件（开发契约 + 方案）

> 版本：contract-v0.2 · 2026-08-31 · 移交开发（冻结接口）
> 定位：**独立插件**（不并入 dsh-deepmemory）。单页文档，可直接移交另开团队/子代理开发。
> 变更记录：v0.2 依据审核结论（14 处修订）+ 复核结论（3 缺口 + 3 小问题）全量冻结；配套执行细节见 `dsh-literatum-dev-plan.md`。

---

## 0. 一句话

给 DeepSeek Harness 一个「文献管理器 + 证据库 + 书本知识库」三合一插件：
管理论文/PDF/引用，维护「主张—证据—论文」的论证链，把书本阅读沉淀成可检索的知识网络。

> 核心洞察：三件事共享一个元模型——**文献（Documents）→ 证据（Evidence）→ 知识（Knowledge）** 是逐层派生的（读书/读论文 → 提取证据 → 形成知识）。

---

## 1. 目标 / 非目标

### 1.1 目标
| 模块 | 核心能力 |
|---|---|
| **A. 文献管理器** | 文献元数据（题录）+ PDF/附件 + 引用（citation/bibtex）+ 标签/收藏/检索 |
| **B. 证据库** | 证据（claim/evidence 三元组）+ 每条证据**可溯源到具体文献/章节**；多来源论文对同一主张的支持/反驳图谱 |
| **C. 知识库** | 书本管理 + 阅读笔记 + 概念/知识条目 + **知识网络**（概念→关联→来源文献） |

### 1.2 非目标（明确排除）
- 不做 PDF 渲染/批注编辑器（管附件，不改文档）
- 不做文献下载的版权合规（只提供链接/DOI 管理，不代下载）
- 不做自动 AIGC 论文写作（只做材料组织）
- 不与 dsh-deepmemory 记忆混存（**独立存储**；MVP 不读取 deepmemory 会话记忆，P2 以「可选 adapter + 对方授权 token」再议）

---

## 2. 领域模型（核心元模型）

```
Document（文献/书本，统一实体）
 ├─ type: paper | book | report | web
 ├─ metadata: title/authors/year/journal/DOI/ISBN/url
 ├─ attachment: PDF/EPUB 路径（可选）+ sha256
 ├─ read_status: unread | reading | intensive | read（阅读状态）
 ├─ lifecycle_status: active | archived（生命周期，默认 active）
 └─ sections: 章节/页面锚点（供证据溯源）

Evidence（证据，从文献可定位处提取）
 ├─ claim: 主张（一句话）
 ├─ evidence_text: 原文摘录（**带文献+章节定位**）
 ├─ stance: supporting | contradicting | contextual
 ├─ mapped_document → Document
 └─ confidence / note

KnowledgeItem（知识条目，书本/多文献形成）
 ├─ concept: 概念名（如"认知负荷"）
 ├─ summary: 一段话理解
 ├─ relations: [KnowledgeItem.id → KnowledgeItem.id, 关系词]（id 对，知识网络边）
 ├─ sources: [Evidence.id...]（溯源）
 └─ notes: 阅读笔记（富文本/纯文本 + 时间线）
```

**派生链**：`Document →(提取)→ Evidence →(归纳)→ KnowledgeItem →(关联)→ 概念网络`

**软删**：全部实体支持软删（`deleted_at`），删除有救济；查询默认过滤 `deleted_at IS NULL`。

---

## 3. 功能需求（分级）

### P0（MVP，先交付）
- [ ] 文献 CRUD + 题录字段；导入 **BibTeX / Zotero 导出 JSON / DOI 元数据**
- [ ] 附件（PDF）存储与打开跳转（上传/取回两端点）
- [ ] 证据 CRUD：claim/evidence_text/stance + **溯源**（关联文献章节）
- [ ] 书本 / KnowledgeItem CRUD + 笔记
- [ ] 文献精确去重（DOI/ISBN/标题归一化）——**P0**（原 P1 降级）
- [ ] 基础检索（标题/作者/标签/关键词；SQL FTS）
- [ ] DSH 集成：`tools`（literatum_add/search/attach/link_evidence）+ 记忆面板并列的「文献/知识」tab

### P1（增强）
- [ ] 引用格式导出（BibTeX/APA/MLA）
- [ ] 证据→论文反查（同一主张的所有支持/反驳文献图谱）
- [ ] 知识网络可视化（概念图，类似 deepmemory 图谱）
- [ ] 文献模糊匹配（标题 similarity）
- [ ] 阅读进度同步（read_status 已入库，UI 展示进度条）

### P2（进阶）
- [ ] LLM 辅助：从 PDF/章节**自动提取证据候选**（用户确认后入库）
- [ ] LLM 辅助：多文献 **综述成 KnowledgeItem**（带溯源）
- [ ] 导入注记（PDF 高亮/批注导入证据）
- [ ] 多人协作/共享文献库（可选，per-workspace）

---

## 4. 接口契约（SA 间 frozen 接口）

### 4.1 存储（独立 sqlite，不占 deepmemory 库）
```
<部署基线>/literatum-server/data/literatum.db     （绝对路径基线见 dsh-literatum-dev-plan §0）
  documents(id, type, title, authors_json, year, journal, doi, isbn, url,
            attachment_path, attachment_sha256, tags_json,
            read_status, lifecycle_status, deleted_at,
            created_at, updated_at, workspace_id)
  evidence(id, claim, stance, evidence_text, doc_id, chapter_anchor,
            page, confidence, note, deleted_at, created_at, updated_at, workspace_id)
  knowledge_items(id, concept, summary, notes, deleted_at, created_at, updated_at, workspace_id)
  knowledge_relations(source_id, target_id, relation, workspace_id, deleted_at, updated_at)
            -- 概念网络边：source/target 引用 knowledge_items.id（id 对口径）
  evidence_source(evidence_id, knowledge_item_id, workspace_id, deleted_at)
            -- 证据↔知识多对多（溯源）；workspace 硬过滤经两端推导
  (FTS: documents_title_fts / evidence_fts / knowledge_fts)
```

### 4.2 API（独立服务，默认端口 6260，`/lit-api` 前缀给 web 插件代理）
| 方法/路径 | 行为 |
|---|---|
| `POST /v1/literatum/documents` | 新建文献（题录/附件路径/tags/read_status） |
| `GET /v1/literatum/documents?q=&read_status=&tags=&workspace_id=` | 检索（FTS + 过滤，workspace 硬过滤） |
| `GET /v1/literatum/documents/<id>` | 详情（含 evidence 反查） |
| `PATCH /v1/literatum/documents/<id>` | 部分更新（题录/read_status/进度） |
| `DELETE /v1/literatum/documents/<id>` | 软删（置 deleted_at） |
| `POST /v1/literatum/evidence` | 建证据（claim/stance/溯源 doc+anchor） |
| `GET /v1/literatum/evidence/<id>` | 单条证据详情 |
| `PATCH /v1/literatum/evidence/<id>` | 更新证据 |
| `DELETE /v1/literatum/evidence/<id>` | 软删证据 |
| `GET /v1/literatum/claims?q=&workspace_id=` | 同一主张全部证据（support/contradict 聚合；主张走查询参数） |
| `POST /v1/literatum/knowledge` | 建知识条目（concept/summary/relations/sources） |
| `GET /v1/literatum/knowledge/<id>` | 单条知识详情 |
| `PATCH /v1/literatum/knowledge/<id>` | 更新知识条目（含 relations/sources 差量） |
| `DELETE /v1/literatum/knowledge/<id>` | 软删知识条目 |
| `GET /v1/literatum/graph?workspace_id=` | 概念网络（nodes/edges，workspace 硬过滤，join concept 出标签） |
| `POST /v1/literatum/import/bibtex` | BibTeX/Zotero JSON 批量导入（含去重：DOI/ISBN 命中默认跳过） |
| `POST /v1/literatum/import/doi` | DOI 元数据导入（fetch Crossref/OpenAlex） |
| `POST /v1/literatum/dedupe/check` | DOI/ISBN/标题精确去重检查（入参候选 → 命中列表） |
| `GET /v1/literatum/export/bibtex?ids=` | 引用导出 |
| `POST /v1/literatum/attachments` | **附件上传**（multipart；落盘 + 登记 attachment_sha256） |
| `GET /v1/literatum/attachments/<file>?t=<signed>` | **附件取回**（短期签名 token，浏览器可直接打开） |

### 4.3 鉴权（完全沿用 deepmemory 模式，冻结）
- 服务端：`data/api-token`（`secrets.token_urlsafe(32)`，首次启动自动创建）；校验 `Authorization: Bearer <token>`，缺失/不匹配 401；带 `Origin` 头 403；根路径 404
- Host 插件侧：token 从 `LITERATUM_API_TOKEN_FILE` env / `$DSH_HOME/.dsh-literatum-api-token` 读取，代理请求附加 Bearer（同 deepmemory index.js `readToken()` 模式）
- **workspace 传递**：所有读写请求带 `workspace_id`（body 或 query）；服务端强制硬过滤，绝不依赖软排序兜底
- **附件签名（冻结）**：共享 secret `data/attachment-signing-key`（与 api-token 同级，启动自动生成）；token = `<expires_epoch>.<HMAC-SHA256(signing-key, f"{file_path}.{expires_epoch}")>`；服务端校验未过期（≤5 分钟）+ 签名匹配 + 无 `..` 越界

### 4.4 DSH 插件侧
- **Host 插件**（`/lit-api` prefix 代理 → 6260）类比 dsh-livetaskboard 的 index.js；token/签名 key 同路径读取
- **Client**：侧栏/记忆面板相邻入口「文献库」浮层或 conversation.view tab；渲染 documents/evidence/知识网络
- **Agent 工具（4 个，冻结）**：`literatum_add`（加文献）/`literatum_search`（查证据/文献）/`literatum_attach`（挂附件）/`literatum_link_evidence`（对当前讨论挂证据）；**载体文件** `agent-preset/literatum-plugin/plugin-v1.js`
- **workspace 隔离**：`workspace_id` 硬过滤（同 deepmemory 模式）

---

## 5. 架构（独立插件）

```
DSH Web (client.js) ──/lit-api──> Host (index.js, prefix 代理, Bearer token)
                                       │
                 literatum_server.py (6260, sqlite + FTS + 附件落盘 + 鉴权)
                                       │
                  literatum_domain.py（域模型 + CRUD + 图谱 + 导入导出 + 去重）
```

- **复用**：dsh-livetaskboard 的插件骨架（`git clone http://127.0.0.1:3000/LazyFish/dsh-livetaskboard.git`，board_server 模式）；deepmemory 的图谱/检索思想（可借用 RRF 排序，但**独立库表**）
- **派生参照实物**：`/www/deepmemory-v063-deploy/memory-server/server.py`（鉴权/路由模式）；`/www/deepseek harness workspace/harness-memory-archive/web-plugin/`（代理模式）
- **不强耦合 deepmemory**：仅 workspace_id 语义一致，便于共存；不读 deepmemory 库、不需要 deepmemory token

---

## 6. 验收标准（MVP）

1. 新建 3 篇文献（含 1 篇带 PDF 附件，走 `POST /attachments` 上传）→ 检索标题/作者命中；PATCH 改题录生效；DELETE 软删后列表过滤
2. 添加 2 条证据（1 支持 + 1 反驳，各溯源到不同文献）→ `GET /claims?q=` 返回聚合（support/contradict 各 1）
3. 新建 2 个 KnowledgeItem（"认知负荷"、"工作记忆"）+ 1 条 relation → `GET /graph` 渲染 **1 概念边**（id 对口径）；workspace B 查 graph 为空（跨区隔离验证）
4. 先建 1 篇文献（DOI=D1）→ BibTeX 导入 5 条（其中 1 条与 D1 同 DOI）→ **去重提示 1 条（跳过不覆盖）** → 导出 5 条（4 新增 + 1 既有）
5. agent 工具 `literatum_search('认知负荷')` 返回 ≥1 ≤5 条（带来源）
6. 生产/测试机双装（绝对路径基线见 dsh-literatum-dev-plan §0），**全端口复核 6230/6240/6260/6261 互不冲突**；无 token 401、带 Origin 403、根路径 404

---

## 7. 开发建议（多子代理并行）

| 子代理 | 范围 | 依赖 |
|---|---|---|
| **SA-0 仓库+契约** | 仓库迁移 + 契约修订 contract-v0.2（本文档） | 无（唯一串行） |
| **SA-1 领域 + 存储** | `literatum_domain.py`（模型/CRUD/软删/导入导出/去重/附件登记） | SA-0 契约 |
| **SA-2 API 服务** | `literatum_server.py`（路由 → domain；鉴权 + 附件上传取回） | SA-1 |
| **SA-3 Host+Client 插件** | `/lit-api` 代理 + UI（文献/证据/知识/图谱）+ agent 工具载体 | SA-2 |
| **SA-4 图谱/检索增强** | 概念网络 + 去重（DOI/ISBN 精确） | SA-1 并行 |

> 多代理分工原则：各 SA 独立交付可测产物；**契约即接口**（4.1/4.2/4.3 为 SA 间 frozen 接口）；先 SA-1/SA-4 并行，SA-2/SA-3 随后。生产/测试机同步遵守「先测试机（B）→ 生产（A）」顺序（沿用 deepmemory 计划规范）；**B0 前置 = SA-1/SA-2 产物已 commit 入仓**。

---

## 8. 移交信息（发给开发方）

- **交付物**：本契约（§2 领域模型 + §4 接口 + §6 验收）+ 开发方案（`dsh-literatum-dev-plan.md`，含环境基线/执行计划/风险表）
- **技术栈**：Python HTTP 服务（可仿 `/www/deepmemory-v063-deploy/memory-server/server.py` 模式）+ Node bundle（仿 dsh-livetaskboard web-plugin）
- **绝对路径基线**：见 `dsh-literatum-dev-plan.md §0`（生产 6260 / 测试机 6261；生产 6230/测试机 6240 为 deepmemory，互不冲突）
- **仓库**：`http://127.0.0.1:3000/LazyFish/dsh-literatum.git`（去 token URL）
- **里程碑**：M1 MVP（P0 全过验收）→ M2 P1 → M3 P2（LLM 辅助）
