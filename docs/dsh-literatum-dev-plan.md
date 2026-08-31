# dsh-literatum 开发方案（deepmemory 派生插件 · 审核修订版）

> 版本：plan-v0.2 · 2026-08-31 · 依据：契约审核结论（14 处问题）+ 复核结论（3 缺口 + 3 小问题）+ 记忆库 key `dmk.11660edec5`（56 条原始上下文）
> 定位：literatum 是 deepmemory 的派生插件——**复用其部署/鉴权/代理/依赖范式，但存储与 API 完全独立**（契约 §0：独立插件、独立 sqlite、不并入 dsh-deepmemory）。

---

## 0. 环境基线（绝对路径，唯一事实源，沿用 deepmemory 计划 §0）

| 角色 | 绝对路径 | 服务 | 端口 | 备注 |
|---|---|---|---|---|
| **literatum 源码仓库** | `/www/deepseek harness workspace/dsh-literatum` | — | — | **已就位**（2026-08-31 迁移完成，commit `e3351f3` 历史保留） |
| **生产 literatum server** | `/www/dsh-literatum-deploy/literatum-server` | `dsh-literatum.service` | 6260 | 库 `data/literatum.db`（独立） |
| **测试机 literatum server** | `/www/dsh-test-literatum` | `dsh-test-literatum.service` | 6261 | 独立库（新，0 条）；代码复制自源码 |
| **生产 core（DSH 引擎）** | `/opt/dsh-rc2-core` | `dsh-web.service` | 3081 | 宿主插件安装处 |
| **生产 web home** | `/www/dsh/home` | — | — | `.agent-presets/_literatum-plugin/` + `profiles/web/node_modules/dsh-literatum/` |
| **测试机 core** | `/opt/dsh-rc2-test-core` | `dsh-test.service` | 3091 | 测试机宿主 |
| **测试机 home** | `/www/dsh-test-home` | — | — | 插件安装处（当前为空，B0 需建） |
| **源码仓库（deepmemory，被仿照）** | `/www/deepseek harness workspace/harness-memory-archive` | — | — | 契约/方案存放处；server.py 模式来源 |
| **生产 memory-server** | `/www/deepmemory-v063-deploy/memory-server` | `dsh-memory-server.service` | 6230 | 仅作派生参照，不耦合 |
| **测试机 memory-server** | `/www/dsh-test-memory` | `dsh-test-memory.service` | 6240 | 仅作派生参照 |
| **Gitea（本机）** | `http://127.0.0.1:3000` | docker | 3000 | 仓库 `LazyFish/dsh-literatum`（id=12）、骨架 `LazyFish/dsh-livetaskboard`（id=11） |
| **node 运行时** | `/usr/local/node`（v22.23.2） | — | — | pnpm 软链 `/usr/local/node/bin/pnpm` |

**抄送路径（勿用，仅留痕）**：`/www/deepseek hardness workspace`（差一个 s）——**一律以本表为准**。

---

## 1. 前置修复（审核高危 #1，#1 必做；其余为契约修订，交付开发前置）

### 1.1 仓库迁移：勿用路径 → 正确路径（立即执行）

现状（已核实）：`dsh-literatum` 建在拼错路径 `/www/deepseek hardness workspace/dsh-literatum`（19:38 创建，仅 1 个 commit `e3351f3`，是契约纯 docs）。deepmemory 计划 §0 与记忆 258 明确该路径"勿用，仅留痕"。

```bash
# 1) 在正确路径原地建仓（保留 commit 历史）
git clone "http://127.0.0.1:3000/LazyFish/dsh-literatum.git" \
  "/www/deepseek harness workspace/dsh-literatum"
# 2) 校验两份副本与 Gitea 均一致
cmp "/www/deepseek hardness workspace/dsh-literatum/docs/dsh-literatum-contract.md" \
    "/www/deepseek harness workspace/dsh-literatum/docs/dsh-literatum-contract.md"
# 3) 校验通过后，移除勿用路径副本（勿用路径不留活物）
rm -rf "/www/deepseek hardness workspace/dsh-literatum"
# 4) 勿用父目录 /www/deepseek hardness workspace 是随仓库一起被创建的（19:38，复核确认），
#    删掉子目录后若为空则一并移除；"勿用留痕"由 §0 本表登记承担，无需保留空目录
rmdir "/www/deepseek hardness workspace" 2>/dev/null || echo "父目录非空，保留占位"
```

> remote 是 URL（`http://LazyFish:<token>@127.0.0.1:3000/...`），**不受本地路径影响**，clone 后 remote 自动指向 Gitea。
> ⚠️ **凭据纪律**：remote URL 内嵌 Gitea token，任何文档/移交材料不得原样传播该 URL（审核小问题 5）。

### 1.2 契约修订为 contract-v0.2（修复审核高危 #2~#5 + 应修复 + 小问题）

修订后的契约 §4 冻结接口（SA 间唯一依据），变更点用 🔧 标注：

**§4.1 存储（独立 sqlite）：**
- documents/evidence/knowledge_items 表：保留 workspace_id（硬过滤，同 deepmemory 模式，记忆 240）
- 🔧 **knowledge_relations 补 `workspace_id` 列**（修复高危 #4：图谱按 workspace 硬过滤，跨区边绝不漏进图）
- 🔧 **evidence_source 补 `workspace_id` 列**（同上；join 时按两端 workspace 推导）
- 🔧 全表补 `deleted_at`（软删列，修复小问题 2：误删有救济；查询默认过滤 `deleted_at IS NULL`）
- 🔧 `documents.status` 拆语义：`read_status`（未读/在读/精读/已读，P1）+ `lifecycle_status`（active/archived，默认 active）
- 🔧 增加 `documents.attachment_sha256`（附件内容指纹，供去重 & 附件校验）

**§4.2 API（独立服务，默认端口 6260，`/lit-api` 前缀给 web 插件代理）：**

| 方法/路径 | 行为 | 变更 |
|---|---|---|
| `POST /v1/literatum/documents` | 新建文献（题录/附件路径/tags） | — |
| `GET /v1/literatum/documents?q=&status=&tags=&workspace_id=` | 检索（FTS + 过滤，workspace 硬过滤） | 🔧 补 workspace 参数 |
| `GET /v1/literatum/documents/<id>` | 详情（含 evidence 反查） | — |
| 🔧 `PATCH /v1/literatum/documents/<id>` | 部分更新（题录/status/阅读进度） | **新增**（修复高危 #2 缺 U） |
| 🔧 `DELETE /v1/literatum/documents/<id>` | 软删（置 deleted_at） | **新增**（修复高危 #2 缺 D） |
| `POST /v1/literatum/evidence` | 建证据（claim/stance/溯源 doc+anchor） | — |
| `GET /v1/literatum/evidence/<id>` | 单条证据详情 | 🔧 新增（补齐按 id 查） |
| 🔧 `PATCH /v1/literatum/evidence/<id>` | 更新证据 | **新增** |
| 🔧 `DELETE /v1/literatum/evidence/<id>` | 软删证据 | **新增** |
| 🔧 `GET /v1/literatum/claims?q=` | 同一主张全部证据（support/contradict 聚合） | **改**：主张放查询参数（修复应修复 2：中文/空格/斜杠不再进 path） |
| `POST /v1/literatum/knowledge` | 建知识条目（concept/summary/relations/sources） | — |
| `GET /v1/literatum/knowledge/<id>` | 单条知识详情 | 🔧 新增 |
| 🔧 `PATCH /v1/literatum/knowledge/<id>` | 更新知识条目（含 relations/sources 差量） | **新增** |
| 🔧 `DELETE /v1/literatum/knowledge/<id>` | 软删知识条目 | **新增** |
| `GET /v1/literatum/graph?workspace_id=` | 概念网络（nodes/edges，按 workspace 硬过滤） | 🔧 补 workspace 参数 |
| `POST /v1/literatum/import/bibtex` | BibTeX/Zotero JSON 批量导入 | — |
| `GET /v1/literatum/export/bibtex?ids=` | 引用导出 | — |
| 🔧 `POST /v1/literatum/attachments` | **附件上传**（multipart；落盘 + 登记 attachment_sha256） | **新增**（修复应修复 1：PDF 如何进服务器） |
| 🔧 `GET /v1/literatum/attachments/<file>` | **附件取回**（浏览器可直接打开/下载） | **新增**（修复应修复 1：前端如何打开） |
| 🔧 `POST /v1/literatum/import/doi` | DOI 元数据导入（fetch Crossref/OpenAlex） | **新增**（补 P0 DOI 导入的实现通道） |
| 🔧 `POST /v1/literatum/dedupe/check` | DOI/ISBN/标题精确去重检查（入参候选，返回命中列表） | **新增**（修复应修复 3：去重降 P0，验收 #4 有据） |

**§4.3 鉴权（修复高危 #5 —— 完全沿用 deepmemory 模式，冻结）：**
- 服务端：`data/api-token` 文件（`secrets.token_urlsafe(32)` 生成，首次启动自动创建）；`_reject_browser_origin()` 校验 `Origin` 头 + `Authorization: Bearer <token>`，缺失/不匹配返回 401；根路径 404（同 deepmemory 6230 行为）
- Host 插件侧：token 从 `MEMORY_API_TOKEN_FILE` env / `$DSH_HOME/.dsh-literatum-api-token` 读取，代理请求附加 `Authorization: Bearer`（同 deepmemory index.js `readToken()` 模式）
- **workspace 传递约定**：所有读写请求带 `workspace_id`（body 或 query）；服务端强制硬过滤，绝不依赖软排序兜底（记忆 240）
- 附件取回：**冻结签名机制**（修复复核缺口 2）——
  - **共享 secret**：`data/attachment-signing-key` 文件（与 `api-token` 同级，服务端首次启动自动生成 `secrets.token_urlsafe(32)`）；Host 与 Python 服务端**从同一路径读取**，无需网络协商
  - **Token 格式**：`<expires_epoch>.<hmac_hex>`，其中 `hmac_hex = HMAC-SHA256(key=signing-key, message=f"{file_path}.{expires_epoch}")`
  - **校验**：服务端验「未过期（≤5 分钟）」+「签名匹配」+「file_path 无 `..` 越界」；Host 代理生成 token 后附加 `?t=`，前端直接可打开；**杜绝裸 URL 直接暴露文件**
  - 实现示例（SA-3 按此）：Host 读取 signing-key → `hmac.new(key, f"{path}.{exp}.encode(), hashlib.sha256).hexdigest()`

**§4.4 依赖链（修复"可读取 deepmemory 会话记忆"张力，记忆 30）：**
- 冻结为「**MVP 不做**」。deepmemory 会话记忆读取机制未定义（走 6230/6240 API 需要对方 token，耦合）——契约 §1.2 非目标保留"独立存储"，删除"可读取 deepmemory 注入的会话记忆"，改在 P2 以「可选 adapter + 对方授权 token」再议（修复小问题 3）

**§4.5 工具命名 + 载体文件（修复小问题 1 + 复核缺口 1）：**
- 冻结为 4 个 agent 工具：`literatum_add`、`literatum_search`、`literatum_attach`（挂附件）、`literatum_link_evidence`（对当前讨论挂证据）。**删除契约 line 119 的 `literatum_attach_evidence`**，统一为 `literatum_link_evidence`
- **载体文件（冻结）**：`agent-preset/literatum-plugin/plugin-v1.js`（源码仓库内，承载 4 个 agent 工具注册，仿 deepmemory `.agent-presets/_memory-plugin/plugin-v3.js` 先例）
  - B0 拷贝：`/www/deepseek harness workspace/dsh-literatum/agent-preset/literatum-plugin/plugin-v1.js` → `/www/dsh-test-home/.agent-presets/_literatum-plugin/plugin-v1.js`
  - A3 拷贝：同上 → `/www/dsh/home/.agent-presets/_literatum-plugin/plugin-v1.js`
  - 职责分离：agent 工具走 `.agent-presets/`，UI 走 `profiles/web/node_modules/`

### 1.3 契约其他修订（对应审核"应修复""小问题"）

| 审核项 | 修订 |
|---|---|
| 应修复 4：见附录空引用 | ✅ 本方案 §0 即绝对路径基线；契约 §6.6 改为「绝对路径基线见 dsh-literatum-dev-plan §0」并补全生产/测试机目录、systemd 服务名、DB 绝对路径（删 `/…/literatum/` 占位符） |
| 应修复 3：验收测 P1 去重 | ✅ 去重（DOI/ISBN 精确）降 P0；验收 #4 改为「**先建 1 篇文献（DOI=D1）→ BibTeX 导入 5 条（其中 1 条 DOI=D1 与既有重复）→ 去重提示 1 条** → 命中语义冻结为 **跳过（不覆盖）** → 导入后导出 5 条（4 新增 + 1 既有）」 |
| 高危 #3：关系边三口径 | ✅ **冻结为 id 对口径**：`knowledge_relations(source_id, target_id, relation, workspace_id)`，其中 source/target 引用 `knowledge_items.id`；**1 个 KnowledgeItem 自身不算边，需显式创建 ≥2 个条目 + 1 条 relation 才有 1 条边**。验收 #3 改为：新建 2 个 KnowledgeItem（"认知负荷" + "工作记忆"）+ 1 条边 → 图谱渲染 1 概念边；加"跨 workspace 图谱隔离"验证 |
| 小问题 4：livetaskboard 无实物 | 契约 §5 给出 clone 地址：`git clone http://127.0.0.1:3000/LazyFish/dsh-livetaskboard.git`（已验证存在，HEAD a7fd5eb，v0.1.1 tag） |
| 小问题 5：remote 内嵌凭据 | 契约/方案文档一律写 `http://127.0.0.1:3000/LazyFish/dsh-literatum.git`（去 token） |

---

## 2. 技术架构（派生自 deepmemory）

```
DSH Web (client.js) ──/lit-api──> Host (index.js, prefix 代理, Bearer token)
                                       │
                literatum_server.py (6260, sqlite + FTS + 附件落盘 + 鉴权)
                                       │
                 literatum_domain.py（域模型 + CRUD + 图谱 + 导入导出 + 去重）
```

- **复用（已验证实物）**：
  - 后端：`/www/deepmemory-v063-deploy/memory-server/server.py` 的 `BaseHTTPRequestHandler + ThreadingHTTPServer`、`api-token` 鉴权 `_reject_browser_origin`、`_send/_read_body`、workspace 硬过滤模式
  - 前端：`/www/deepseek harness workspace/harness-memory-archive/web-plugin/` 的 index.js（`/mem-api` 代理 + `readToken()`）+ client.js（记忆面板 tab）
  - 骨架：Gitea `LazyFish/dsh-livetaskboard`（`board_server` 模式）
- **不强耦合 deepmemory**：仅 workspace_id 语义一致；**不读 deepmemory 库、不需要 deepmemory token**

### 依赖（冻结）
- Python：`sqlite3`（标准库）+ `http.server`（标准库）+ `jieba`（FTS 分词，deepmemory 已用）；无第三方 HTTP 框架
- Node：`@deepseek-ai/schemastery`、`@deepseek-ai/dsh-settings`（同 deepmemory peerDependencies）
- 附件存储：`<BASE_DIR>/data/attachments/`（server 同目录，便于 systemd 管理）

---

## 3. 分阶段执行计划（B 测试机先行 → A 生产后行 → C 增强）

> 硬性前置：**B 阶段全部通过后才可执行 A**（沿用 deepmemory 计划 L0 规则，记忆 153/257）。
> 阶段语义：B=测试机验证（先行），A=生产同步（后行），C=P1/P2 增强（可选）。
> **B0 前置（复核缺口修正）**：B0 要拷贝的 `literatum-server/` 源码与 `agent-preset/literatum-plugin/plugin-v1.js` 需 **SA-1/SA-2 交付入仓并 commit** 后才存在；S0 只做迁移 + 契约修订。故执行顺序为 **S0（本文档）→ SA-0~SA-4 开发 → B0**。

### 阶段 S0：仓库就位（1 次，已完成前置 1.1 后）
- [ ] S0.1 迁移仓库到 `/www/deepseek harness workspace/dsh-literatum`（§1.1）
- [ ] S0.2 契约修订为 contract-v0.2（§1.2~1.3），commit `contract-v0.2` 并推送 Gitea

### 阶段 B：测试机（硬性前置，先行）
- [ ] B0 建目录+装插件（测试机 home 为空，必须补装；**前置 = SA-1/SA-2 产物已 commit 入仓**）：
  ```bash
  mkdir -p /www/dsh-test-literatum
  mkdir -p /www/dsh-test-home/.agent-presets/_literatum-plugin
  mkdir -p /www/dsh-test-home/profiles/web/node_modules/dsh-literatum
  # 复制 server 源码
  cp -r "/www/deepseek harness workspace/dsh-literatum/literatum-server/"{literatum_domain.py,literatum_server.py,config_schema.json} /www/dsh-test-literatum/
  # 复制 agent 工具载体（复核缺口 1 补齐）
  mkdir -p "/www/deepseek harness workspace/dsh-literatum/agent-preset/literatum-plugin"
  cp "/www/deepseek harness workspace/dsh-literatum/agent-preset/literatum-plugin/plugin-v1.js" \
     /www/dsh-test-home/.agent-presets/_literatum-plugin/plugin-v1.js
  # 复制 web 插件
  cp "/www/deepseek harness workspace/dsh-literatum/web-plugin/"{client.js,index.js,package.json,dsh.patch.yml} /www/dsh-test-home/profiles/web/node_modules/dsh-literatum/
  # 转换 client（沿用 fix-client-bundle.py）
  /opt/AstrBot/venv/bin/python3 "/www/deepseek harness workspace/harness-memory-archive/scripts/fix-client-bundle.py" \
    /www/dsh-test-home/profiles/web/node_modules/dsh-literatum/client.js dsh-literatum
  ```
- [ ] B1 建 systemd：`/etc/systemd/system/dsh-test-literatum.service`（ExecStart=`/opt/AstrBot/venv/bin/python3 /www/dsh-test-literatum/literatum_server.py`，Environment=`LITERATUM_SERVER_PORT=6261`，Environment=`HOME=/root`，WorkingDirectory=`/www/dsh-test-literatum`）
- [ ] B2 `systemctl daemon-reload && systemctl restart dsh-test-literatum.service dsh-test.service`
- [ ] B3 验证（测试机 6261 + 3091 浏览器）：
  - `TOKEN=$(cat /www/dsh-test-literatum/data/api-token); curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:6261/v1/health` → ok
  - 无 token → 401；带浏览器 Origin → 403；根路径 → 404
  - 建 3 文献（含 1 附件）→ 检索命中；2 证据（1 支持 1 反驳）→ claims 聚合 support/contradict 各 1；2 知识条目 + 1 边 → graph 渲染 1 边；BibTeX 导入 5 条 → 去重提示 → 导出 5 条；PATCH/DELETE 生效且软删后查询过滤
  - **跨 workspace 隔离**：workspace A 建条目不出现于 workspace B 的 graph/检索

### 阶段 A：生产同步（B 全通过后）
- [ ] A1 建目录 `/www/dsh-literatum-deploy/literatum-server` + 复制源码
- [ ] A2 建 systemd `dsh-literatum.service`（ExecStart 同 B1 但 `LITERATUM_SERVER_PORT=6260`，WorkingDirectory=`/www/dsh-literatum-deploy/literatum-server`）
- [ ] A3 同步生产插件（含 agent 载体 + web 插件）：`cp` 源码 `agent-preset/literatum-plugin/plugin-v1.js` → `/www/dsh/home/.agent-presets/_literatum-plugin/plugin-v1.js`；`web-plugin/{client.js,index.js,package.json,dsh.patch.yml}` → `/www/dsh/home/profiles/web/node_modules/dsh-literatum/`（转换 client）
- [ ] A4 `systemctl daemon-reload && systemctl restart dsh-literatum.service dsh-web.service`
- [ ] A5 验证：6260 health/401/403/404 + 浏览器 UI 全流程 + **全端口复核 6230/6240/6260/6261 互不冲突**（统一口径，修复复核小问题）

### 阶段 C：P1/P2 增强（后续，本轮不做）
- [ ] C1 阅读状态同步（read_status 已入库）+ 进度条
- [ ] C2 证据→论文反查图谱（claims 聚合增强）
- [ ] C3 知识网络可视化增强（图谱交互）
- [ ] C4 LLM 辅助：PDF 自动提取证据候选 / 多文献综述成 KnowledgeItem（P2，需另行评审）

---

## 4. 多子代理分工（对齐契约 §7，接口冻结后并行）

| 子代理 | 范围 | 依赖 | 说明 |
|---|---|---|---|
| **SA-0**（先做） | 仓库迁移 + 契约修订 contract-v0.2（§1.1~1.3） | 无 | 唯一串行步骤；产出冻结接口供 SA-1~4 读取 |
| **SA-1 领域 + 存储** | `literatum_domain.py`（模型/CRUD/软删/图谱/导入导出/去重/附件登记） | SA-0 契约 | 与 SA-4 并行 |
| **SA-2 API 服务** | `literatum_server.py`（路由 → domain；鉴权 + 附件上传取回） | SA-1 | 契约 §4.2 全量路由 + §4.3 鉴权 |
| **SA-3 Host+Client 插件** | `/lit-api` 代理 + UI（文献/证据/知识/图谱）+ agent 工具 | SA-2 | 骨架 dsh-livetaskboard |
| **SA-4 图谱/检索增强** | 概念网络（workspace 硬过滤）+ 去重（DOI/ISBN 精确） | SA-0 契约 | 与 SA-1 并行 |

> 依赖链：SA-0 → {SA-1, SA-4} → SA-2 → SA-3。每个 SA 交付可测产物；契约 §4.1/4.2/4.3 为 frozen 接口。

---

## 5. 验收标准（MVP，对应契约 §6 修订版）

1. 新建 3 篇文献（含 1 篇带 PDF 附件，走 `POST /attachments` 上传）→ 检索标题/作者命中；PATCH 改题录生效；DELETE 软删后列表过滤
2. 添加 2 条证据（1 支持 + 1 反驳，各溯源到不同文献）→ `GET /claims?q=` 返回聚合（support/contradict 各 1）；PATCH/DELETE 生效
3. 新建 2 个 KnowledgeItem（"认知负荷"、"工作记忆"）+ 1 条 relation → `GET /graph` 渲染 1 概念边（id 对口径）；workspace B 查 graph 为空（隔离验证）
4. 先建 1 篇文献（DOI=D1）→ BibTeX 导入 5 条（其中 1 条与 D1 同 DOI）→ **去重提示 1 条重复（跳过不覆盖）** → 导出 5 条（4 新增 + 1 既有）
5. agent 工具 `literatum_search('认知负荷')` 返回 ≥1 ≤5 条（带来源）
6. 生产/测试机双装（绝对路径基线见本方案 §0），**全端口复核 6230/6240/6260/6261 互不冲突**；无 token 401、带 Origin 403、根路径 404

---

## 6. 风险与开放问题

| # | 风险/问题 | 处置 |
|---|---|---|
| R1 | relations id 对口径下，图谱"概念名对"的展示语义变弱（边无概念名只显示 id） | 契约冻结 id 对 + 图谱渲染时 join knowledge_items.concept 显示标签；概念名对作为 P2 增强 |
| R2 | 附件取回 token（`?t=`）实现复杂度 | ✅ **已冻结**（复核缺口 2）：共享 secret `data/attachment-signing-key` + HMAC-SHA256(file, expires)，Host 与服务端同路径读取；超时/越界校验在服务端；若仍出问题降级为同源代理 + 随机文件名 |
| R3 | deepmemory 会话记忆读取搁置 | 明确 MVP 不做，避免耦合（契约 §1.2 已删该句）；P2 再议 |
| R4 | dedupe 降 P0 增加 SA-1 工作量 | 精确去重（DOI/ISBN 唯一索引 + 标题归一化）成本低，收益高（验收 #4 必测）；模糊匹配仍留 P1 |
| R5 | Gitea token 泄露面 | 所有文档用去 token URL；remote 凭据仅本机保留；移交材料生成前用 `git remote set-url` 检查 |

---

## 7. 移交材料清单（发给开发方）

1. `contract-v0.2`（§1.2 修订后契约全文，frozen 接口）
2. 本方案（环境基线 §0 + 执行计划 §3 + 验收 §5）
3. 仓库：`/www/deepseek harness workspace/dsh-literatum`（Gitea `LazyFish/dsh-literatum`，**去 token URL**）
4. 骨架：`git clone http://127.0.0.1:3000/LazyFish/dsh-livetaskboard.git`（已验证存在）
5. 派生参照：`/www/deepmemory-v063-deploy/memory-server/server.py`（鉴权/路由模式）+ `/www/deepseek harness workspace/harness-memory-archive/web-plugin/`（代理模式）