# deepmemory 架构说明（模块级 · 依赖 · 数据流）

> **文档状态**：现行（2026-10-21 首次成文）
> **性质**：只读梳理。本文档不修改任何 `.js` / `.py` / `.yml`，不重启任何服务。
> **取代关系**：补充并部分取代 `memory-architecture-proposal.md`（后者是改造方案，不是现状描述）。
> **路径基线**：本文所有路径均为**绝对路径**；行号均以实际文件核对（`grep -n` / `sed -n`），非估算。
>
> **⚑ 权威版本声明（P0-2，2026-10-21）**：`plugin-v3.js` 与 `server.py` 都存在多份副本，
> **生产 live 是唯一权威**：插件以 `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`
> （1620 行 / md5 `1b32cd85…`）为准，服务端以 `/www/deepmemory-v063-deploy/memory-server/server.py`
> （4057 行 / md5 `652c345f…`）为准。仓库副本含**未部署**特性，**禁止**用仓库副本反推线上行为，
> 也**禁止**把两份 server.py 强行合并。详见文末 [附录 Z](#附录-z权威版本声明p0-22026-10-21)。

---

## 0. 先纠正一个前提：不存在「一份」插件/服务代码

梳理过程中最先撞到的事实是：**deepmemory 的运行时、仓库、暂存包各自跑着不同版本**。
如果按"三层并列"去读，会把三个不同版本当成同一套代码。

### 0.1 插件 `plugin-v3.js`：四份，四个 md5

| 角色 | 绝对路径 | 行数 | 大小 | md5 | 状态 |
|---|---|---|---|---|---|
| **生产运行** | `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | **1620** | 89859 | `1b32cd85f855182e481f0ea827293afd` | ✅ 线上真实挂载（preset 级） |
| 方案 A 暂存（未落盘） | `/www/deepseek harness workspace/planeA-prod-stage-20261018/files/plugin-v3.js` | **1751** | 97827 | `8cd3f75db8cd7da6389932308f95cdb4` | ⏳ 已通过测试机验证，**尚未应用到生产** |
| 仓库副本（较新） | `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/memory-plugin/plugin-v3.js` | 1155 | — | `a215b7dbd68c5b75f9d73efd5032cbd7` | ⚠️ 停止在 2026-09-16（无 child_memory / memory_call） |
| 仓库副本（旧） | `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/_memory-plugin/plugin-v3.js` | 849 | — | `ed28d8b206ca2a5e9ca57895a4de56af` | ❌ 更旧；**静态 import 全局 dsh-tools**（见 §5.3） |

**生产实际挂的是哪一份？** `/www/dsh/home/profiles/web/cordis.patch.yml` 第 504 / 838 / 958 行三处均写：

```
name: '/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js'
```

即 **绝对路径的 `.agent-presets/_memory-plugin` 副本（1620 行）**。
仓库里的 `agent-preset/memory-plugin/` 与 `agent-preset/_memory-plugin/` **都不是生产件**，
且仓库 `agent-preset/agent.cordis.yml:264` 仍写 `./memory-plugin/plugin-v3.js`（相对路径、旧副本），
与 live patch 不一致。`daily/agent.cordis.yml:92`、`task/agent.cordis.yml:176` 写的是
`../_memory-plugin/plugin-v3.js`。

> 结论：**唯一事实源应当是运行态文件**；仓库两份都落后于生产，不能当"源码"读。

### 0.2 `server.py`：两份，已分叉

| 角色 | 绝对路径 | 行数 | 大小 | md5/特征 |
|---|---|---|---|---|
| **生产运行** | `/www/deepmemory-v063-deploy/memory-server/server.py` | **4057** | 177274 | 运行进程 cwd 即此目录（PID 2662895） |
| 仓库副本 | `/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py` | **4366** | 191856 | 含 v0.4 assertion 模型（生产没有） |

两者不是"新旧"关系，是**双向分叉**：

- 生产**多**：P12 空回复修复（`LLM_MAX_TOKENS=2048`、空 content 视为失败并拒绝写入空摘要）、
  `/v1/topic-summaries/auto` 相关增强。
- 仓库**多**：v0.4 断言语义层（`assertions` / `assertion_events` 表、`_assertion_current_allowed_batch`、
  `_promote_assertion`、S06 孤儿事件清理），共约 300 行。
- 同一函数的行号差从 +3（文件头）扩大到 +310（Handler 附近）。**不能拿一份的行号直接套另一份。**

本文件后续 §2.2 的表格以**仓库副本**行号为准（因为任务指定的路径是仓库），
并在 §2.2.1 给出生产副本的关键函数行号对照，避免误读。

### 0.3 Web 插件：仓库比线上新

| 角色 | 绝对路径 | 大小 | 说明 |
|---|---|---|---|
| 线上运行 | `/www/dsh/home/profiles/web/node_modules/.pnpm/dsh-deepmemory@file+…/node_modules/dsh-deepmemory/index.js` | 83166 | 仍是 **preset 行判断**（未含方案 A） |
| 仓库 | `/www/deepseek harness workspace/dsh-deepmemory/web-plugin/index.js` | 87472 | 已含方案 A（`loadHostPlaneRows` / `findMemoryPluginRow` / frame0 header 补齐），**未部署** |

`client.js` 两边一致（md5 `670c709e622fef7b2ca5be6d1857764d`）。

### 0.4 数据规模与 embedding（2026-10-21 实测）

| 项 | 值 | 来源 |
|---|---|---|
| `documents` 总行数 | **33 345** | 只读打开 `/www/deepmemory-v063-deploy/memory-server/data/memory.db` |
| 按 library/scope | runtime/session 32 397；runtime/workspace 516；runtime/global 349；core/workspace 27；project/workspace 20；core/session 18；project/session 14；bias/global 4 | 同上 |
| embedding provider | `api`，`http://127.0.0.1:6192/v1`，模型 `bge-m3` | `settings` 表 |
| 向量维度 | **1024**（`dim.json = {"dim":1024,"fp":"api:bge-m3"}`） | `data/dim.json` |
| `server_url` | `http://127.0.0.1:6230`（生产自指，正确） | `settings` 表 |

> 注意：`server.py` 头部注释与常量 `DIM = 512` 描述的是**本地 fastembed** 路线，
> 当前生产走**远程 API bge-m3（1024 维）**。判断维度必须以 `dim.json` 为准。

---

## 1. 分层与依赖（带方向）

### 1.1 不是"三层并列"，而是"三个进程 + 一个宿主总线"

```
                          ┌──────────────────────────────────────────┐
                          │            dsh-web 进程 (:3081)           │
                          │                                          │
  浏览器 ──HTTP/同源──▶   │  ┌────────────────────────┐              │
  (client.js)             │  │ web-plugin/index.js    │              │
      │                   │  │  · /mem-api/* 路由      │              │
      │  fetch('/mem-api')│  │  · 读会话日志/注入详情   │              │
      └──────────────────▶│  └───────────┬────────────┘              │
                          │              │ HTTP + Bearer             │
                          │  ┌───────────▼────────────┐              │
                          │  │ preset 插件 plugin-v3  │              │
                          │  │  · systemPrompt.context │              │
                          │  │  · assemble 钩子        │              │
                          │  │  · turn-stopping 抽取   │              │
                          │  └─────┬──────────┬───────┘              │
                          │        │ DSH API  │ HTTP + Bearer        │
                          └────────┼──────────┼──────────────────────┘
                                   │          │
        ┌──────────────────────────▼──┐   ┌───▼──────────────────────────────┐
        │ DSH Core 服务总线            │   │ memory-server (:6230)            │
        │ · systemPrompt              │   │  · HTTP 路由 / 鉴权              │
        │ · commands / tools          │   │  · SQLite + FAISS + BM25         │
        │ · llm (stream/listModels)   │   │  · embedding (fastembed/API)     │
        │ · sessionPersistence        │   │  · 抽取结果落库 / 状态卡 / 任务   │
        │ · agents / webServer        │   │  · 敏感原文 vault / 备份/迁移     │
        └─────────────────────────────┘   └──────────────────────────────────┘
                                                    ▲
                                                    │ 文件系统（SQLite / FAISS / 备份）
                                                    │
        ┌───────────────────────────────────────────┴──────────────────────┐
        │ 会话文件体系（dsh-web 与 web-plugin 直接读）                       │
        │ $DSH_HOME/sessions/<project>/<session>/session.v*.jsonl.zstd      │
        │ $DSH_HOME/storages/workspace.json                                 │
        │ $DSH_HOME/storages/session_projcache.json                         │
        │ $DSH_HOME/profiles/web/cordis.patch.yml                           │
        │ $DSH_HOME/.agent-presets/_memory-plugin/plugin-v3.js              │
        │ $DSH_HOME/.dsh-memory-api-token                                   │
        └──────────────────────────────────────────────────────────────────┘
```

### 1.2 依赖方向（谁依赖谁）

| 依赖方 | 被依赖方 | 协议 | 证据 |
|---|---|---|---|
| `client.js` | `web-plugin/index.js` | 浏览器同源 HTTP `fetch('/mem-api' + path)` | `client.js:208-237` |
| `web-plugin/index.js` | memory-server | HTTP `http.request` → `localhost:6230`，`Authorization: Bearer` | `index.js:17-19, 39-73` |
| `web-plugin/index.js` | DSH Core | `ctx.get('agents'/'sessionPersistence'/'llm'/'systemPrompt'/'webServer')` | `index.js:304-338, 1247-1720` |
| `web-plugin/index.js` | 文件系统 | 读 zstd 会话日志、`workspace.json`、`session_projcache.json`、`cordis.patch.yml`、`plugin-v3.js` 源码 | `index.js:32, 603, 630, 644, 786, 973-1054, 1272, 1530` |
| `plugin-v3.js` | memory-server | HTTP `fetch` → `SERVER`（默认 `http://localhost:6230`），`Authorization: Bearer` | live `222-241` |
| `plugin-v3.js` | DSH Core | `ctx.get('systemPrompt'/'commands'/'llm')`、`ctx.on(...)`、`ctx.tools.register` | live `753, 770, 816, 899, 1145, 1588` |
| `plugin-v3.js` | 文件系统 | 读 `$DSH_HOME/storages/workspace.json`、token 文件 | live `105-149, 91-104` |
| `plugin-v3.js` | `@deepseek-ai/dsh-tools` | 动态 `import()` 多候选（N25） | live `22-48` |
| memory-server | SQLite / FAISS / 模型目录 | 本地文件 | `server.py:71-84, 328, 995` |
| memory-server | 外部 LLM（仅 topic summary） | HTTPS `llm_chat` | `server.py:1155`（仓库） |
| `_literature-*` 插件 | literature-server `:6260` | HTTP + 显式 token 路径 | `_literature-kb-plugin/plugin-v1.js:27-33` |

**没有的依赖（重要）**：

- `plugin-v3` **不依赖** `web-plugin`，反之亦然。两者是同一 dsh-web 进程里的**两个独立模块**，
  不共享作用域：`memoryCache` 是 plugin-v3 的模块私有变量，Web 插件读不到（`index.js:335` 注释已写明）。
- `memory-server` **不依赖** 任何插件，也不主动回调 DSH。
- **memory-server 里没有 literature 桥接**（全文件搜 `literature` 为 0 命中）。
  文献是独立服务（6260）+ 两个独立 preset 插件；不要把文献算进 deepmemory 的后端模块。

### 1.3 部署拓扑（端口事实）

| 端口 | 进程 | 服务 | 用途 | 核实 |
|---|---|---|---|---|
| `3081` | node | `dsh-web.service` | Web GUI + `/mem-api` 代理 + 所有 DSH API | `ss -tlnp` |
| `6230` | python3 | `dsh-memory-server.service` | **生产** memory-server | `ss` + cwd `/www/deepmemory-v063-deploy/memory-server` |
| `6240` | python3 | `dsh-test-memory.service` | 测试机 memory-server | `ss` |
| `6260` | python3 | `dsh-literature.service` | 生产 literature-server（非本文范围） | `ss` |
| `6263` | — | `dsh-test-literature` | 测试 literature | 插件注释 |
| `3091` | — | `dsh-test.service` | 测试 dsh-web | 迁移文档 |

---

## 2. 模块拆解（行号级）

### 2.1 `plugin-v3.js`（生产运行副本，1620 行）

> 路径：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`
> 职责：preset 平面插件，负责**注入拼装、抽取触发、会话级开关、状态卡/任务板写回、记忆工具面**。

| # | 模块 | 行号 | 职责 | 读取（输入） | 写出（输出/副作用） | 外部依赖 |
|---|---|---|---|---|---|---|
| 1 | 文件头 + `dsh-tools` 解析 | 1–52 | 说明、`name='deepmemory'`、`inject=['tools']`、N25 多候选 `defineTool` 解析与降级 | `process.argv[1]`、`createRequire` | `defineTool` / `degraded()` | `@deepseek-ai/dsh-tools` |
| 2 | `apply()` 状态容器 | 54–84 | 每会话 Map：`buckets`/`memoryCache`/`enabledCache`/`childMemoryCache`/`recentBySession`/`_parentOfSession`/`initializedSessions` | `config.preset_mode` | 内存状态（**不持久化**） | — |
| 3 | 服务器地址 + token | 85–104 | `SERVER` 默认 `localhost:6230`（`MEMORY_SERVER_PORT` 可覆盖）；`TOKEN_FILES` 三级 fallback | env、`$DSH_HOME/.dsh-memory-api-token`、`$HOME/...` | `readToken()` | fs |
| 4 | workspace 归属 | 105–149 | `readWorkspaceTables` / `lookupWorkspace` / `resolveWorkspace`：按会话 id（含父会话）反查 workspace | `$DSH_HOME/storages/workspace.json` | workspace_id 字符串 | fs |
| 5 | 运行配置常量 | 150–162 | `WORKSPACE`、`EXTRACT_THRESHOLD=4`、`RECALL_K=5`、**`INJECT_BUDGET_CHARS=2600`**、`INJECT_ORDER=50`、`OPERATION_INTENTS`、`detectOperationIntent` | 硬编码 | — | — |
| 6 | 注入指引文案 | 164–173 | `GUIDE`（主）/`GUIDE_CHILD`（子 agent，强调只读） | — | 拼进注入尾部 | — |
| 7 | 运行期开关 | 174–180 | `INJECT_ENABLED/INJECT_CARD/EXTRACT_ENABLED/TOOLS_ENABLED/DECAY_RATE/EXTRACT_PROVIDER/EXTRACT_MODEL` | 由 `loadConfig` 覆写 | — | — |
| 8 | 抽取提示词 | 182–201 | `EXTRACT_SYSTEM`：记忆/原子/实体/关系/状态卡/任务的 JSON 契约；含凭据脱敏要求 | — | LLM system prompt | — |
| 9 | 脱敏 | 204–220 | `redactSensitive`：git token、`sk-`、AWS、JWT、Bearer、私钥、`token=` 形式 | 文本 | 替换为 `[REDACTED:*]` | — |
| 10 | HTTP 封装 | 222–241 | `http(method,path,body)`：URL 校验（仅 http/https）、25s 超时、JSON | — | `{ok,data}` / `{ok:false,error}` | `fetch` |
| 11 | 配置读取 | 243–286 | `readKey`/`readKeyOr`/`loadConfig`：GET `/v1/settings/<key>`，映射到本地变量 | memory-server settings | 覆写 §7 的开关与 `SERVER` | — |
| 12 | 会话 id / 开关 | 288–320 | `sessionIdOf`；`isEnabled`/`setEnabled`（**5s TTL 缓存**，N04） | `/v1/settings/session_enabled:<sid>` | 同左 | — |
| 13 | 子 agent / astra 闸门 | 318–441 | `headerOfAgent/isDelegatedChild/headerOfSession/isDelegatedSession/parentSessionOf/isAstraAgent/childMemoryEnabled/setChildMemoryEnabled/shouldSupplyMemory/memoryReadAllowed/memoryScopeSessionId/guideTextFor` | `session.header.origin/delegationDepth/parentSession`、`agent.options.provider/model` | 布尔闸门 + 子会话→父会话映射 | DSH header |
| 14 | 记忆格式化为注入文本 | 445–496 | `formatMemories`：过滤 `rule_crystallized`、importance 排序、`preference≥0.8` 保底、按类型分组、每条截 240 字 | search 结果数组 | `[长期记忆召回]` 文本 | — |
| 15 | 模型路由 | 498–525 | `resolveModelRoute`：**settings 有值即早退**；否则 `listProviders/listModels` 候选（deepseek-official → uuapi → 其余） | `ctx.get('llm')` | `{provider,model}` | DSH LLM API |
| 16 | 抽取 | 527–590 | `extract(dialog)`：固定 `reasoningEffort='low'`、45s 超时、`llm.stream`、JSON 截取解析；失败返回 null（不吞正文） | 对话批次 | 结构化 JSON | DSH LLM API |
| 17 | 检索 query 组装 | 594–600 | `recentQuery`：最近 6 条消息前 1500 字 + seed 200 字 | `recentBySession` | query 字符串 | — |
| 18 | 状态卡渲染 | 602–613 | `cardText`：goal / current_plan / next_steps → `[会话状态]` | card payload | 文本块 | — |
| 19 | 刷新调度 | 615–637 | `ensureSessionRefresh`：按 `userCount` 变化触发，回合内冻结，`pendingRefresh` 去重 | `state.userCounts` | Promise | — |
| 20 | 检索 + 缓存刷新（核心） | 629–741 | `refreshMemoryCache`：三类并行检索 + bias 恒并 + L3 操作规则 + 摘要链 + 状态卡 → 拼文本 → **2600+300 截断** → 写 `memoryCache` → POST `/v1/memories/injection-log` | memory-server 6 个端点 | 注入全文缓存 + 详情 | — |
| 21 | `systemPrompt.context` 注册 | 753–768 | 注册名为 `deepmemory` 的上下文块（对话流尾部持久 user 快照），`order=50` | `memoryCache` | 注入文本 | DSH systemPrompt |
| 22 | `system-prompt/assemble` 钩子 | 770–814 | 每次组装：60s 节流 `loadConfig` → GET `/v1/config/session` → `shouldSupplyMemory` → `ensureSessionRefresh` → **按 name 替换 `assembled.contexts`** | 同上 | 修改后的 assembly | DSH hook |
| 23 | `session/event` 钩子 | 816–897 | 收 `user/message`/`assistant/message` 入桶；跳过 `source.kind!=='user'` 合成输入；`compaction/summary` → POST `/v1/topic-summaries` | 事件流 | `buckets`/`recentBySession`/`topic_summaries` | DSH hook |
| 24 | `agent/turn-stopping` 钩子 | 899–1140 | 门槛判断 → `extract` → POST `/v1/memories/add_batch`（逐项核对 `added[].error`）→ GET/PUT `/v1/v2/cards`（字段缺省保留旧值）→ 任务板 BFS 状态机（GET tasks / POST create / POST transition） | 抽取结果 | 记忆、状态卡、任务 | DSH hook |
| 25 | `/memory` 命令 | 1145–1190 | `on/off/status/clean/child on|off|status`；子 agent 禁止改策略 | `commands.register` | 会话开关、衰减 | DSH commands |
| 26 | 三层读取辅助 | 1193–1360 | `textRender/normalizeLayer/clampInt/p0Title/sourceCountOf/memoryRowsForCall/memoryIndexLayer/memoryLayer/readSourceWindow` | server 响应 | 工具输出 | — |
| 27 | `memory_call` 工具 | 1362–1424 | 唯一对外入口：`layer=index|memory|source`，`ids` 直取，`source` 走窗口 + untrusted 标记 | `exec.agent` | 结构化结果 | `defineTool` |
| 28 | `memory_child_default` | 1426–1451 | 主 agent 开关子 agent 注入 | `args.enabled` | `child_memory:<sid>` | — |
| 29 | 兼容工具 | 1453–1586 | `memory_recall` / `memory_save` / `memory_briefing` / `memory_source`（保留给根 agent 与旧调用） | — | — | — |
| 30 | 工具注册 | 1588–1593 | 降级时（`__degraded`）跳过注册 | — | 6 个工具 | DSH tools |
| 31 | 子 agent 工具裁剪 | 1596–1614 | `agent/created` 时 `agent.ctx.tools.restrict({deny:[...]})`，覆盖 team/ralph/one-shot（**`toolFilter.deny` 覆盖不到 team**） | — | 工具面缩窄 | DSH agent API |
| 32 | 启动 | 1616–1619 | `loadConfig().then(ready log)` | — | — | — |

### 2.1.1 方案 A 暂存版（1751 行）相对生产版的差异

| 变化 | 暂存版行号 | 生产版行号 | 含义 |
|---|---|---|---|
| profile/root 平面常量（注释 + `MEMORY_PRESETS` + `DEFAULT_CARD_KIND`） | `71-105` | 无 | 根级挂载后按 preset 白名单自闸门 |
| `presetOfAgent/presetOfSession/isMemoryAgent/isMemorySession` | `409-436` | 无 | 不再靠"preset 里有行"判断 |
| `cardKindForAgent`（`-daily` 后缀） | `435-436` | `CARD_KIND` 常量 68 | 卡类型从配置改为按 preset 推导 |
| `registerMemoryCommand(agent)` 按 agent 注册 | `1257-1275` | 全局注册 1145 | 根级插件不能全局注册命令 |
| `ensureAgentPlane(agent)` | `1689-1723` | 无 | 按 agent 建平面/裁剪工具 |
| `INJECT_BUDGET_CHARS` | `170` | `153` | **仍然硬编码 2600**（未解决 §5.1） |

### 2.2 `server.py`（仓库副本，4366 行）

> 路径：`/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py`
> 职责：HTTP API + 持久化 + 检索融合 + 生命周期。**不是"一个 memory CRUD"**，它同时是
> 向量库、关键词库、图库、状态卡库、任务库、敏感原文 vault、备份/迁移工具。

| # | 模块 | 行号（仓库） | 职责 | 表/文件依赖 | 对外接口 |
|---|---|---|---|---|---|
| 1 | 文件头 + 常量 | 1–104 | 说明、`PORT=6230`、`DATA_DIR`、`API_TOKEN_FILE`、`DIM=512`、权重 `ALPHA/BETA/GAMMA`、`RRF_K=60` | — | — |
| 2 | embedding 配置与调用 | 105–330 | `get_embed_config`、`_validate_embedding_*`、`_embed_api`（禁重定向）、`embed_texts`（local fastembed / API） | `models/fast-bge-small-zh-v1.5`、`settings` 表 | `POST /v1/embeddings` |
| 3 | SQLite 连接 | 328–480 | `get_conn()`（WAL/超时/row_factory） | `data/memory.db` | — |
| 4 | 迁移 | 481–695 | `run_migrations()`：DDL 版本链；v0.4 建 `assertions`/`assertion_events`；S06 清孤儿；`_migrate_legacy_disputed_assertions`；`init_db()` | `schema_version`、全部表 | 启动时 |
| 5 | 断言语义层 | 696–994 | 语义代际 `_semantic_gen`、`_assertion_current_allowed_batch`、事件记录、`_promote_assertion`（≥2 独立确认 → adopted） | `assertions`、`assertion_events` | `/v1/assertions/*` |
| 6 | FAISS 索引 | 995–1031 | `get_index`/`save_index`（`IndexIDMap(IndexFlatL2)`） | `data/memory.faiss`、`dim.json` | 内部 |
| 7 | BM25 | 1032–1117 | jieba 分词 + `BM25` 类（add/remove/replace/search） | 内存索引（启动重建） | 内部 |
| 8 | 注入日志 / info_store / LLM | 1118–1216 | `note_injection`、`get_info_store`（懒加载 `info_store.InfoStore`）、`_credentials_map`、`llm_chat` | `info/`、`.credentials.yaml` | `/v1/memories/injection-log`、`/v1/info/*` |
| 9 | 向量/图检索 | 1217–1293 | `normalize`、`vector_search`、`graph_search`（实体→记忆） | FAISS、`graph_nodes/edges` | 内部 |
| 10 | 融合与加权 | 1294–1387 | `cfg_float/cfg_bool`、`rrf_fuse`、`apply_weighting`（relevance/importance/recency）、`scope_allows` | `settings` | 内部 |
| 11 | **检索主流程** | 1388–1485 | `search_memories`：缓存（语义代际失效）→ 向量/BM25/图三路 → RRF → 加权 → scope/type/library/tier/断言门禁 → 访问强化 | 多表 + 缓存 | `POST /v1/memories/search` |
| 12 | 写入 | 1486–1603 | `add_memory`：脱敏 → 兜底 scope → embedding → 可选近重复合并（默认关）→ library 校验（bias 需 global+≥0.8）→ INSERT | `documents`、`sources`、`protected_sources` | `POST /v1/memories/add` |
| 13 | 断言创建 / 原文 | 1604–1681 | `_create_assertion_for_memory`、`_protected_ids`、`_save_source`（8000 字分段、32000 上限、`truncated` 标记） | `assertions`、`sources` | 内部 |
| 14 | 原子/实体/关系 | 1682–1802 | `add_atom`、`add_entity`、`add_relation`（图边） | `atoms`、`graph_nodes/edges` | `/v1/atoms/*`、`/v1/graph` |
| 15 | 导出/来源 | 1803–1892 | `export_archive`（since/workspace）、`get_sources` | `documents`、`sources` | `/v1/memories/export-archive` |
| 16 | 原文窗口 | 1893–2179 | 游标解析、seq 窗口、关键词窗口、`get_source_window`（≤800 字/次） | `sources` | `GET /v1/memories/<id>/source`、`memory_call layer=source` |
| 17 | 删除/更新/附件 | 2180–2296 | `delete_memory`（软/硬）、`update_memory`、`_process_attachments`、`add_batch`（逐项 error） | `documents`、`document_links` | `DELETE/PUT /v1/memories/<id>` |
| 18 | 状态卡 / settings | 2297–2362 | `get_card`、`get_setting`、`set_setting` | `workspace_cards`、`settings` | `/v1/cards/*`、`/v1/settings/*` |
| 19 | 敏感 vault | 2363–2552 | 审计、`_record_protected`、审批 token、`expand_sensitive_source`、`end_sensitive_session` | `protected_sources`、`sensitive_audit`、`sensitive_approvals` | `/v1/sensitive/*` |
| 20 | 配置中心 | 2553–2694 | schema 读取、密钥键过滤、全局/会话级配置读写与回退 | `config_schema.json`、`settings` | `/v1/config*` |
| 21 | 衰减 / 生命周期 | 2695–2898 | `_run_decay`、`_nightly_tier_forgetting`、`_nightly_maintenance`、`lifecycle_scheduler`（小时级线程） | `documents`、`atoms` | `/v1/maintenance/decay` |
| 22 | 归档/备份/恢复 | 2899–3041 | `archive_memories`、`restore_memory`、`create_backup`、`list_backups`、`restore_backup`（文件名白名单） | `data/backups/*` | `/v1/backups/*`、`/v1/memories/archive` |
| 23 | 重建索引 | 3042–3118 | `_rebuild_indexes_internal`（FAISS + BM25 从 DB 重建） | 全表 | `/v1/maintenance/rebuild` |
| 24 | 规则固化 / 合并 | 3119–3287 | `rule_candidates`（相似度+出现次数）、`crystallize_rules`（写规则文件）、`consolidate_memories` | `documents`、规则目录 | `/v1/maintenance/*` |
| 25 | 合并应用 / 概览 | 3288–3412 | `apply_consolidation`、`get_overview`、`put_card` | 多表 | `/v1/overview`、`/v1/cards/upsert` |
| 26 | HTTP Handler | 3413–4321 | `_reject_browser_origin`（无 Origin + Bearer token）、`_read_body`、v2 分派、`do_GET/do_POST/do_PUT/do_DELETE` | — | 全部路由 |
| 27 | token / main | 4322–4365 | `_ensure_api_token`（文件缺失即生成 0600）、`ThreadingHTTPServer` 启动 | `data/api-token` | — |

#### 2.2.1 生产副本（4057 行）关键函数行号对照

| 函数 | 生产 | 仓库 | 函数 | 生产 | 仓库 |
|---|---|---|---|---|---|
| `get_embed_config` | 102 | 105 | `search_memories` | 1106 | 1388 |
| `_embed_api` | 257 | 260 | `add_memory` | 1204 | 1486 |
| `get_conn` | 325 | 328 | `add_batch` | 1994 | 2284 |
| `run_migrations` | 478 | 481 | `get_source_window` | 1804 | 2086 |
| `init_db` | 618 | 666 | `get_config_values` | 2290 | 2580 |
| `get_index` | 697 | 995 | `_run_decay` | 2421 | 2711 |
| `tokenize` | 734 | 1032 | `create_backup` | 2656 | 2946 |
| `llm_chat` | 864 | 1155 | `rebuild_indexes` | 2824 | 3114 |
| `vector_search` | 940 | 1222 | `consolidate_memories` | 2906 | 3196 |
| `graph_search` | 965 | 1247 | `get_overview` | 3046 | 3336 |
| `rrf_fuse` | 1033 | 1315 | `class Handler` | 3123 | 3413 |
| `apply_weighting` | 1042 | 1324 | `_ensure_api_token` | 4013 | 4322 |
| `scope_allows` | 1094 | 1376 | `main` | 4039 | 4348 |

> 生产副本**没有** `assertions` / `assertion_events` 相关段（`grep -c assertion_events` = 0），
> 因此 `search_memories` 中没有语义门禁那 20 行；仓库副本有。

### 2.3 辅助模块

| 模块 | 绝对路径 | 行数/大小 | 角色 | 被谁调用 | 备注 |
|---|---|---|---|---|---|
| `v2_domain.py` | `.../memory-server/v2_domain.py` | 76 KB | **v2 事务数据模型**：任务、状态卡修订、记忆生命周期、会话 key、导入导出、purge、recall | `server.py:59` 导入 `V2Store` 等；`Handler._v2_store()` | 仅标准库 + `sensitive`；`V2_SCHEMA_VERSION=5` |
| `sensitive.py` | `.../memory-server/sensitive.py` | 4346 B | 敏感检测/脱敏原语（Luhn、证件、密钥模式） | `server.py:54`、`v2_domain.py:14` | 无 DB/HTTP 依赖 |
| `info_store.py` | `.../memory-server/info_store.py` | 3799 B | 域级信息注册表（`env/project/status/config/ticket` → `data/info/<domain>.db`） | `server.py:1136` 懒加载 | 白名单外 404 |
| `kb-query.py` | `.../memory-server/kb-query.py` | 5810 B | **CLI**：search/libraries/for-doc/doc-link/archive/restore-tier/health | 人工调用；**不被 server import** | 默认 `http://127.0.0.1:6230` |
| `config_schema.json` | `.../memory-server/config_schema.json` | 19 KB | 配置中心 schema（18 个一级段） | `server.py:2553` | 段：basic/embedding/scope/injection/recall_engine/fusion_strategy/reflection_engine/state_card/context_automation/importance_decay/archiving/agent_tools/search_cache/access_reinforcement/sensitive/memory_consolidation/compression/library |
| `tests/` | `.../memory-server/tests/` | 5 文件 | `test_library_model` / `test_lifecycle` / `test_sensitive` / `test_v2_domain` / `test_v2_http` | pytest | — |

### 2.4 `web-plugin/index.js`（仓库副本，1992 行）

> 路径：`/www/deepseek harness workspace/dsh-deepmemory/web-plugin/index.js`
> 职责：**宿主面插件**。两条腿：① 给浏览器提供 `/mem-api` 反向代理；② 把会话日志解析成"注入详情/工具集"报告。

| # | 模块 | 行号 | 职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|---|
| 1 | 头部 + 常量 | 1–28 | `name='deepmemory'`、`inject=['webServer','timer']`、`TARGET_PORT=6230`、`PREFIX='/mem-api'`、token 路径 | env | — | — |
| 2 | `readToken` + `memoryRequest` | 29–73 | Node `http.request` 转发到 memory-server，60s 超时，Bearer | — | JSON / throw | `node:http` |
| 3 | 小工具 + 模型路由 | 74–124 | `boolValue`、`resolveModelRoute`（Web 侧独立实现，与插件不共享）、`hostTimeoutSignal` | `ctx.get('llm')` | — | DSH LLM |
| 4 | 脱敏 / 摘要 | 125–216 | `redactSensitive`、`summarizeGroup`、`extractSessionCard`（五回合状态卡） | 对话文本 | card JSON | DSH LLM |
| 5 | 事件文本辅助 | 217–260 | `eventText`、`stateCardKind`（`preset_mode==='daily'`）、`presetHasMemoryCompletion`、`memoryCompletionText` | 事件 | 文本 | — |
| 6 | 会话事件读取 | 261–338 | `sessionEventList`、`latestMemoryQuery`、`cadenceCompactionRange`、`sessionEvents`（live → persistence） | `agent.session` / `sessionPersistence` | 事件数组 | DSH |
| 7 | 注入分段常量 | 339–393 | `INJECT_SECTION_NAME='deepmemory'`、段标记、截断正则、工具来源提示 | — | — | — |
| 8 | 文本/头部解析 | 394–461 | `asText`、`messageContentText`、`findSessionHeaderEvent`、`lastEventOfType`、`sessionMeta`、`extractRuntimeContext` | 事件 | meta/runtime | — |
| 9 | 注入分段与 system prompt | 462–597 | `splitInjectionSegments`、`systemPromptHeuristicSegments`、`extractSystemPrompt`、`extractRequestHeader`、`extractToolFilter`、`parsePluginRows` | 事件/YAML 文本 | 结构化报告 | 正则/YAML 文本 |
| 10 | 平面行加载 | 598–648 | `loadPresetOnlyRows`、`MEMORY_PRESET_IDS`、`findMemoryPluginRow`、`loadHostPlaneRows`、`loadPluginRows` | `cordis.patch.yml`、preset 源 | 行数组 | fs |
| 11 | 工具来源解析 | 649–706 | `resolveToolSource`（工具名 → 插件行） | 行 + 工具名 | `{source,...}` | — |
| 12 | 工具调用/回合输入 | 707–779 | `collectToolCalls`、`buildTurnInputMap` | 事件 | 映射 | — |
| 13 | **注入预算（源码正则）** | 780–794 | `inspectPluginBudget`：**读 `plugin-v3.js` 源码正则抠 `INJECT_BUDGET_CHARS` 与 `+300`**，默认 2600/300 | 插件源码文件 | `{base,extra,effective,source}` | fs + 正则 |
| 14 | 注入/工具报告 | 795–950 | `buildInjectionReport`、`buildToolsReport` | 事件 + budget + flags | 报告 JSON | — |
| 15 | 会话日志兜底 | 951–1074 | `readSessionLogHeader`（只解首帧）、`findSessionLogFile`、`zstdFrameEnd`、`decompressZstdFrames`、`readSessionLogEvents` | `$DSH_HOME/sessions/**/session.v*.jsonl.zstd` | 事件 | `node:zlib`（zstd） |
| 16 | 状态卡初始化 / 合并 | 1075–1188 | `initializeStateCard`、`performConsolidation` | LLM + memory-server | 卡/合并结果 | — |
| 17 | HTTP 辅助 | 1190–1246 | `readRequestJson`、`writeJson`、`queryParam`、`pickFirst`、`boolSetting` | req/res | JSON | — |
| 18 | `apply()` + 钩子 | 1247–1720 | 设置索引（不再 `register`）、`refreshHostMemory`、`systemPrompt.section`、`assemble` 补全记忆补全、`session/event`、`turn-stopping`（五回合卡 + 压缩） | DSH hooks | 注入补全 / 卡 | DSH |
| 19 | **9 条 `/mem-api` 路由** | 1722–1991 | 会话列表、注入详情、工具集、配置读写、会话开关、模型列表、合并、卡初始化、健康 | 浏览器 | JSON | memory-server |

`/mem-api` 路由清单（`path: PREFIX + ...`）：

| 方法 | 路径 | 处理 |
|---|---|---|
| GET | `/mem-api/v1/context/sessions` | 列会话（`session_projcache.json` 标题 + `sessions/` 目录扫描） |
| GET | `/mem-api/v1/context/injection` | 读会话事件 → `buildInjectionReport` |
| GET | `/mem-api/v1/context/tools` | `buildToolsReport` |
| POST | `/mem-api/v1/context/config` | 写 memory-server 配置 |
| POST | `/mem-api/v1/context/session-flag` | 写会话开关 |
| GET | `/mem-api/v1/models` | 列 provider/model |
| POST | `/mem-api/v1/maintenance/consolidate` | 触发合并（单飞 + 队列） |
| POST | `/mem-api/v1/cards/initialize` | 初始化状态卡 |
| * | `/mem-api`（前缀兜底） | 透传到 memory-server |

### 2.5 `web-plugin/client.js`（2078 行）

| # | UI 区块 | 行号 | 职责 |
|---|---|---|---|
| 1 | i18n + CSS | 1–207 | 中英文案、面板样式（`dsh-mem-*`） |
| 2 | `api()` | 208–237 | `fetch('/mem-api' + path)`，统一错误 |
| 3 | `ListEditor` | 240–269 | 列表编辑小组件 |
| 4 | `apply()` 入口 | 271–392 | 注册侧栏入口/页面 |
| 5 | `GraphView` | 393–619 | 实体图谱（缩放/平移/选中/关联记忆） |
| 6 | `ArchiveView` | 620–662 | 归档列表、恢复、硬删 |
| 7 | `MaintenanceView` | 663–786 | 备份/恢复/重建/合并/衰减/热记忆/注入日志 |
| 8 | **`InjectionView`（三 Tab）** | 787–1219 | Tab1 注入内容（分段/预算/截断标记）、Tab2 工具集、Tab3 开关与预算 |
| 9 | `ContextInspectSection` | 1220–1234 | 嵌入记忆页的上下文检查 |
| 10 | **`ConfigView`（配置页）** | 1235–1469 | 按 `config_schema.json` 渲染表单、全局/会话级覆盖、重置 |
| 11 | `MemoryRow` | 1470–1541 | 单条记忆展示/编辑 |
| 12 | **`MemoryPanel`（记忆页）** | 1542–2078 | 列表/搜索/新增、状态卡编辑器、修订历史、会话 key、导出、敏感展开 |

> 「注入详情」不是从插件内存读的：`memoryCache` 在另一个模块里。Web 插件是从
> **会话日志的 runtime-context** 还原"实际下发给模型的文本"（`index.js:335-337` 注释）。

---

## 3. 数据流（端到端）

### 3.1 注入（每回合开始）

```
用户发消息
  │
  ▼ DSH Core 产生 session/event(user/message)
plugin-v3.js  L816 session/event
  ├─ L856  过滤 source.kind !== 'user'（防记忆自我强化）
  ├─ L874  写 state.userCounts / lastUserText
  ├─ L884  入 buckets（≤40 条，每条 800 字）
  ├─ L888  入 recentBySession（≤10 条，每条 600 字）
  └─ L895  ensureSessionRefresh(sid)                        L615
              └─ refreshMemoryCache(query, limit)           L629
                   ├─ POST /v1/memories/search ×3 类（preference/rule、decision/goal/plan、fact/episode）
                   ├─ POST /v1/memories/search  library=bias（恒并，top-6）
                   ├─ POST /v1/memories/search  type=preference,rule（操作意图命中时，预算外）
                   ├─ GET  /v1/topic-summaries?topic_id=<sid 前12位>   L706
                   ├─ GET  /v1/v2/cards/<task|daily>/<sid>
                   ├─ formatMemories（importance 排序 + 偏好保底 + 分组）
                   ├─ L727-729  超 2600+300 字符截断 → 追加 "[^ 预算裁剪 N 字符 ^]"
                   ├─ L734  memoryCache.set(sid, text)
                   └─ L743  POST /v1/memories/injection-log { full }

DSH Core 组装 system prompt
  │
  ▼ system-prompt/assemble
plugin-v3.js  L770
  ├─ L777  next() 拿基线 assembly
  ├─ L783  （60s 节流）loadConfig()                        L257
  │           └─ GET /v1/settings/<key> ×12 → 覆写开关/阈值
  ├─ L784  GET /v1/config/session?session_id=...
  ├─ L791  shouldSupplyMemory(agent)                       L412
  │           ├─ isAstraAgent? → false
  │           ├─ 主会话 → isEnabled(sid)（5s TTL）
  │           └─ 子会话 → 父 isEnabled && child_memory:<parent>
  ├─ L798  await ensureSessionRefresh(...)
  ├─ L799  injectText = memoryCache.get(sid)
  └─ L812  按 name==='deepmemory' 替换 assembled.contexts[i].text

另外：systemPrompt.context（L755）注册了同名块，order=50，
      作为"对话流尾部持久 user 快照"的渲染兜底（与 assemble 写同一份缓存）。

模型收到：contexts[deepmemory].text = 记忆块 + GUIDE
```

**回合同步**：`userCount` 未变时 `ensureSessionRefresh` 直接复用 `pendingRefresh`，
因此**回合内注入冻结**，避免击穿前缀缓存（`L615-627` 注释）。

### 3.2 抽取（回合结束）

```
agent/turn-stopping
  │
  ▼ plugin-v3.js L899
  ├─ 闸门：EXTRACT_ENABLED / 子 agent / astra / isEnabled
  ├─ buckets.get(sid).length >= EXTRACT_THRESHOLD(4)
  ├─ extract(dialog)                                       L527
  │    ├─ resolveModelRoute(llm)                           L498
  │    │    └─ settings 有 provider+model → 直接早退（关键）
  │    ├─ llm.listModels(provider)（仅日志，不决定 effort）
  │    ├─ llm.stream({system: EXTRACT_SYSTEM, temperature:0.2,
  │    │             reasoningEffort:'low', signal: 45s})
  │    └─ 截取首个 '{' .. 末个 '}' → JSON.parse
  ├─ 结构守卫（Array.isArray）→ 逐字段 redactSensitive
  ├─ POST /v1/memories/add_batch { items:[...] }           L956
  │    └─ server.py add_batch → add_memory ×N
  │         ├─ _sanitize_text_fields（脱敏 + vault）
  │         ├─ embed_texts → normalize
  │         ├─ INSERT documents
  │         ├─ BM25.add
  │         ├─ FAISS add_with_ids
  │         ├─ _create_assertion_for_memory
  │         └─ _save_source（8000 分段 / 32000 上限 / truncated）
  ├─ 状态卡：GET /v1/v2/cards → 逐字段 merge → PUT /v1/v2/cards
  └─ 任务板：GET /v1/v2/tasks → 同 title 匹配
            → 无则 POST /v1/v2/tasks
            → 有则 BFS 最短合法路径 → POST /transition（阻塞卡先 /blocked false）
```

**关键设计**：抽取成功后**才**消费本批消息（`N09`），失败保留桶重试；`add_batch` 逐项核对
`added[].error`，不把部分失败当成功。

### 3.3 工具调用（memory_call）

```
模型调用 memory_call
  ▼ plugin-v3.js L1362
  ├─ TOOLS_ENABLED? / memoryReadAllowed(agent)?
  ├─ layer=index  → memoryIndexLayer  → POST /v1/memories/search（只回标题/命中线索）
  ├─ layer=memory → memoryLayer       → POST /v1/memories/search（提炼正文，默认）
  └─ layer=source → readSourceWindow  → GET /v1/memories/<id>/source（≤800 字 + 游标）
                                       └─ 文本包 [不可信数据-记忆原文 开始/结束]
```

### 3.4 Web 面板读取注入详情

```
浏览器 client.js
  ▼ fetch('/mem-api/v1/context/injection?session_id=...')
dsh-web 进程内 web-plugin/index.js  L1738
  ├─ loadSessionEventsDetailed(sessionId)                  L1450
  │    ├─ ① ctx.get('agents').get(id).session.snapshotEvents()（live）
  │    ├─ ② ctx.get('sessionPersistence').open(id,'read').read(0)（官方解析）
  │    └─ ③ 读 $DSH_HOME/sessions/**/session.v*.jsonl.zstd（node:zlib zstd，逐帧解压）
  ├─ extractRuntimeContext / extractSystemPrompt / extractRequestHeader
  ├─ inspectPluginBudget()  ← 读 plugin-v3.js 源码正则抠 2600/+300
  └─ buildInjectionReport → JSON（segments / total_chars / budget / flags）
```

### 3.5 三次"跨模块"事实

1. **plugin-v3 与 web-plugin 之间没有任何直接调用**；它们只通过 ① memory-server 的库
   ② 会话日志文件 ③ DSH 会话对象 间接对齐。
2. **注入文本的唯一权威副本在会话日志**（`runtime-context`/`contexts`），
   插件的 `memoryCache` 是过程态，Web 读不到。
3. **配置读取方向是单向的**：插件 → memory-server `/v1/settings/*`；
   Web → memory-server `/v1/config*`。同一份 `settings` 表，两个读写面。

---

## 4. 端口 / 文件 / 环境变量清单

### 4.1 端口

| 端口 | 绑定 | 用途 |
|---|---|---|
| 3081 | 127.0.0.1 | dsh-web（GUI + `/mem-api`） |
| 6230 | 127.0.0.1 | 生产 memory-server |
| 6240 | 127.0.0.1 | 测试 memory-server |
| 6260 | 127.0.0.1 | 生产 literature-server（非 deepmemory） |
| 6263 | — | 测试 literature |

### 4.2 文件

| 路径 | 读/写方 | 用途 |
|---|---|---|
| `/www/dsh/home/.dsh-memory-api-token` | plugin-v3、web-plugin、kb-query | Bearer token（**客户端侧**） |
| `/www/deepmemory-v063-deploy/memory-server/data/api-token` | memory-server | Bearer token（**服务端侧**，`_ensure_api_token` 缺失即生成 0600） |
| `/www/deepmemory-v063-deploy/memory-server/data/memory.db` | memory-server | SQLite 主库（约 289 MB） |
| `.../data/memory.faiss` | memory-server | FAISS `IndexIDMap(IndexFlatL2)` |
| `.../data/dim.json` | memory-server | 向量维度/指纹 |
| `.../data/info/<domain>.db` | info_store | 域级信息注册表 |
| `.../data/backups/` | memory-server | 备份目录 |
| `.../config_schema.json` | memory-server、web-plugin | 配置中心 schema |
| `/www/dsh/home/storages/workspace.json` | plugin-v3、web-plugin | 会话→workspace 归属 |
| `/www/dsh/home/storages/session_projcache.json` | web-plugin | 会话标题缓存 |
| `/www/dsh/home/sessions/<project>/<session>/session.v*.jsonl.zstd` | web-plugin、DSH | 会话日志（zstd 分帧） |
| `/www/dsh/home/profiles/web/cordis.patch.yml` | DSH、web-plugin | 运行时插件/预设声明 |
| `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | DSH | **生产插件代码** |

> 两个 token 文件内容当前相同（sha256 前 12 位一致），但**是两份文件**，必须同步。
> 本文档不记录 token 字面值。

### 4.3 环境变量

| 变量 | 作用域 | 默认 | 说明 |
|---|---|---|---|
| `MEMORY_SERVER_PORT` | plugin-v3、web-plugin、server | `6230` | 端口 |
| `MEMORY_API_TOKEN_FILE` | 插件/CLI/server | 见上 | token 路径 |
| `DSH_HOME` | 全链路 | `/www/dsh/home` | 会话/预设/存储根 |
| `HOME` | 插件 | — | token 第三 fallback |
| `MEMORY_ALLOW_PRIVATE_EMBEDDING` | server | — | 允许私网 embedding 地址 |
| `EMBED_API_KEY` | server | — | embedding provider key（systemd 注入） |
| `EMBED_API_BASE` / `EMBED_MODEL` | server | — | 远程 embedding 配置 |
| `HF_ENDPOINT` | server | `https://hf-mirror.com` | 模型下载镜像 |
| `MEMORY_TRUSTED_USER_ID` | server | `dsh-user` | 敏感审批受信主体 |
| `MEMORY_MAX_BODY_BYTES` | server | 10 MB | 请求体上限 |
| `MEMORY_REQUEST_TIMEOUT_SECONDS` | server | 30 | 请求超时 |

---

## 5. 隐藏耦合与风险

> 标注：✅ = 本次已核实；⚠️ = 推断（见 §8）。

### 5.1 ✅ `INJECT_BUDGET_CHARS` 硬编码在插件里，且被 Web 插件"读源码"耦合

- 生产插件 `L153`：`const INJECT_BUDGET_CHARS = 2600`，注释写明"token 预算 1-2K"。
- memory-server 的配置段有 `injection`，但**没有** `budget_chars` / `inject_budget` 键
  （`config_schema.json` 一级段核对；`grep INJECT_BUDGET` 在 `.py` 中 0 命中）。
- 更隐蔽的是 Web 端：`web-plugin/index.js:786-793` 直接
  `fs.readFileSync(plugin-v3.js)` 用正则 `/INJECT_BUDGET_CHARS\s*=\s*(\d+)/` 抠数值，
  再用 `/INJECT_BUDGET_CHARS\s*\+\s*(\d+)/` 抠 `+300`。**改常量名或改写法，Web 显示会静默回落到默认 2600/300。**
- 客户端文案也明说："INJECT_BUDGET_CHARS lives in the preset plugin, not memory-server"
  （`client.js:1161-1162`）。

**风险**：预算不是配置而是源码常量；未来按模型窗口动态调预算必须改代码 + 重启 dsh-web。

### 5.2 ✅（方案 A 待落地）`has_memory_plugin` 依赖 preset 行判断

- **线上版** `index.js`（83166 B）判定（实测行号 `1576` / `1596` / `1623`）：
  `const memoryPluginRow = presetInfo.rows.find(...)`，
  第 1579 行用 `!memoryPluginRow` 直接判 `effective=false`，
  第 1623 行 `has_memory_plugin: !!memoryPluginRow`。
  这假设"记忆插件挂在 preset 里"。方案 A 把它迁到 profile/root 级后，
  preset 行不再存在 → 所有记忆会话会显示"未挂载"、`effective=false`。
- 仓库版已修：`loadHostPlaneRows` + `findMemoryPluginRow` + `MEMORY_PRESET_IDS`（`index.js:609-648`），
  并补了 frame0 header 回读（`index.js:1574-1596`）。**但尚未部署**（见 §0.3）。

### 5.3 ✅ `defineTool` 硬编码全局路径（部分文件仍存在）

| 文件 | 行 | 写法 | 状态 |
|---|---|---|---|
| live `_memory-plugin/plugin-v3.js` | 22–48 | 多候选 `import()` + 降级 | ✅ 已修（N25） |
| 仓库 `memory-plugin/plugin-v3.js` | 22–48 | 多候选 | ✅ 已修 |
| 仓库 `_memory-plugin/plugin-v3.js` | 18 | **静态** `import { defineTool } from '/usr/local/node/.../dsh-tools/lib/index.js'` | ❌ 未修 |
| live `_literature-plugin/plugin-v1.js` | 19 | **静态**同一路径 | ❌ 未修 |
| `_literature-kb-plugin/plugin-v1.js` | 19 | **静态**同一路径 | ❌ 未修 |
| `memory-plugin/plugin-v2.js` | 18 | **静态**同一路径 | ❌ 旧版 |

**风险**：该路径绑定 `/usr/local/node` 安装根。换机 / 升级 / 清理旧安装后，
**整个 preset 加载失败**，不只是工具不可用——这是 N25 注释里写的教训。
memory 侧已修，**literature 侧仍在雷上**。

### 5.4 ✅ 四份插件副本 + 两份 server，无单一事实源

- 插件：live 1620 / 暂存 1751 / 仓库 1155 / 旧仓库 849。功能集不同（child_memory、memory_call、
  N25、计划 A 只在部分副本里）。
- server：生产 4057 / 仓库 4366，**双向分叉**（生产有 P12，仓库有 v0.4 断言）。
- 后果：任何"按仓库读代码得出的结论"都可能与线上不符；本文档因此以 live 为准并标出差异。
- 仓库 `agent-preset/agent.cordis.yml:264` 仍指向 `./memory-plugin/plugin-v3.js`（旧副本），
  与 live patch 的绝对路径不一致。

### 5.5 ✅ 配置 `server_url` 可把测试实例指向生产

`loadConfig()`（live `L257-262`）读 `deepmemory.server_url` 并**直接覆盖** `SERVER`。
测试机 6240 的 settings 曾被写成 `http://127.0.0.1:6230`（生产），
导致"测试机验证"实际写生产库。`docs/n17-session-config-plan.md` / `docs/deepmemory-fix-delivery.md`
均有记录。**当前生产 `server_url` 已核为 `http://127.0.0.1:6230`（正确）**，但该键仍可被 POST 覆盖。

### 5.6 ✅ `topic_id` 是 session id 的前 12 位，存在碰撞风险

live `L706`（读取）/ `L844`（写入）：

```js
topic_id: String(sid).slice(0, 12)          // 写入
GET /v1/topic-summaries?topic_id=<sid 前12位>  // 读取
```

如果两个会话 id 前 12 个字符相同（同一前缀批量生成时可能），摘要链会串。
另外带不带 `session-` 前缀会得到不同 topic_id，子 agent/父会话切换时容易错位。⚠️ 是否实际发生过未验证。

### 5.7 ✅ 子 agent 判定依赖头字段，且"失败即按子 agent"

`isDelegatedChild` / `isDelegatedSession`（live `L327-357`）在 header 读不到时 `return true`
（fail-closed）。2026-10-17 的注释记录了一个真实反例：误用 `agent` 访问器读 header 会
把主会话判成子 agent，静默关掉折桶与抽取。**这套判定的正确性完全依赖 DSH header 契约**。

### 5.8 ✅ 工具面裁剪有两条路径，语义不同

- `toolFilter.deny`（preset 配置）只覆盖 `tool-subagent` 派生的子 agent，
  **覆盖不到 team 成员**；
- `agent/created` 里 `agent.ctx.tools.restrict({deny:[...]})`（live `L1602-1614`）补上 team/ralph/one-shot。

两处名单必须同步，否则会出现"某个派发路径仍能写记忆"。

### 5.9 ⚠️ 写入面与读取面的 scope 语义不完全对称

- 写：`add_memory` 有"dialog_scoped 且 scope=workspace → 回落 session"的兜底（server `L1502-1507`）。
- 读：`scope_allows` 对未知 scope 的默认行为、`session_id`/`workspace_id` 的 OR 语义，
  在仓库版与生产版可能不同（生产无 v0.4 门禁）。
  这是"记忆串会话"类问题的常见根因，需要单测覆盖。见 `docs/codex-review.md` 第 2 条。

### 5.10 ✅ 两个 token 文件必须同步

服务端读 `DATA_DIR/api-token`，客户端读 `$DSH_HOME/.dsh-memory-api-token`。
当前内容一致（hash 相同），但**没有同步机制**。轮换 token 时只改一处 → 全链路 401。

---

## 6. 可插拔点（为换压缩策略留位）

> 目标：把"检索 / 注入拼装 / 压缩摘要"从流程代码里抽成接口，
> 将来接 billion-context 那类**增量可逆压缩**时只换实现，不动钩子。

### 6.1 现状：三个逻辑块都写死在流程里

| 逻辑块 | 现在在哪 | 耦合点 |
|---|---|---|
| 检索 | `refreshMemoryCache` live `L629-741` + server `search_memories` | 插件里写死"三类并行 + bias + L3 + 摘要链"；server 里写死 RRF+加权 |
| 注入拼装 | `formatMemories` live `L445-496` + `L723` 截断 + `assemble` `L770-814` | 文本格式、分组、2600 预算、GUIDE 全在插件 |
| 压缩/摘要 | **没有独立实现**：跟随 DSH `compaction/summary` 事件落 `topic_summaries`（live `L823-855`）；读取在 `L700-712` | 只存"摘要文本"，没有可逆展开句柄 |

### 6.2 建议的三个接口（草案）

```js
// ① 检索：多路召回 → 融合 → 过滤，返回统一候选
interface Retriever {
  name: string
  recall(ctx: { query, sessionId, workspaceId, k, filters }): Promise<Candidate[]>
  // Candidate: { id, content, score, importance, type, scope, tier, source_ref }
}

// ② 注入拼装：把候选渲染成文本段，自己声明成本
interface InjectionStrategy {
  name: string
  build(ctx: {
    candidates: Candidate[],
    card: object | null,
    summaries: SummaryHandle[],
    budgetChars: number,          // ← 来自 server settings，不再是常量
  }): Promise<{ text: string, segments: Segment[], cost: { chars, tokensEst } }>
  // Segment: { kind: 'card'|'summary'|'memory'|'rule'|'guide', text, refs }
}

// ③ 压缩/摘要：可增量、可逆、带句柄
interface Compressor {
  name: string
  // 输入一段将被压缩的历史，输出摘要 + 可展开句柄
  compress(ctx: {
    sessionId: string,
    range: { fromSeq: number, toSeq: number },
    content: string,
    budgetChars: number,
  }): Promise<{
    summary: string,
    handle: string,               // 供 memory_call / 工具展开
    reversible: boolean,
    lostTokensEst?: number,
  }>
  expand(handle: string, window?: { cursor?: string, maxChars?: number }): Promise<string>
}
```

### 6.3 落点（最小改动）

| 要做的事 | 落点 | 不改什么 |
|---|---|---|
| 预算可配置 | 在 server `config_schema.json` 的 `injection` 段加 `budget_chars`；插件 `loadConfig` 读它；`inspectPluginBudget` 改为读 server 而非源码 | 不动 2600 的默认语义 |
| 检索可插拔 | `refreshMemoryCache` 里的"三类 + bias + L3"抽成 `Retriever[]`，循环调用后统一 `formatMemories` | 不改 server 的 `/v1/memories/search` 契约 |
| 注入可插拔 | `formatMemories` + 截断 + GUIDE 合并为 `InjectionStrategy.build()`；`assemble` 只调用它 | 不改 `contexts` 按名替换的机制 |
| 压缩可插拔 | `compaction/summary` 事件处调用 `Compressor.compress()`，把 `handle` 存进 `topic_summaries`（**新增列**，不动现有 `summary`）；读取侧 `memory_call` 增 `layer='expand'` | 不改 DSH 压缩本身；不重启即可灰度（新列可空） |
| 反向兼容 | `topic_summaries` 现有行 `handle` 为空 → 走旧路径 | 不迁移历史数据 |

> 与 billion-context 的关系：它的核心是"分段 + 增量 + 可逆"。本仓库目前**没有任何相关代码**
> （全仓库 `billion-context` 0 命中），因此 §6.2 是**为它预留的接口**，不是已实现能力。

### 6.4 不建议现在动的地方

- 不要为了"可插拔"改 `systemPrompt.context` + `assemble` 双写机制——它是 0.1.5 兼容的
  核心修复（旧代码改 `sections` 恒不匹配，注入滞后一次），拆开容易回归。
- 不要把 `memoryCache` 暴露给 Web 插件——跨模块共享会破坏"回合内冻结"。

---

## 7. 架构优化建议（先理清，再动手）

按"风险 × 改动成本"排序：

| 优先级 | 建议 | 理由 | 是否需重启 |
|---|---|---|---|
| P0 | 收敛副本：确定 live 为唯一事实源，仓库副本改为**部署快照 + 回灌脚本** | §5.4；当前无法从仓库判断线上行为 | 否（仅文件） |
| P0 | 修 literature 侧两处静态 `defineTool` import | §5.3；换机会炸整个 preset | 是（改插件代码） |
| P1 | 把 `INJECT_BUDGET_CHARS` 迁到 server `injection.budget_chars` | §5.1；Web 正则耦合一并消除 | 是 |
| P1 | 方案 A 落盘（已暂存并测试） | §5.2；否则 preset 行判定永远错 | 是 |
| P1 | token 单一来源（客户端读 server 的 token 路径，或反过来） | §5.10 | 是 |
| P2 | `topic_id` 改用完整 session id 或 hash | §5.6 | 是（需兼容旧行） |
| P2 | 抽出 `Retriever` / `InjectionStrategy` 接口 | §6 | 是 |
| P3 | 接 `Compressor`（可逆压缩） | §6.3；先加列、灰度 | 是（但可空列先行） |

---

## 8. 不确定项（推断，未实测）

1. ~~**生产 `deepmemory.server_url` 当前值**未核对。~~ **已核对**：settings 表为
   `http://127.0.0.1:6230`（生产自指），不存在测试指向生产的问题。
   风险仍在：该键**可被 POST 覆盖**，测试机历史上就踩过（§5.5）。
2. **`MEMORY_SERVER_PORT` 是否在 dsh-web.service 中显式设置**未核对；若未设置，
   plugin-v3/web-plugin 都默认 6230（与生产一致），但测试机必须显式设置。
3. **`topic_id` 12 位截断是否已造成实际串链**未验证（只是代码层风险）。
4. **方案 A 暂存包是否会在近期落盘**未知；本文档把它标为"未生效"，不假设已上线。
5. **仓库 `memory-plugin/plugin-v3.js`（1155 行）与 live（1620 行）的 597 行差异**只做了
   特征级比对（child_memory/memory_call/readSourceWindow/N25/工具裁剪），未逐行 diff；
   不排除还有其它行为差异。
6. **`server.py` 双向分叉的完整差异**只核对了断言段、P12 段和函数行号；未逐段对齐。
   若要做"生产=仓库"合并，需要单独做一次三方 diff（生产 / 仓库 / 测试机 6240）。
7. **`_literature-plugin` 在 live 是否真的被加载**：`cordis.patch.yml:958` 附近有行，
   但本次未核对运行日志；只确认了源码里的静态 import。
8. **`server.py` 的 `sensitive.py`/`info_store.py` 版本**：生产目录与仓库同名文件
   `sensitive.py` 均为 4346 B（同大小），未比 md5；`info_store.py` 均为 3799 B。

---

## 9. 附：本次核实用到的命令（可复现）

```bash
# 副本谱系
md5sum /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js \
       "/www/deepseek harness workspace/dsh-deepmemory/agent-preset/memory-plugin/plugin-v3.js" \
       "/www/deepseek harness workspace/dsh-deepmemory/agent-preset/_memory-plugin/plugin-v3.js"
wc -l /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js \
      /www/deepmemory-v063-deploy/memory-server/server.py \
      "/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py"

# 生产挂载点
grep -n "plugin-v3" /www/dsh/home/profiles/web/cordis.patch.yml

# 生产进程
ls -l /proc/2662895/cwd     # → /www/deepmemory-v063-deploy/memory-server
ss -tlnp | grep -E ':(3081|6230|6240|6260)\b'

# 隐藏耦合
grep -n "INJECT_BUDGET_CHARS" /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js
grep -n "INJECT_BUDGET_CHARS" "/www/deepseek harness workspace/dsh-deepmemory/web-plugin/index.js"
grep -rn "defineTool" "/www/deepseek harness workspace/dsh-deepmemory/agent-preset/" | grep import

# token 文件同一性（只比 hash，不打印值）
sha256sum /www/dsh/home/.dsh-memory-api-token \
          /www/deepmemory-v063-deploy/memory-server/data/api-token
```

---

## 附录 Z：权威版本声明（P0-2，2026-10-21）

> 目的：此前同一构件存在 3–4 份副本，长期靠"部署时手工复制保持逐字节一致"，
> 结果各自漂移（1155 / 849 / 1620 三版并存，缺 `memory_call` / `child_memory`）。
> 本节把「谁是权威、仓库怎么跟」固化成规则，避免下一次再分叉。

### Z.1 `plugin-v3.js`：live 权威 → 仓库单向镜像

| 角色 | 绝对路径 | 行数 | md5 | 状态 |
|---|---|---|---|---|
| **权威（live）** | `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | **1620** | `1b32cd85f855182e481f0ea827293afd` | ✅ 线上真实加载 |
| **仓库镜像** | `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/memory-plugin/plugin-v3.js` | 1630（10 行镜像声明 + 1620 正文） | 正文 md5 = `1b32cd85…` | ✅ 唯一仓库副本 |
| DEPRECATED | `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/_memory-plugin/plugin-v3.js` | 849 + 10 行声明 | 原 `ed28d8b2…` | ⚠️ 仅因 `daily`/`task` preset 仍引用而保留 |
| 未部署暂存 | `/www/deepseek harness workspace/planeA-prod-stage-20261018/files/plugin-v3.js` | 1751 | `8cd3f75d…` | ⏳ 方案 A 快照，不是权威 |

规则：

1. **只允许 live → 仓库单向回灌**；禁止在仓库副本上单独改逻辑（会被下一次回灌覆盖）。
2. 回灌后必须验证「仓库正文 == live」：`tail -n +11 <仓库镜像> | md5sum` 必须等于 live 的 md5。
3. 回灌后必须过 ESM 冒烟：`cp <文件> /tmp/chk.mjs && node --check /tmp/chk.mjs`。
4. 仓库镜像文件头已写明权威路径与基线 md5；md5 变了说明仓库又落后，重跑回灌。

### Z.2 `server.py`：双向分叉，生产是权威，**不合并**

| 角色 | 绝对路径 | 行数 | 大小 | md5 | 说明 |
|---|---|---|---|---|---|
| **权威（生产）** | `/www/deepmemory-v063-deploy/memory-server/server.py` | **4057** | 177274 | `652c345f79041964940b6f35e36c559c` | 线上 `dsh-memory-server.service` :6230 真在跑；库为 **schema v9** |
| 仓库副本 | `/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py` | 4366 | 191856 | `140b463c6753f1eb87d231ecbdccc00a` | 与 `/www/dsh-test-memory/server.py` 逐字节相同（测试机 :6240） |

**方向性结论（不许模糊）**：

- 生产文件 = 线上真实行为，**是权威**；仓库文件 = 未来候选，**含未部署特性**。
- 两份差异**不是待修的落后**，而是**双向分叉**（例如 v0.4 assertions 模型生产没有）。
  因此**禁止**：把仓库版本直接覆盖生产、或用仓库版本"修"生产。要动生产必须走
  AGENTS.md 的两阶段：测试机(3091/6240) → 生产(3081/6230)。
- 硬证据（本次克隆验证时实测）：用仓库/测试机那份 `server.py` 启动**克隆来的生产库**，
  日志打印 `migration: schema v10 已应用`；而生产库仍是 **v9**。
  说明仓库版本确实带 schema 迁移，**回灌生产会改动生产库结构** —— 必须先测试机验证。
- 仓库文件头已加同等声明；`memory-server/` 若将来加 README，必须复制本节结论。

### Z.3 这次改了什么（可回滚）

- 回灌：`agent-preset/memory-plugin/plugin-v3.js`（1155 → live 1620 + 10 行镜像声明）。
- 降级标注：`agent-preset/_memory-plugin/plugin-v3.js` 头部加 DEPRECATED（文件保留，未删除）。
- 声明：`memory-server/server.py` 头部加版本声明；本节 + 文首 ⚑ 权威声明。
- 备份目录：`/www/clone-prod-to-test-20261021-184840/repo-backup-p0-2-20261021-184840/`
  - `memory-plugin.plugin-v3.js.orig`（md5 `a215b7db…`，1155 行）
  - `_memory-plugin.plugin-v3.js.orig`（md5 `ed28d8b2…`，849 行）
  - `server.py.orig`（md5 `140b463c…`，4366 行）
  - `ARCHITECTURE.md.pre-p0-2`（md5 `c8241312…`，56300 B —— 加本节之前的完整原文）
- 回滚（三个已被 git 跟踪的文件）：`git checkout -- agent-preset/memory-plugin/plugin-v3.js agent-preset/_memory-plugin/plugin-v3.js memory-server/server.py`
- 回滚（未被 git 跟踪的两个文件）：
  `cp <备份目录>/ARCHITECTURE.md.pre-p0-2 docs/ARCHITECTURE.md`；
  `agent-preset/_memory-plugin/README.md` 无独立备份，回滚=删掉其中「P0-2 起：单一事实源已收敛」一节、恢复原 7 行版本。
