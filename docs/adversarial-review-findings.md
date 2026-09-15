# 对抗性复核结果（deepmemory 已应用修复的独立验证）

**执行者**：独立子 agent（只读；生产仅 GET/检索，测试机仅可逆操作且已清理）
**日期**：2026-09-16
**性质**：对 `docs/astra-plugin-review.md` 所列修复的**事后对抗性验证**（astra 审的是原始代码，本报告审的是**修复后的**代码）
**结论**：修复总体有效，**无回归、无误伤**；发现 3 个未修缺口 + 1 个必须动作

---

## 零、必须动作：磁盘上的 preset 修复在运行中的 dsh-web 里未生效

| 证据 | 内容 |
|---|---|
| 运行实例 | `dsh-web.service` PID 3912913，**启动 09-15 16:41:08** |
| 被改文件 | `_memory-plugin/plugin-v3.js`，最后写入 **23:51:01** |
| 加载机制 | cordis loader 走 Node 内部 ESM loader（`@deepseek-ai+cordis-plugin-loader@1.0.3/lib/index.js:270-279`，`internal.import(name, baseUrl, {})`），**无 cache-busting** → 进程内同 URL 只求值一次 |
| **运行版本指纹** | 旧格式 `[deepmemory] extracted N memories (total X): [{"id":…` 出现 **38 次**；N09（23:48）才引入的新格式 `extracted N/N memories` **0 次** |
| 旁证 | `GET /v1/settings/session_enabled:<sid>` 自 16:41 起**每个会话只请求 1 次**（N04 的 5s TTL 若生效应反复请求） |

→ preset 侧 **20 项**（N01/N03a/N03b/N04/N05/N06/N08/N09/N10/N12/N13/N14/N15/N18/N23/N24/N25…）在生产**全部未生效**，必须重启 dsh-web。**重启前确认无 running 会话**（见 AGENTS.md 红线 2）。

> 注：复核据亚秒时间戳推断「S03/S17 在 prod 也未加载」，**此推断不成立** —— 主 agent 用运行时行为证伪：新建备份的 manifest 含 `fingerprint: {count, max_id}`，正是 S17 才有的字段。memory-server 是 Python，每批补丁后均已重启。

---

## 一、五项验证结论

| 项 | 结论 | 关键证据 |
|---|---|---|
| **S01** 备份穿越 | **有效，无绕过** | 入口穷举仅 4 处（DELETE/restore/list/create），全部校验；**v2 API 无备份端点**；离线实测 `..`/`.`/`%2e%2e`/`../../etc`/软链外指 全 False；线上（测试机）全部 400；软链内指虽 True 但 `shutil.rmtree` 对软链抛 `OSError: Cannot call rmtree on a symbolic link` → 不可利用 |
| **S02** 模型指纹 | **有效，2 个缺口** | 离线 harness：一致→rebuild 0；同维换模型→1；旧 dim.json 无 fp→0 且补写；local→api→1。线上 `dim.json` 与配置一致、不重建 |
| **S12** 归档/恢复同步 | **有缺口** | 恢复路径实测：add→FAISS 有 / archive→**无** / restore→**有** / delete→无。缺失点见下 |
| **S14** 密钥屏蔽 | **有效，1 个缺口（已修）** | 测试机 canary：`/v1/config` 0 命中、`/v1/config/session` 不含值、`/v1/settings/<key>` 404、`/v1/overview` 0 命中 |
| **N01** 合成输入过滤 | **逻辑正确、不误伤** | 线上会话日志实测 `source.kind` 分布：`plugin:529 / user:264 / goal:28 / agent-instructions:18 / agent-message:16 / skill-catalog:11 / subagent-settled:10 / subagent-report:5`，**无一条缺 source**；真实用户消息 = `{"kind":"user","rpcId":…,"clientTimeZone":…}` |

---

## 二、未修缺口（按优先级）

### G1「S12-a」`apply_consolidation` 未同步 FAISS（**host 合并流程必经**）
- 位置：生产 `server.py:2667-2708`（`get_bm25().remove(mid)` @2702，无任何 FAISS 移除）；测试机 `2975-3010` 同
- 可达路径：host `index.js:360 POST /v1/maintenance/consolidate/candidates`（dry_run）→ `index.js:393 …/consolidate/apply`
- 后果：被合并记忆 `status=archived`、BM25 已删、**FAISS 向量保留**（死向量长期占据 `k*3` 候选位，且不自愈）
- 修法：在 BM25 移除处并行调用 `_index_remove_ids(...)`（与 `archive_memories` 的既有配对不同）

### G2「S12-b」`v2_domain.purge_session()` 完全不碰 FAISS/BM25
- 位置：`v2_domain.py`（v2 API 直改 `documents` 表）
- 实测：测试机 FAISS 含 id `10674/10675/10676`，而 DB 行已硬删（`max(id)=10673`）
- 修法：purge 时按 session 收集 ids → 同步 `_index_remove_ids` + BM25 移除

### G3「S02 旁路」`/v1/settings/set` 可抹平指纹
- 位置：`get_index()` 只在 `_index is None` 时比对指纹（`server.py:697-716`）；而 `/v1/settings/set`（3529-3534）只 `set_setting`，**不清 `_index`、不删 `DIM_PATH`**
- 链路：旁路写 settings → 搜索先跑 `embed_texts([query])`(1114) → `set_embed_dim`(319) **把 dim.json 的 fp 覆盖成新模型** → 指纹证据消失
- 实测：`查询 embedding 后 dim.json: {"dim":1024,"fp":"api:bge-large"}` → `get_index()` 返回旧索引 `rebuild_calls=0`，**重启也不重建**
- 条件：需 token + 同维模型两条件同时成立（正常走 `/v1/config` 的改动会 `os.remove(DIM_PATH)`(1996) 强制重建，不受影响）
- 修法：`/v1/settings/set` 命中 embedding 相关键时同样 `os.remove(DIM_PATH)` + 置 `_index=None`
- **另**：指纹只由 `provider:model` 构成，`api_base_url` 变更 / 同名不同后端不可感知

---

## 三、已处置

| 项 | 处置 |
|---|---|
| **S14 解析器缺口** | ✅ 已修（`patch_server_s14b_secret_key.py`）：原 `key.split(".", 2)` 假定 sid 不含点 → `session.a.b.embedding.api_key` 不被屏蔽（实测 `/v1/config` 明文回显、`/v1/settings` 200）。改为按 `.<secret>` 后缀判定。测试机复测：回显 **0**、settings **404**；生产已重启加载 |
| **G1 `apply_consolidation` 缺 FAISS 配对** | ✅ 已修（`patch_server_g1_consolidation_faiss.py`）：循环后 `_index_remove_ids(archived)`。4 处副本标记各 1、`py_compile`、两侧重启后检索 200。⚠️ **未做功能实测**（需真跑一次合并归档、会改数据），正确性来自与 `_run_decay`/`archive_memories` 既有配对模式同构 |
| **G3 `/v1/settings/set` 旁路抹平指纹** | ✅ 已修（`patch_server_g3_settings_bypass.py`）：命中含 `embedding` 的键时执行与 `/v1/config` 相同的失效动作（清 `_index`、清 `_embed_model`、删 `dim.json`）。两侧重启后 `dim.json` 完好、检索 200。⚠️ **未做功能实测**（触发会真删 `dim.json` 引发 ~10 分钟级重建），正确性由逐行同构保证 |
| **N01 误伤风险** | ✅ 双重验证排除（主 agent 读 DSH 源码 + 复核用线上日志，结论一致） |
| **G2 `v2_domain.purge_session` 不碰 FAISS/BM25** | ⬜ **仍未修**（需按 session 收集 ids 后调用 `_index_remove_ids` + BM25 移除；跨模块改动，留待后续） |
| **`harness-memory` preset 缺补丁** | ⬜ 待决策：该 preset 挂自带 610 行副本（9/8，0 补丁），选"记忆增强模式"的会话重启也拿不到修复 |
| **副本漂移** | ⬜ 记录：`_memory-plugin`(66340d07) == 测试机 == `dsh-deepmemory/agent-preset/memory-plugin`；但 `harness-memory-archive/agent-preset/memory-plugin`(902a7ff5)、`dsh-deepmemory/agent-preset/_memory-plugin`(27fdd5f8)、`deepmemory-v063-deploy/agent-preset/memory-plugin`(89266caa, 590 行) 各不相同且**不在验收工具的核对组里** |

---

## 四、复核未能覆盖的部分（如实记录）

1. 生产侧的启动/写入时序无法从 systemd 秒级时间戳判定，故 S01/S02/S14 的"prod 已加载"最初为推断（S03/S17 已由 manifest fingerprint 实证；S14b 为重启后新加载）。
2. `apply_consolidation` 未做实测（需真跑一次合并归档、会改测试数据）→ 结论来自静态可达性分析 + 别处死向量实证。
3. 测试机 3 条死向量的**成因**无法唯一归因（v2 hard purge / 手工 sqlite 删 / DB 快照回滚都可能）。
4. §0 的 ESM 缓存结论基于 loader 源码 + 日志指纹；若 DSH 另有未知的模块热重载机制则需修正 —— 但 38:0 的格式差已足以证明运行版本早于 N09(23:48)，即模块自 16:41 加载后未被重读。
