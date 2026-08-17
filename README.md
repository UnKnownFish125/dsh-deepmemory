# deepmemory — DeepSeek Harness 长期记忆系统

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform: DeepSeek Harness](https://img.shields.io/badge/Platform-DeepSeek%20Harness-4c8dff)](https://github.com/deepseek-ai)

为 DeepSeek Harness 构建的长期记忆系统：让 agent 拥有**跨会话记忆**与**无限上下文**能力。
设计概念对齐 [AstrBot livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)，实现为 DSH 平台独立移植。

## 功能特性

- **自动记忆抽取**：每轮对话结束后用便宜 LLM 抽取事实/偏好/决定/计划/事件，结构化解构为记忆条目、记忆原子、实体与关系边
- **混合检索召回**：BM25 + 向量 + 图谱三路检索 → RRF 融合 → 相关性/重要性/新鲜度三因子加权；检索缓存（TTL，命中不更新访问）
- **静默注入**：工作区状态卡 + 长期记忆 Top-K 经 systemPrompt 注入（不污染消息流）
- **记忆图谱**：实体节点 + 关系边，Obsidian 风格交互（节点拖拽 / 悬停高亮 / 滚轮缩放 / 平移），点击节点查看关联记忆
- **生命周期管理**：每日重要性衰减（访问强化、保护阈值）、原子 TTL、归档软删除、快照备份、索引重建、记忆整合（可选 LLM 摘要）
- **原文追溯**：记忆条目保留抽取原文切片 + 来源会话 id
- **WebUI**：记忆管理面板（分区/域手动调整、中英双语）、实体图谱、归档管理、维护（备份/重建/整合/衰减）、配置中心（12 组参数）
- **三种运行 preset**：`task` 工作模式、`daily` 日常模式、`blank-template` 第三方子插件开发模板；另有动态插件和 web bundle 形态

## 架构

```
┌─ 写入 ────────────────────────────────────────────┐
│ ① 廉价 LLM 自动抽取（turn-stopping 达阈值触发）    │
│ ② memory_save 模型工具（主动写入）                 │
│ ③ /memory 命令 + 记忆面板（手动）                  │
└──────────────┬────────────────────────────────────┘
               ▼
   memory-server（Python，systemd 常驻 :6230）
   SQLite + FAISS(IndexFlatL2) + BM25(jieba)
   RRF 融合 + α相关性+β重要性+γ时间衰减 加权
   近重复合并 / 每日衰减 / 归档恢复 / persona 字段
               ▼
┌─ 召回注入 ────────────────────────────────────────┐
│ agent/pre-step 静默注入 system 消息                │
│ [工作区状态卡] + [长期记忆召回 Top-K]              │
│ 三层作用域：session / workspace / global           │
└───────────────────────────────────────────────────┘
```

## 组件

| 目录 | 说明 |
|---|---|
| `memory-server/` | Python 记忆后端（SQLite+FAISS+BM25，HTTP API），systemd 服务 `dsh-memory-server` |
| `web-plugin/` | DSH web 层插件 `dsh-deepmemory`：同源代理路由 `/mem-api/*` + 对话页「记忆」视图面板 |
| `agent-preset/` | Agent preset：工作/日常模式方案，以及 `blank-template/` 第三方子插件空白模板 |

## 安装 / 升级（一键，幂等）

```bash
git clone https://github.com/UnKnownFish125/dsh-deepmemory.git && cd dsh-deepmemory
sudo bash scripts/install.sh
```

或下载 [最新 Release](https://github.com/UnKnownFish125/dsh-deepmemory/releases) 的 `deepmemory-vX.Y.Z.tar.gz` 解压安装。install.sh 重复执行安全（幂等）：

1. **memory-server**：同步源码（排除运行时数据，绝不覆盖已部署的 `data/`）→ 写 systemd unit → 重启 → 健康检查
2. **web 插件**：复制 `dsh-deepmemory` → `profiles/web/package.json` 的 bundles 幂等注册 → **client.js 自动转换为 `__ModuleLoader__` 格式**（无需手工）
3. **agent preset**：复制 `harness-memory` 到 `${DSH_HOME}/.agent-presets/`——用该 preset 的会话重启后**自动加载记忆插件**，无需 define+run 动态插件

脚本最后打印 dsh web 重启清单（不自动重启 web，安全交给管理员）。环境变量可覆盖：`DSH_HOME` / `APP_DIR` / `VENV_PY`。

## 部署要点

1. **memory-server**：`/opt/AstrBot/venv/bin/python3 server.py`，端口 6230
   - 依赖：fastembed(bge-small-zh-v1.5, 512维)、faiss-cpu、jieba
   - 模型下载走镜像：`HF_ENDPOINT=https://hf-mirror.com`
   - systemd unit：`dsh-memory-server.service`
2. **web 插件**：放入 `profiles/web/node_modules/dsh-deepmemory/`，
   `profiles/web/package.json` 的 `dsh.profile.bundles` 加入 `"dsh-deepmemory"`
3. **agent preset**：目录放至 `${DSH_HOME}/.agent-presets/harness-memory/`

## 记忆模型（对齐 AstrBot livingmemory 策略）

- 层级：原始消息（sessionPersistence 原生归档）→ 记忆条目（summary/key_facts/persona_summary/canonical_summary）→ 图谱（实体+关系边）→ 记忆原子（TTL/衰减/强化）
- 双域：work（状态卡+决策链+派发）/ life（原子生命周期）
- 检索：BM25 + 向量 + 图谱三路 → RRF → 三因子加权；检索缓存（TTL 45s，命中不更新访问）
- 生命周期：每日重要性衰减（幂等）、访问强化参数组、保护阈值、归档软删除
- 维护：快照备份（create/list/restore/delete）、索引重建（指纹+影子重建）、记忆整合（相似聚合 canonical_summary）、schema 迁移框架（v2：图谱索引）

## HTTP API 摘要

| 端点 | 说明 |
|---|---|
| `POST /v1/memories/add` `add_batch` | 写入（近重复合并、原文保留、原子/实体/关系抽取） |
| `POST /v1/memories/search` | 三路 RRF 检索 + 访问强化 + 缓存 |
| `GET /v1/memories/list` `GET /v1/memories/<id>` `PUT` `DELETE` | 条目管理（分区/域手动调整） |
| `GET /v1/memories/<id>/source` | 原文回查 |
| `GET /v1/graph` | 图谱（节点+关系边） |
| `POST /v1/maintenance/decay` | 衰减（force） |
| `POST /v1/maintenance/consolidate` | 记忆整合 |
| `POST /v1/maintenance/rebuild` | 索引重建 |
| `POST /v1/backups/create` `GET /v1/backups/list` `POST /v1/backups/restore` `DELETE /v1/backups/<name>` | 备份管理 |
| `GET/POST /v1/config-schema` `/v1/config` | 配置中心（10 组 38 项） |

## WebUI（dsh-deepmemory「记忆」视图）

记忆管理（分区/域/标签中文）· 实体图谱 · 归档管理 · 维护（备份/重建/整合/衰减）· 配置中心 · 中英双语切换

## 设计依据

核心概念的理论来源（推荐阅读顺序）：

- **[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)**（Stanford，2023）——记忆流（memory stream）与 **recency × importance × relevance 三因子检索**、反思提炼（reflection）的源头；本系统的加权召回与记忆整合直接源自此
- **[Memory in the Age of AI Agents: A Survey](https://arxiv.org/abs/2512.13564)**（2025）——AI Agent 记忆领域最新综述，覆盖记忆架构、机制与评测基准
- **[HiMem: Hierarchical Long-Term Memory for LLM Long-Horizon Agents](https://huggingface.co/papers/2601.06377)**（2026）——分层长期记忆（条目 → 图谱 → 原子）的最新工作，与本系统的记忆层级设计相互印证

## 致谢与许可

**设计致谢**：本项目的记忆模型分层（原始消息 → 记忆条目 → 图谱 → 记忆原子）、混合检索融合（RRF + 三因子加权）、访问强化、每日衰减、记忆整合、备份迁移等概念体系，来自 **AstrBot 插件 [livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)（作者 [lxfight](https://github.com/lxfight-s)）**。特此致谢原作者的开源工作与设计沉淀。

**实现说明**：deepmemory 是面向 DeepSeek Harness 平台的独立移植——Python 记忆后端（HTTP 服务 + SQLite + FAISS + BM25）与 JavaScript 插件（Cordis 架构）均为独立编写，非代码搬运。

**许可**：本项目以 **AGPL-3.0** 发布，与 livingmemory 相同许可证。任何基于本项目的修改、衍生与分发（含网络服务提供）须遵守 AGPL 条款：保留版权与许可声明、修改开源、向使用者提供完整源码。
