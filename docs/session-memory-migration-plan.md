# deepmemory 会话记忆治理计划（迁移 · key · 归档清理 · 状态卡/任务板 AI 自动更新）

> 版本：v0.6 讨论稿
> 状态：规划中，未进入实施
> 基线：v0.5.0（a9319ab）已收口提交并部署；生产 web 插件为 `dsh-deepmemory`。
> 更新：2026-08-24

## 0. 用户需求原文（梳理）

1. **记忆迁移接口**：记忆支持跨会话/跨实例迁移。
2. **会话记忆库 key**：每个会话对应的记忆库需要一把 key 指向访问，用于迁移；
   key 通过**会话维护界面**获得。
3. **会话归档后记忆清理**：会话归档（删除/移出）后，对应记忆随迁清理。
4. **状态卡与任务看板未被会话 AI 自动更新**：需要修复，AI 应能驱动状态卡与任务板更新。
5. **状态卡主动初始化保存失败（HTTP 500）**：已复现，纳入修复（§3.6）。
6. **技能蒸馏先缓**；后续**知识蒸馏与技能蒸馏合并设计**一起做。

## 2. 现状与根因（已核实，2026-08-24 代码基线）

### 2.0 自动压缩验证结论（2026-08-24 生产实测）

- **结论：生产自动压缩链没有正反馈**。证据：
  1. journal（dsh pid 1083072）显示 `extracting from 6/7/15/16 messages…` 出现 4 次，
     **0 次** `extracted …`（v0.5.0 插件只在成功写回时打 extracted 日志）→ 抽取 LLM 空转；
  2. memory.db 过去 24h **无新记忆、无新状态卡写回**；最新状态卡停在 08-23 01:02；
  3. host 五轮节拍日志（five-turn / compaction / memory completion）**零出现**；
  4. 长会话压缩只有旧时代的 2f5e8b0b×2、50d5dc83×1，重启后 0 次新压缩；
  5. 生产会话 `agentPreset=cordis`，不走 harness-memory preset 的 0.45/0.2 覆盖，
     cordis 自带 compaction-basic 无阈值覆盖；且生产 settings 里
     `deepmemory.context_automation.enabled` 为 null（→ 门控默认放开，但下游无输入）。
- **推断根因**：抽取 LLM（生产仍为旧 `deepseek-chat`；v0.5.0 已改 `uuapi/deepseek-v4-flash`）
  返回空/无 JSON → 无记忆、无状态卡增量 → 五轮节拍/压缩失去输入。
  这与「初始化 500（extractSessionCard 无 JSON）」同根因。
- **修复方向**（并入选 §3.5/§3.6）：先恢复抽取 LLM 可产出（模型路由 + 失败分级），
  再让状态卡写回与五轮压缩有输入；验证以 journal `extracted` 日志 + 状态卡 revision
  增长为准；preset 应选择 harness-memory（0.45）才能让压力压缩按新阈值触发。

### 2.1 状态卡/任务板为何不被 AI 自动更新

- `agent-preset/memory-plugin/plugin-v3.js` 的抽取 prompt（`EXTRACT_SYSTEM`）**声明了**
  `card`（goal/current_plan/key_decisions/in_progress/next_steps），
  但 `agent/turn-stopping` 处理器只把 `result.memories` 写入 `/v1/memories/add_batch`，
  **丢弃 `result.card`**（代码第 220–357 行无任何 `/v1/v2/cards` PUT 调用，只有第 232 行 GET）。
- 任务看板（`/v1/v2/tasks`）只有 Web UI 手动操作路径（client.js 任务面板），
  **没有任何 AI/插件写入通道**，也不在抽取 prompt 中。
- Web Host（`web-plugin/index.js`）的五轮节拍虽然每 5 轮用 LLM 同步状态卡一次，
  但它基于 `session/event` 文本批量抽取，是"host 侧兜底"，不是"会话 AI 自主更新"，
  且对 `harness-memory` preset 的会话与插件写入没有冲突协议（谁写赢，靠 `expected_version` 勉强防覆盖）。

结论：**AI 驱动状态卡/任务板更新的通道是断的**——抽取拿到了 card 却没落库，tasks 干脆没有 AI 入口。

### 2.2 会话记忆的边界与可达性

- `documents` 表以 `session_id` / `workspace_id` 作为归属边界；`scope_allows()` 做读写过滤。
- 记忆按会话管理的现有接口：单条 `GET/DELETE /v1/memories/<id>`、`/v1/memories/list`（可按 scope/session 过滤）；
  **没有按会话批量清理、没有会话级导出/迁移接口**。
- `session_id` 本身是 DSH 会话 UUID，任何人拿到 UUID 就能构造对该会话记忆的访问——**缺少独立于会话 UUID 的
  迁移凭证（key）**，无法控制"谁可以用这个会话的记忆迁移"。

### 2.3 会话归档/删除

- DSH 会话生命周期由 Host 管理（`dsh-session` 的 `SessionStore`），
  事件词表中没有 `session/archive` 类的持久化事件；
- deepmemory 目前**不做任何**会话删除的跟随清理——会话归档后其记忆、状态卡、任务板会残留。

## 3. 目标设计

### 3.1 会话记忆 key（迁移凭证）

- **模型**：每个（`workspace_id`, `session_id`）配一把 opaque key（随机 32B，URL-safe）。
  key 只定向到"这个会话的记忆库"，不携带其他会话权限；
  key 与 session 双向映射：key → session 记忆；session → 可重新生成 key。
- **存储**：新增 `session_keys` 表（或 settings 键 `deepmemory.session_key.<base64(id)>`）；
  server 提供：
  - `POST /v1/v2/session-keys` {workspace_id, session_id} → 创建/轮换 key
  - `GET  /v1/v2/session-keys/<session_id>` → 返回现有 key（会话维护界面显示用）
  - `POST /v1/v2/session-keys/<session_id>/rotate` → 轮换（旧 key 失效）
- **key 的用途**：迁移接口用 key 做**只读**凭证访问源会话记忆库，而不是用 session UUID
  （便于把"这份记忆"安全地交给另一个会话/实例）。

### 3.2 记忆迁移接口

- `GET /v1/v2/sessions/<session_id>/memories/export?key=<session_key>&format=json`
  - 导出该会话全部活跃记忆（content/key_facts/persona_summary/type/domain/scope/importance/atoms/entities/relations），
    含 source 摘要与状态卡快照；不含敏感原文（沿用脱敏规则，`has_sensitive` 标记保留）。
- `POST /v1/v2/sessions/<session_id>/memories/import`
  - 接收导出 JSON（携带来源 key 或来源 session+key），按目标会话/工作区写入；
  - 支持 `mode: merge | replace`：merge 按 content 指纹去重，replace 先按目标会话归档旧记忆再写入。
- 迁移后可选择：源会话记忆**归档**（`status='archived'`，保留溯源）或**删除**（确认后）。
- 跨实例迁移：export 文件可先落盘（`/v1/v2/sessions/<sid>/memories/export.dat`）再在目标实例 import，
  与"同一 server 内不同会话迁移"共用数据格式。
- **sensitive / protected_source 处理**：导出不含敏感原文；目标实例 import 时敏感标记随行，
  原文通过受保护来源机制在目标实例重新授权解析（不复制密钥）。

### 3.3 会话维护界面（key 获得入口）

- **deepmemory 记忆面板**（client.js conversation.view `记忆` tab）新增「会话维护」区块：
  - 显示当前会话 key（可复制、可轮换）；
  - 「导出记忆」「导入记忆」按钮；
  - 会话归档清理入口（见 3.4）。
- 该区块仅在该会话 opens 时显示，不跨会话暴露其他会话的 key（key 独立、按会话隔离）。

### 3.4 会话归档 → 记忆清理

- **触发**：DSH Web 会话抽屉删除/归档会话时，Host 不再主动感知 deepmemory；
  方案：由记忆插件的 Host 侧监听会话管理事件，或由 DSH 会话删除流程显式调
  `POST /v1/v2/sessions/<session_id>/purge`。
- **server 提供**：
  - `POST /v1/v2/sessions/<session_id>/purge`：按该 session 归档其全部 `documents`
    （`status='archived'`），归档其状态卡与任务（移入 `archived` 状态），保留溯源与审计；
  - 默认**归档而非物理删除**（与 deepmemory 现有归档语义一致，可恢复），
    可选 `?hard=1` 物理删除（确认后，审计记录保留）。
- **插件侧**：preset/plugin 看到会话结束时（`session/event`? 无此事件）——改为在
  `agent/turn-stopping` + Host 响应中检测"该会话已被抹掉"的条件（如 session store 追查不到）
  是过重；故**清理动作锚定在 Host/UI 层**：会话删除/归档操作发生时调用 purge，插件本身不承担归档探测。

### 3.5 状态卡/任务看板 AI 自动更新（修复）

1. **plugin-v3 写回状态卡**：
   - 抽取产物从 `card` 字段落盘：turn-stopping 中若有 `card`，调
     `PUT /v1/v2/cards/<kind>/<session_id>`（expected_version 用 GET 查到的当前版本）；
   - 同时把"本轮状态卡是否变化"作为 `refreshMemoryCache` 的触发信号之一。
2. **插件任务看板 AI 入口**：
   - 抽取 prompt（EXTRACT_SYSTEM）扩展一条 `tasks` 输出：对当前轮解析出
     `{title, status(planned|todo|in_progress|completed|failed), parent?, blocked?, reason?}`；
   - plugin 调 `POST /v1/v2/tasks`（create）或
     `POST /v1/v2/tasks/<id>/transition`（流转），均带 workspace_id/session_id；
   - 用抽取 LLM 的确定性 JSON 字段 + 按权利要求匹配的命中规则：仅有精确匹配才落任务，避免幻觉任务膨胀。
   - 设计中间稿：同一轮内任务更新合并为一次任务组操作。
3. **Host 五轮节拍与插件写入的协同**：
   - 原则：preset 会话（harness-memory）以插件 AI 写入为准，Host 五轮兜底只做"无插件写入时的下限"；
   - 非 preset 会话（cordis 等）：保留现有 Host 五轮写卡；
   - 冲突：仍以 `expected_version`/增量 revision 防覆盖——不改单体并发语义。
4. **卡片修订/任务事件**：写入全程走 v2 修订轨迹（`state_card_revisions`、`task_events`），AI 行为可审计溯。

### 3.6 状态卡主动初始化 HTTP 500（已复现，纳入修复）

- **现象**：无状态卡会话点「初始化」→ `POST /mem-api/v1/cards/initialize` → **HTTP 500**。
- **已核实根因**：`web-plugin/index.js` 的 `extractSessionCard()` 用
  `uuapi / deepseek-v4-flash` 流式抽取会话状态卡；当 LLM 流无 `{...card...}` JSON 输出
  （流为空 / 网关错误 / 模型回复不合 JSON 契约 / 历史提取超限）时抛
  `state card extraction returned no JSON`，被初始化路由 catch 成 500。
- **验证盲区**：`scripts/verify/web-check.py` 只断言"初始化按钮存在"
  （`count() == 1`），从未点按走通抽取→建卡链路，故 not caught。
- **修复方向**：
  1. `extractSessionCard` 失败语义分级：LLM 流空/网关不可用 → `503`（可重试）；
     解析不到 JSON → 重试 1 次；仍失败返回 `{status:'extract_failed'}` 而非抛 500，
     前端显示可恢复提示（而非"保存失败"）。
  2. prompt 契约加固：`deepseek-v4-flash` 输出强制 JSON 说明 + 失败回退模板；
  3. verify 增加真实初始化链路用例（mock LLM 输出合法卡 JSON → 断言建卡成功、
     或有卡时不重复创建、无卡且 LLM 异常时不产生半成品卡且非 500）。
  4. 与 §3.5 共享修复：插件 AI 写回状态卡的提取健壮性复用同一 LLM 链路。
- **验收**：初始化可成功建卡；LLM 不可用时返回结构化错误而非 500；verify 覆盖真实链路。

## 4. 蒸馏（技能 + 知识，合并设计，延后）

- **延后**：本计划不实施蒸馏；技能蒸馏（`deepmemory-skill-distillation-plan.md`）暂行保留，
  等待指令。
- **未来合并设计基线**：
  - 统一候选源：技能蒸馏（重复操作+SOP）与知识蒸馏（稳定事实/领域知识）共用
    `skill_candidates` 同类候选与成熟度评估（use≥3 且 7 天无修正）；
  - 输出形态区分：`SKILL.md`（程序性 SOP）vs 知识条目（declarative 提炼为可写回记忆库的高置信 fact）；
  - 统一"固化→从记忆召回排除+溯源保留（archived + distilled_from）"通道；
  - UI：记忆 tab 的「技能」标签扩展为「技能 / 知识」两栏状态（候选/已固化/可操作）。
- 本轮只预留架构钩子（server 数据表/API 命名预留），不实施功能。

## 5. 影响面与兼容性

| 面 | 影响 |
|---|---|
| server.py | 新增 `session_keys` 表 + 4 组路由（keys/export/import/purge）；不改动既有 schema |
| v2_domain.py | `V2Store` 增加 session_keys 域函数；任务流转已有，不重写 |
| plugin-v3.js | 恢复 card 写回 + 新增 tasks 抽取/写入；系统 prompt 扩展 JSON 契约 |
| web-plugin/index.js | 初始化 500 修复（§3.6）+ Host 协同修订（保留五轮兜底）+ 清理/迁移代理路由 |
| client.js | 记忆面板新增「会话维护」区块（key 显示/轮换，导出/导入，归档清理入口）；初始化错误可恢复提示 |
| verify.sh | 新增迁移/key/归档/任务 AI 写回 + 初始化链路真实用例 |
| install.sh | 无新增安装面（schema 迁移内建在 server 启动） |

## 6. 验收标准

1. 会话 key：GET key 稳定；轮换后旧 key 不可再访问。
2. 迁移：A 会话导出 → B 会话 import（merge/replace）→ B 检索命中；sensitive 原文不出现。
3. 归档清理：删除会话 → 该 session memories 全部 archived；`/v1/memories/search` 不再返回；
   `/v1/memories/list?status=archived` 仍可查全/可恢复。
4. AI 状态卡：`agent/turn-stopping` 抽取含 card 变化时，`/v1/v2/cards` PUT 被调用，revision 递增，无 PK 冲突。
5. AI 任务板：会话中出现明确任务描述 → tasks 表出现对应任务且状态正确流转；
   无明确任务时不产生空任务。
6. 初始化 500 修复：无卡会话「初始化」成功建卡；LLM 不可用/无 JSON 时不返回 500，
   前端获得可恢复错误状态；verify 真实链路用例通过。
7. 回归：原抽取/注入/压缩/归档/图谱/备份验收全绿（verify.sh 四层）。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 导入 ID 冲突/重复 | content 指纹去重（merge）/ replace 先归档旧记忆；报告合并量 |
| key 泄漏 | key 独立于 session UUID、可轮换、仅 UI 展示且掩盖首尾；审计记录 key uid 而非全文 |
| AI 任务板幻觉爆炸 | 精确匹配才写、每轮最多一组、带 expected_version；约束抽取 JSON 为确定性字段 |
| 归档过于保守 | 默认归档不删除，软恢复路径保留 |
| 与 Host 五轮写入冲突 | preset 会话以插件为准，Host 兜底只补充状态卡缺失场景 |
| 蒸馏模型调用成本 | 蒸馏延迟到 P2/P3 与知识蒸馏合并设计，不占本轮 |

## 8. 待用户确认

1. 已确认迁移/归档清理/任务板 AI 更新进入本轮；技能蒸馏 + 知识蒸馏延后合并（本节）。
2. 归档语义默认「归档（软）」而非硬删——是否同意。
3. 迁移 key 生成方式：自动（每次会话创建时）+ 可手动轮换——同意否。
4. 导入目标会话记忆库的归属：默认「迁移目的会话自己的 workspace_id」——是否要可指定。
5. 初始化 500 的修复优先级：与迁移/归档/l渲染清理同批，还是先行单独修复——
   建议**先单独修复（小改动、立刻解除生产报错）**。