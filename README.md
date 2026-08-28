# deepmemory — Long-term Memory for DeepSeek Harness

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform: DeepSeek Harness](https://img.shields.io/badge/Platform-DeepSeek%20Harness-4c8dff)](https://github.com/deepseek-ai)
[**中文版**](README.zh-CN.md)

Give your DeepSeek Harness agents **cross-session memory** and a **near-infinite context**. Facts, preferences, decisions, plans and tasks survive across sessions — structured, searchable and protected. Implemented natively for DSH: a Python memory backend, a Cordis web plugin and three agent presets.

## Highlights

- **5-class memory model** — semantic, short-term, process, source archive, compressed archive
- **3 storage tiers** — active → cold → archive, with automatic demotion and lifecycle decay
- **Sensitivity-aware by default** — PII and natural-language password detection, redaction at write time, approval-gated reveal, full audit trail
- **Decision lifecycle** — proposed → exploring → pending → adopted / rejected / superseded / invalid, so conflict resolution is explicit instead of silent overwrite
- **Hybrid retrieval** — BM25 + vector + graph, RRF fusion, recency × importance × relevance weighting
- **Dual domains** — work / life memory separated at write and query time
- **Mode presets** — two production presets (task / daily) plus an extension template
- **WebUI** — memory panel, entity graph, archive, maintenance, per-session config
- **Dynamic task board** — 任务看板已独立为 [dsh-livetaskboard](https://github.com/UnKnownFish125/dsh-livetaskboard) 插件（状态机 + 外援 sol/子代理），本仓库保留任务系统接口
- **Recommended companion** — 长对话场景推荐搭配 [dsh-longlongchat](https://github.com/UnKnownFish125/dsh-longlongchat)（大纲悬浮窗、跳转分块加载、LLM 中文总结），与 deepmemory 记忆补全互补使用
- **Explicit ownership** — tasks are Workspace-scoped and link to a Session; state cards are independently versioned per Session

## Memory Model (brief)

```
raw conversation → memory entry (class + domain + actor + sensitivity)
                → atoms (TTL / decay / reinforcement)
                → graph (entities + relations)
```

- **Classes**: `semantic` (stable facts), `short_term` (recent daily context), `process` (task-progress details), `source_archive` (original excerpts), `compressed_archive` (consolidated summaries).
- **Tiers**: memories start in `active`, demote to `cold` after their window (7 days short-term / 15 days process by default), and live in `cold` for a year before `archive`. Recall prefers active, then cold.
- **Sensitivity**: `normal / sensitive / protected / secret`. Detectors cover bank cards (Luhn), Chinese ID numbers (checksum), phones, API keys/tokens, and Chinese natural-language passwords. Matches are redacted on write; revealing original text requires approval (3 attempts, 30-min TTL, audited).
- **Decisions**: a status machine prevents a later conversation from silently overriding an earlier decision. Rejected alternatives are downgraded; only the user can mark a plan `invalid`.
- **Retrieval**: three-way RRF retrieval with a cache, per-domain filtering, actor filtering and sensitivity filtering.

## Mode Presets — pick your agent's shape

deepmemory ships as **three presets**, each a complete `agent.cordis.yml` configuration. Choose one per session.

| Preset | Shape | Includes | Skips |
|---|---|---|---|
| `task` 任务工作模式 | full coding agent | all tools, plan mode, sub-agents, workflow, process memory, budget profile `task-default`; 任务看板由 dsh-livetaskboard 提供 | — |
| `daily` 日常问答模式 | lightweight Q&A | web search, short-term memory, daily state card, topic continuity, budget profile `daily-default` | sub-agent orchestration, workflow |
| `blank-template` | extension template | minimal persona, optional-tool comments, `plugin/` entry point, preset-local realm example, budget comments | deepmemory injection, extraction, state card |

Each preset declares a **budget contract**: `budget_profile` + `priority_allocation` (per-component priority and `min_tokens`). Unused budget returns to the pool and is redistributed by priority — the model context window is the hard cap, components negotiate within it.

## Quick Start

```bash
git clone https://github.com/UnKnownFish125/dsh-deepmemory.git && cd dsh-deepmemory
sudo bash scripts/install.sh
```

Idempotent and safe to re-run. It installs the memory backend (`memory-server`, systemd unit, health check), the web plugin (`dsh-deepmemory` bundle registered in `profiles/web/package.json`, client.js auto-converted to `__ModuleLoader__` format), and the agent presets under `${DSH_HOME}/.agent-presets/`. The script never restarts the DSH web process itself — it prints a restart checklist and leaves that to the administrator.

Override environment: `DSH_HOME`, `APP_DIR`, `VENV_PY`.

## Embedding — local model or API (pick one)

Semantic retrieval runs on a **pluggable embedding provider**, configured under the `embedding` group (rendered automatically in the WebUI「配置」tab, or via the session config API):

| Key | Default | Meaning |
|---|---|---|
| `embedding.provider` | `local` | `local` = fastembed inference on this machine; `api` = any OpenAI-compatible `/v1/embeddings` endpoint |
| `embedding.local_model` | `BAAI/bge-small-zh-v1.5` | Local model; downloaded automatically from the HF mirror on first use |
| `embedding.api_base_url` | — | Required when `provider=api`, e.g. `https://api.openai.com/v1` |
| `embedding.api_key` | — | Required when `provider=api`; or inject via `EMBED_API_KEY` env var to keep it out of the DB |
| `embedding.api_model` | `text-embedding-3-small` | Model name for the API provider |

**Default model: [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)** — 512-dim, tuned for Chinese, ~30 MB ONNX, fully offline after first download (served via the HF mirror `hf-mirror.com`; override with `HF_ENDPOINT`).

Switching providers (e.g. `local` → `api`) is safe: the FAISS index is rebuilt automatically with the new dimension, existing memories are re-embedded on demand, and **no data is lost**.

## Usage

1. **Choose a preset** — start a new session and select `任务工作模式` (task) or `日常问答模式` (daily). Memory plugin loads automatically; no dynamic `define+run` needed.
2. **WebUI** — open the「记忆」tab in the conversation view: manage memories, inspect the entity graph, edit the current conversation card, and use the current Workspace task kanban. Every task can open or rebind its linked conversation.
3. **API** — HTTP backend on `:6230`: v2 task API at `/v1/v2/tasks?workspace_id=...`（任务看板 UI 由 dsh-livetaskboard 消费）, session cards at `/v1/v2/cards/<kind>/<session_id>`, recall/lifecycle under `/v1/v2/`, session config at `/v1/config/session/*`, and sensitivity audit at `/v1/sensitive/audit`.

## Extending

- **Child plugin (recommended)** — copy `agent-preset/blank-template/` to `${DSH_HOME}/.agent-presets/<your-preset>/`, rename it, put your business code in `plugin/`. The template deliberately ships without deepmemory so you start from a clean slate; wire in the pieces you need (memory injection, state card, budget profile).
- **Custom preset** — base it on `task/` or `daily/`, adjust persona, tool catalog and `budget_profile`; keep session state out of preset files and in plugin code.
- **Memory classes & API** — `memory-server/v2_domain.py` is a stdlib-only data-contract layer; extend constants (`MEMORY_CLASSES`, `STORAGE_TIERS`, …) and lifecycle primitives there, then expose routes in `server.py`.
- **Sensitivity rules** — add or tune detectors in `memory-server/sensitive.py`; unit tests live in `memory-server/tests/test_sensitive.py`.

## Architecture

```
┌─ write ────────────────────────────────┐
│ ① cheap LLM extraction (turn-stopping) │
│ ② memory_save model tool (explicit)    │
│ ③ WebUI manual entry                   │
└────────────────┬───────────────────────┘
                 ▼
   memory-server (Python, systemd :6230)
   SQLite + FAISS + BM25 (jieba)
   RRF fusion + tri-factor weighting
   sensitive redaction / decision states / lifecycle decay
                 ▼
┌─ recall injection ─────────────────────┐
│ silent system-message injection        │
│ [state card] + [Top-K memories]        │
│ scopes: session / workspace / global   │
└────────────────────────────────────────┘
```

## Inspiration & References

- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442) — memory stream, recency × importance × relevance retrieval, reflection
- [Memory in the Age of AI Agents: A Survey](https://arxiv.org/abs/2512.13564) — 2025 survey of agent memory architectures
- [HiMem: Hierarchical Long-Term Memory for LLM Long-Horizon Agents](https://huggingface.co/papers/2601.06377) — hierarchical long-term memory

## License

**AGPL-3.0**. This project is an independent native implementation for DeepSeek Harness, not a code port. Modifications, derivatives and distribution (including network service provision) must comply with AGPL terms.
