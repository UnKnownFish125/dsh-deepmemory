# deepmemory 来源与出处（PROVENANCE）

> **文档状态**：现行（2026-10-21 首次成文）
> **性质**：只读梳理，不修改任何代码。
> **路径基线**：全部绝对路径。
> **判定规则**：
> - **自研** = 本仓库实现，未在代码/注释中声明外部来源；
> - **借鉴** = 代码注释或文档明确声明"对齐/参考"了某项目，但**未逐行移植**；
> - **第三方** = 直接 `import` / 依赖的库或模型；
> - **参考未采纳** = 在仓库中**搜不到任何引用**，仅作为候选方案存在。

---

## 1. 仓库自身许可

| 项 | 值 | 证据 |
|---|---|---|
| 仓库许可证 | **GNU AGPL-3.0** | `/www/deepseek harness workspace/dsh-deepmemory/LICENSE` 第 1–3 行 |
| 许可证文件大小 | 34523 B | `ls -l` |
| 影响 | 网络服务化分发需提供源码；衍生作品需同许可 | AGPL §13 |

> 注意：仓库内**没有** `NOTICE` / `THIRD_PARTY_LICENSES` 文件。本文档 §4 的清单是本次
> 按实际 `import` 反推的，不是项目原有合规文件。

---

## 2. 组件级来源

| 组件 | 绝对路径 | 来源判定 | 说明 |
|---|---|---|---|
| memory-server 主体 | `/www/deepmemory-v063-deploy/memory-server/server.py` | **自研 + 借鉴 AstrBot** | 文件头第 4–13 行明确写 "Aligned with AstrBot living memory's storage/retrieval strategy"，并列出 documents/atoms/graph/FAISS/BM25/RRF/加权/本地 embedding 七项 |
| v2 事务域模型 | `.../memory-server/v2_domain.py` | **自研** | 文件头声明"deliberately depends only on Python's standard library"，未提外部来源 |
| 敏感检测/脱敏 | `.../memory-server/sensitive.py` | **自研** | 无 DB/HTTP 依赖；Luhn、证件、密钥模式为通用实现，未见外部声明 |
| 域级信息注册表 | `.../memory-server/info_store.py` | **自研** | 文件头引"契约 §11"，属本项目设计 |
| CLI 查询工具 | `.../memory-server/kb-query.py` | **自研** | 对本机 HTTP API 的薄封装 |
| preset 插件 | `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | **自研** | 依赖 DSH API；N25 注释是项目内事故复盘 |
| Web 宿主插件 | `.../web-plugin/index.js` | **自研** | `/mem-api` 反向代理 + 日志解析 |
| Web 客户端 | `.../web-plugin/client.js` | **自研** | React（宿主提供）+ 原生 `fetch` |
| 状态卡 / 任务板状态机 | plugin-v3 live `L899-1140` + `v2_domain.py` | **自研** | 任务状态图 `draft→planned→todo→in_progress→review→completed/failed`，BFS 最短合法路径 |
| 三层按需读取（P0/P1/P2） | plugin-v3 live `L1199-1360` | **自研** | `index/memory/source` 三层 + 原文窗口游标 |

### 2.1 「AstrBot living memory」借鉴的具体范围

代码注释只声明"对齐策略"，**没有**声明复制代码。可对应的实现点：

| AstrBot living memory 概念 | 本仓库落点 | 证据 |
|---|---|---|
| documents（摘要/关键事实/人格摘要） | `documents` 表 | `server.py:340` |
| atoms（细粒度事实 + TTL/decay） | `atoms` 表 | `server.py:382` |
| graph（实体-关系层） | `graph_nodes` / `graph_edges` | `server.py:401-414` |
| FAISS + BM25 双路召回 | `vector_search` / `BM25` | `server.py:1222` / `1044` |
| RRF 融合 | `rrf_fuse` `RRF_K=60` | `server.py:1315` |
| `alpha*rel + beta*importance + gamma*recency` | `ALPHA,BETA,GAMMA=0.5,0.25,0.25` | `server.py:88` |
| 检索缓存 | `search_cache.*` + 语义代际 | `server.py:1403-1417` |
| 访问强化（召回延长 TTL） | `access_reinforcement.*` | `server.py:1466-1484` |

> **未核实**：AstrBot 的具体版本、许可证、以及是否存在逐行同源代码。
> 本仓库 `LICENSE` 为 AGPL-3.0；若 AstrBot 亦为 AGPL-3.0 则方向兼容，但**本次未核对**。

---

## 3. 关键算法来源（通用算法，非本项目原创）

| 算法 | 出处 | 本仓库实现 | 备注 |
|---|---|---|---|
| **RRF**（Reciprocal Rank Fusion） | Cormack, Clarke & Büttcher, SIGIR 2009 | `server.py:1315` `rrf_fuse`，`k=60` | 多路召回融合的标准做法 |
| **BM25 / Okapi BM25** | Robertson & Zaragoza, 2009 | `server.py:1044` `class BM25` | 中文用 `jieba` 分词（`server.py:1033`） |
| **FAISS 近似/精确检索** | Johnson, Douze & Jégou, 2019（FAISS） | `IndexFlatL2` + `IndexIDMap` | 精确 L2；非 IVF/HNSW |
| **bge 系列 embedding** | BAAI（北京智源） | 通过 `fastembed` 或远程 API | 见 §4.3 |
| **Luhn 校验** | 通用算法（ISO/IEC 7812） | `sensitive.py` `_luhn_ok` | 身份证/卡号识别 |
| **指数衰减** | 通用记忆衰减模型 | `_run_decay` / `atoms.expires_at` | 参数 `decay_rate=0.01` |
| **状态机最短路径（BFS）** | 通用图算法 | plugin-v3 live `L1048-1072` `taskPath` | 仅用于任务状态迁移 |

> 这些算法**不构成第三方代码依赖**；实现是本仓库自己写的（或调库）。

---

## 4. 第三方依赖清单（按实际 import / 声明）

### 4.1 Python（memory-server，运行于 `/opt/AstrBot/venv`）

| 包 | 版本（实测） | 许可证 | 用途 | 证据 |
|---|---|---|---|---|
| `faiss-cpu` | 1.14.3 | MIT（Meta） | 向量索引 | `server.py:56` |
| `numpy` | 2.3.5 | BSD-3-Clause | 向量运算 | `server.py:57` |
| `jieba` | 0.42.1 | MIT | 中文分词（BM25） | `server.py:1033` |
| `fastembed` | 0.8.0 | Apache-2.0 | 本地 embedding（**动态导入，可选路径**） | `server.py:216` |
| `onnxruntime` | 1.27.0 | MIT | fastembed 后端 | 由 fastembed 带入 |
| `zstandard` | 0.25.0 | BSD-3-Clause | 仅脚本/工具用（`tools/check_cache_health.py:18`、`scripts/fix-session-roles.py:11`） | **server.py 未 import** |
| `PyYAML` | 6.0.2 | MIT | 工具/脚本 | — |

> `zstandard` **不在 memory-server 运行时**：Node 侧 `zlib.zstdDecompressSync` 是 Node 内置，
> 不依赖 python 包。不要把 `zstandard` 写进服务依赖。

### 4.2 Node / Web

| 包 | 版本约束 | 许可证 | 用途 | 证据 |
|---|---|---|---|---|
| `@deepseek-ai/dsh-settings` | `>=0.1.0-rc.6 <0.2`（peer） | 以包内 LICENSE 为准（未核对） | 设置 API（0.1.7 起不再 `register`） | `web-plugin/package.json` |
| `@deepseek-ai/schemastery` | `^3.18.1`（peer） | **MIT**（`LICENSE` 第 1 行 "MIT License"） | schema 校验 | 同上 |
| `@deepseek-ai/dsh-tools` | 宿主提供 | 以 DSH 包内 LICENSE 为准（未核对） | `defineTool` | plugin-v3 `L22-48` |
| `node:zlib` | Node 内置 | Node.js 许可 | zstd 解压会话日志 | `web-plugin/index.js:9, 996-1074` |
| React | 宿主注入（`require('react')`） | MIT | UI | `client.js:8` |

> Web 插件**没有** `dependencies`（`package.json` 只有 `peerDependencies`），
> 运行时全部依赖宿主 dsh-web 提供。

### 4.3 模型

| 模型 | 维度 | 用途 | 来源/许可 | 当前是否启用 |
|---|---|---|---|---|
| `bge-m3` | 1024 | **生产实际 embedding** | BAAI，MIT（模型卡） | ✅ `dim.json = {"dim":1024,"fp":"api:bge-m3"}`；settings `embedding.provider="api"`、`api_base_url="http://127.0.0.1:6192/v1"` |
| `BAAI/bge-small-zh-v1.5` | 512 | 本地 embedding 默认值 | BAAI，MIT | ❌ 仅配置默认值（`embedding.local_model`），当前未用 |

> **重要事实**：`server.py` 头部注释写 "local embedding: fastembed BAAI/bge-small-zh-v1.5 (512-dim)"，
> 且常量 `DIM = 512`；但生产 `data/dim.json` 是 **1024 维 / api:bge-m3**。
> 检索维度以 `dim.json` + `settings` 为准，**不要按注释或 `DIM` 常量推断**。

### 4.4 命令行/脚本依赖

| 工具 | 依赖 | 用途 |
|---|---|---|
| `tools/migrate_embedding.py` | `fastembed` / HTTP API | 换 embedding 模型/维度 |
| `tools/check_cache_health.py` | `zstandard` | 检查会话缓存 |
| `tools/gen-preset-017.py` | PyYAML | 生成 0.1.7 preset 片段 |
| `scripts/fix-session-roles.py` | `zstandard` | 修会话日志 role 雷 |
| `scripts/verify.sh` | shell + node | 部署前校验 |

---

## 5. 参考但**未采纳**的来源

> 判定方法：在 `/www/deepseek harness workspace/dsh-deepmemory` 全仓库
> （`*.md` / `*.js` / `*.py`）搜关键词，**0 命中**。

| 来源 | 关键词检索结果 | 状态 | 说明 |
|---|---|---|---|
| **billion-context** | `billion-context` / `billion_context`：**0 命中** | **参考未采纳** | 其"增量可逆压缩"思想在本仓库无实现。`ARCHITECTURE.md §6.2` 的 `Compressor` 接口是**为它预留的位置**，不是已有能力 |
| **dsh-evil-mcp** | `evil-mcp` / `evil_mcp`：**0 命中** | **参考未采纳** | 无代码、无文档引用 |
| **reconcile-agent-memory** | `reconcile-agent` / `reconcile_agent`：**0 命中** | **参考未采纳** | 无代码、无文档引用 |

> 结论：这三项**目前都不在 deepmemory 的实现或依赖里**。如果后续要引入，
> 需先补一份"接口对齐 + 许可证 + 数据兼容"评估，不要直接说"已借鉴"。

### 5.1 已在文档中提及、但属于**独立项目**的相邻来源

| 项目 | 关系 | 证据 |
|---|---|---|
| `dsh-literatum` / `dsh-literature` | 独立服务（6260），独立仓库；deepmemory 只通过导出契约对接 | `docs/dsh-literatum-contract.md`、`docs/plan-literature-side.md` |
| `dsh-workbench` | 规划中的独立服务（6270/6271） | `docs/workbench-plan.md:178` |
| `dsh-synapse` / `OpenViking` / `mattpocock/skills` | workbench 调研参考，**未进入 deepmemory** | `docs/workbench-plan.md:5` |

---

## 6. 来源相关的风险与待补

| 项 | 状态 | 建议 |
|---|---|---|
| 无 `THIRD_PARTY_LICENSES` / `NOTICE` | ❌ 缺失 | 补一份，至少覆盖 §4 的库与模型 |
| AstrBot 借鉴范围未量化 | ⚠️ 仅有注释声明 | 若对外分发，需确认是否存在同源代码与许可证兼容 |
| `@deepseek-ai/*` 的许可证未核对 | ⚠️ | 以各包内 `LICENSE` 为准；不要默认 MIT |
| 模型许可证 | ⚠️ 仅按模型卡常识标注 | `bge-m3` / `bge-small-zh-v1.5` 均标 MIT，商用前请复核模型卡 |
| `kb-query.py` 的 shebang 是 `#!/usr/bin/env python3`，server 是 `#!/opt/AstrBot/venv/bin/python3` | ✅ | 两套解释器；跑 CLI 时注意依赖是否在 PATH 的 python 里 |
| 仓库 `LICENSE` 是 AGPL-3.0，但 README 未声明 | ⚠️ | README 建议加许可证段落 |

---

## 7. 一句话总结

deepmemory 的**主体是自研**：HTTP 服务、v2 事务域、注入/抽取/工具链、Web 面板都是本项目代码；
**检索与记忆分层策略借鉴 AstrBot living memory**（注释声明，非逐行移植）；
**底层依赖是通用库**（FAISS/BM25/jieba/numpy/fastembed，均为宽松许可）；
**billion-context / dsh-evil-mcp / reconcile-agent-memory 尚未纳入**，代码中零引用。
