# deepmemory — DeepSeek Harness 长期记忆系统

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform: DeepSeek Harness](https://img.shields.io/badge/Platform-DeepSeek%20Harness-4c8dff)](https://github.com/deepseek-ai)
[**English**](README.md)

让 DeepSeek Harness 的 agent 拥有**跨会话记忆**与**近乎无限的上下文**。事实、偏好、决定、计划与任务在会话之间持续存在——结构化、可检索、受保护。设计概念对齐 [AstrBot livingmemory](https://github.com/lxfight-s-AstrBot-Plugins/astrbot_plugin_livingmemory)，实现为 DSH 平台的原生移植：Python 记忆后端 + Cordis Web 插件 + 三套 agent preset。

## 核心特性

- **五类记忆模型** — 语义、短期、过程、源归档、压缩归档
- **三级存储** — 活跃 → 冷库 → 归档，自动降级与生命周期衰减
- **默认敏感保护** — PII 与中文自然语言密码检测，写入即脱敏，原文查看需授权，全程审计
- **决策生命周期** — 提案 → 探索 → 待定 → 采纳 / 否决 / 取代 / 失效，冲突剔除显式化，不再被静默覆盖
- **混合检索** — BM25 + 向量 + 图谱三路，RRF 融合，相关性 × 重要性 × 新鲜度加权
- **双域分离** — 工作 / 生活记忆在写入与查询时隔离
- **模式 preset** — 两套生产 preset（任务 / 日常）+ 一套扩展模板
- **WebUI** — 记忆面板、实体图谱、归档、维护、会话级配置、任务看板

## 记忆模型（简述）

```
原始对话 → 记忆条目（分类 + 域 + 来源主体 + 敏感度）
        → 记忆原子（TTL / 衰减 / 强化）
        → 图谱（实体 + 关系边）
```

- **分类**：`semantic` 稳定事实、`short_term` 近期日常上下文、`process` 任务推进细节、`source_archive` 原文切片、`compressed_archive` 整合摘要。
- **分级**：记忆先入 `active`，窗口结束后（默认短期 7 天 / 过程 15 天）降入 `cold`，冷库保留一年后进 `archive`。召回优先活跃、其次冷库。
- **敏感度**：`normal / sensitive / protected / secret` 四级。检测覆盖银行卡（Luhn 校验）、身份证（校验位）、手机号、API key/token 与中文自然语言密码。命中即脱敏存储；查看原文需授权（3 次机会、30 分钟过期、审计留痕）。
- **决策**：状态机避免后续对话静默推翻早前决定。被否决的方案自动降级；只有用户能手动将方案标记为 `invalid`。
- **检索**：三路 RRF 检索 + 缓存，支持按域、按来源主体、按敏感度过滤。

## 模式 preset — 为 agent 选择形态

deepmemory 以**三套 preset** 交付，每套都是完整的 `agent.cordis.yml` 配置，按会话选择其一。

| preset | 形态 | 包含 | 不含 |
|---|---|---|---|
| `task` 任务工作模式 | 完整编码 agent | 全部工具、plan 模式、子 agent、workflow、任务看板、过程记忆、预算档案 `task-default` | — |
| `daily` 日常问答模式 | 轻量问答 | 联网搜索、短期记忆、日常状态卡、话题连续性、预算档案 `daily-default` | 任务看板、子 agent 编排、workflow |
| `blank-template` | 扩展模板 | 最小 persona、可选工具注释、`plugin/` 插件入口、preset-local realm 示例、预算注释 | deepmemory 注入、自动抽取、状态卡、任务看板（刻意留空） |

每套 preset 声明**预算契约**：`budget_profile` + `priority_allocation`（组件优先级与 `min_tokens`）。未用预算回流池中按优先级再分配——模型上下文窗口是硬上限，组件在其内协商分配。

## 快速开始

```bash
git clone https://github.com/UnKnownFish125/dsh-deepmemory.git && cd dsh-deepmemory
sudo bash scripts/install.sh
```

幂等，可重复执行。脚本安装记忆后端（`memory-server`，systemd unit，健康检查）、Web 插件（`dsh-deepmemory` bundle 注册进 `profiles/web/package.json`，client.js 自动转换为 `__ModuleLoader__` 格式）与 agent preset（写入 `${DSH_HOME}/.agent-presets/`）。脚本**不会**自行重启 DSH Web 进程，只打印重启清单，由管理员执行。

可用环境变量覆盖：`DSH_HOME`、`APP_DIR`、`VENV_PY`。

## 向量嵌入 — 本地模型或 API，二选一

语义检索的嵌入提供方可插拔，配置在 `embedding` 组（WebUI「配置」tab 自动渲染，也可走会话配置 API）：

| 配置键 | 默认值 | 含义 |
|---|---|---|
| `embedding.provider` | `local` | `local`=本机 fastembed 推理；`api`=任意 OpenAI 兼容 `/v1/embeddings` 接口 |
| `embedding.local_model` | `BAAI/bge-small-zh-v1.5` | 本地模型，首次使用自动从 HF 镜像下载 |
| `embedding.api_base_url` | — | `provider=api` 时必填，如 `https://api.openai.com/v1` |
| `embedding.api_key` | — | `provider=api` 时必填；也可用环境变量 `EMBED_API_KEY` 注入，避免明文入库 |
| `embedding.api_model` | `text-embedding-3-small` | API 提供方的模型名 |

**默认模型：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)** — 512 维、中文效果好、ONNX 约 30MB，首次下载后完全离线运行（走 HF 镜像 `hf-mirror.com`，可用 `HF_ENDPOINT` 覆盖）。

切换提供方（如 `local` → `api`）安全无损：FAISS 索引自动按新维度重建，已有记忆按需重新嵌入，**不丢数据**。

## 使用方式

1. **选择 preset** — 新建会话并选择「任务工作模式」或「日常问答模式」。记忆插件自动加载，无需动态 define+run。
2. **WebUI** — 对话页「记忆」tab：记忆管理（作用域/域/标签）、实体图谱、归档管理、维护（备份/重建/整合/衰减）、会话级配置覆盖、任务看板。
3. **API** — HTTP 后端 `:6230`：`POST /v1/memories/add`、`/v1/memories/search`，v2 业务 API 在 `/v1/v2/`（任务、召回、生命周期），状态卡 `/v1/cards/upsert`，会话配置 `/v1/config/session/set|reset`，敏感审计 `/v1/sensitive/audit`。

## 如何扩展

- **子插件（推荐）** — 复制 `agent-preset/blank-template/` 到 `${DSH_HOME}/.agent-presets/<your-preset>/` 并改名，业务代码放进 `plugin/`。模板刻意不包含 deepmemory，从干净起点按需接入记忆注入、状态卡、预算档案。
- **自定义 preset** — 基于 `task/` 或 `daily/` 修改 persona、工具目录与 `budget_profile`；会话状态放在插件代码里，不要写进 preset 文件。
- **记忆类与 API** — `memory-server/v2_domain.py` 是仅依赖标准库的数据契约层，扩展常量（`MEMORY_CLASSES`、`STORAGE_TIERS` 等）与生命周期原语，再在 `server.py` 暴露路由。
- **敏感规则** — 在 `memory-server/sensitive.py` 增删检测规则，单测在 `memory-server/tests/test_sensitive.py`。

## 架构

```
┌─ 写入 ────────────────────────────────┐
│ ① 廉价 LLM 自动抽取（turn-stopping）  │
│ ② memory_save 模型工具（主动写入）    │
│ ③ WebUI 手动录入                     │
└────────────────┬──────────────────────┘
                 ▼
   memory-server（Python，systemd :6230）
   SQLite + FAISS + BM25（jieba）
   RRF 融合 + 三因子加权
   敏感脱敏 / 决策状态 / 生命周期衰减
                 ▼
┌─ 召回注入 ────────────────────────────┐
│ system 消息静默注入                   │
│ [状态卡] + [Top-K 记忆]               │
│ 作用域：session / workspace / global  │
└───────────────────────────────────────┘
```

## 设计依据

- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442) — 记忆流、recency × importance × relevance 检索、反思提炼
- [Memory in the Age of AI Agents: A Survey](https://arxiv.org/abs/2512.13564) — 2025 年 agent 记忆综述
- [HiMem: Hierarchical Long-Term Memory for LLM Long-Horizon Agents](https://huggingface.co/papers/2601.06377) — 分层长期记忆

## 许可

**AGPL-3.0**。设计致谢 [AstrBot livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) 作者 [lxfight](https://github.com/lxfight-s)；本项目是其面向 DeepSeek Harness 的独立原生实现，非代码搬运。修改、衍生与分发（含网络服务提供）须遵守 AGPL 条款。
