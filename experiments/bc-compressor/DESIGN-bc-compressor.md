# billion-context 压缩机制拆解 + deepmemory 增量可逆压缩设计（v0.1）

- 作者：compress-engineer（Team Lead: lead）
- 日期：2026-10-21
- 状态：**只读研究 + 离线验证**。未改生产 `:3081` / 生产 memory-server `:6230`；未改测试机 `:3091` 的插件行为；所有实验产物在
  `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/` 与 `/tmp/bc-exp/`。
- 证据标注：**【实测】**=本次在真实会话日志上量测；**【读代码】**=billion-context `dist/` 或 DSH 包源码；**【读论文】**=论文原文；**【推断】**=未验证推断。
- 路径一律绝对路径。

---

## 0. 结论速览（先看这段）

1. **用户看到的"5 条摘要开头一模一样"已在真实日志上复现**：8 份真实日志（7 个会话、125 次 `compaction/summary`）里，注入取最近 5 条 ×240 字符，**80/80 对首行完全相同**，去掉 `[摘要#N]` 前缀后的最长公共前缀中位 **214 字符**（上限 240 = 整个切片），40 字符 shingle 重复率中位 **23.8%**。【实测】
2. **根因不是"每次压缩把原始会话重读一遍"**：DSH 从不重复折叠原始事件（125 次压缩里 raw refold = **0**）。真正的机制是
   **DSH `compaction-basic` 每次产出的是"把上一份 checkpoint 摘要 + 新内容合并重写"的整段摘要**——实测新摘要的**输入**里，上一份 checkpoint 节点文本量中位 **95.75%**（80%–98.5%）于新摘要自身长度（即"几乎等量重写"）；从"源事件覆盖"看，后一份摘要覆盖的原始事件集合中位 **91%** 与前一份重复。全文 8-word shingle 重叠中位只有 **12.6%**（细节确实在变），但注入取的正是每份摘要最前 240 字符——那是重写最少、重复最多的头部（80/80 首行相同）。【实测】
3. **还有一条机械放大器**：`compaction-basic` 在压力仍超阈值时会 `compactionRetries`（默认 1）再压一次，而这时唯一可压的就是**刚生成的 checkpoint 节点**。真实日志抓到：第二次压缩 `shadowedRange {start:1998,end:1998}`、`shadowedSeqs` 长度 1、2,759 tokens——把刚写好的摘要又重写了一遍。【实测 + 读代码】
4. **deepmemory 插件把它当"链"存**：`compaction/summary` 每来一条就 `topic_summaries` 插一行（正文 `slice(0,3000)`，无范围/无句柄），注入固定取最近 5 条。这三点（DSH 整段重写 + 重试再压 checkpoint + 记忆侧追加式存链/取 5 条）叠加，就是用户看到的重复。【读代码】（live 插件 `:823-855`、`:700-714`）
5. **billion-context 值得照搬的不是"模型摘要"本身，而是 fold 的结构**：increment-only（每段内容只折一次）、block/多代/lineage、**原位插入**（前缀缓存友好）、可逆句柄（`decompress` 默认还原一层、`full:true` 还原原文、`toFile` 不膨胀上下文）、growth-gated 触发、预算式摘要工艺。**不必照搬**：代理拦截、注入 4 个工具、模型自决压缩时机（DSH 已有自己的 model-driven compaction）、把会话原文状态放代理内存。【读论文 + 读代码】
6. **可落地的最小方案 = Compressor 层**（`compress/expand/renderInjection/status` + 外部存储 + 覆盖账本），插件侧只增加：压缩事件捕获 → 增量分块 → 注入渲染 → `memory_call(layer='expand')`。全链路 fail-open，旧 `topic_summaries` 一行不动，灰度只靠 server settings。
7. **离线验证已通过**：8 份真实日志共 125 次压缩；Python 侧按"每份日志独立会话"存了 **123** 个可逆块（sha256 全等），JS 侧把 0655 的生产 v3 与测试 v4 两份日志视为同一会话，因此后到的 26 个范围被覆盖账本判为 `already-covered`，成功落 **99** 块、其余 26 次正确跳过；**原文重折 0**；注入 append-only **92/92**；失败降级 6 项全过。原文存储 zstd 压缩比 **3.92×**（31.9 MB → 8.15 MB）。**注意**：这里没有跑真实 LLM 摘要（见 §4.5 局限）。

---

## 1. billion-context 压缩算法拆解

资料：npm `billion-context@0.1.202`，解包在 `/tmp/billion-ctx/package/`；论文
`/tmp/billion-ctx/package/paper/model-driven-incremental-hierarchical-compression-training-free-multi-generational-context-management-for-long-lived-coding-agents.md`（95 KB）；实现为 minify 后的
`/tmp/billion-ctx/package/dist/index.js`（4.97 MB / 4790 行，字符串常量与函数名保留，本次用 grep 定位后逐段阅读）；DSH 原生入口 `/tmp/billion-ctx/package/dist/agent/dsh-native.js`（2.36 MB / 145 行，minify 更彻底）；配置文档 `/tmp/billion-ctx/package/CONFIGURATION.md`（327 KB / 2341 行）。

### 1.1 形态（它怎么接进来）

- 代理模式：`bili-proxy` 拦截请求 → 跑 `acp-kernel` 压缩 → 注入 `compress/decompress/search_context/acp_status` 四个工具 → 转发上游 → 改写流式响应。【读论文 §3.1；README.zh-CN「工作原理」】
- DSH 接入：`/tmp/billion-ctx/package/dsh.bundle.patch.yml` 做三件事：
  1. `insert: bili-native`（原生插件 entry）；
  2. `compaction-basic: {auto: false}` **关掉 DSH 自带自动压缩**；
  3. 给 `preset-standard/ptc/minimal/cordis` 注入 `biliBasicCompactionPolicy`（Web preset 场景的加载期闸门）。
  也就是说它**整体替换** DSH 压缩，而不是共存；这也解释了为什么装上它我们的 `[历史脉络摘要]` 链路会失效。【读代码】
- 关键分工（论文 §3.1）：**模型只负责写摘要，kernel 负责其余全部**：引用分配、块同步、合并、裁剪、过滤、隐藏压缩调用、nudge 注入、应急截断、渲染。kernel 暴露
  `processTurn(state, messages, config) → (state', rendered, effects)`，host 只做模型 I/O 与工具派发。【读论文】

### 1.2 数据结构（这是最值得抄的部分）

`CompressionBlock`（论文 §3.2）：

```
{ blockId, runId, tier ∈ {1,2,3}, topic, summary,
  directMessageIds, effectiveMessageIds, directBlockIds,
  createdAt, survivedCount, generation ∈ {young, old}, active }
```

- `tier-1` 折叠连续**消息区间**；`tier-2` 折叠 **block**（`directBlockIds` 指向来源 block）；`tier-3` 再折 tier-2。
- `effectiveMessageIds` **传递闭包**覆盖该块最终代表的所有原始消息 → 任意代重压缩后 lineage 不丢。
- `active` 决定是否渲染；deactivate 仍保留在 state 里，所以能恢复。
- 它是"**LSM tree over prose**"：young 代吸收写、old 代持压缩历史，压缩是增量的而不是全量重做（论文 §3.2 原话）。【读论文 + 读代码】
- 代码侧对应 `createInitialState()`：`{blocks, messageRefs:{byRaw,byRef}, tokenSnapshot, nudge, stats, absorbed, rules, nextBlockId, nextRunId}`；`allocateBlockId()` 生成 `b1,b2,…`；`advanceSurvival(state, promotionThreshold)` 每回合给 active 块 `survivedCount+1`，达到阈值 `promotionThreshold=5` 置 `generation='old'`。【读代码】
- 触发阈值默认（`resolveConfig()` 实测读出）：`tiers.tier2Trigger=1000`、`tier3Trigger=2000`（单位是 **active tier-1 块数**，不是 token）；`compress.minCompressRange=5000` 字符、`maxSummaryLength=20000`、`minSummaryLength=50`；`preserveRecentMessages=3`、`preserveRecentTokens=5000`；`nudge.growthFloor=50000`、`minGrowthFloor=20000`、`minGrowthRatio=0.45`、`minContextLimitPct=0.45`、`maxContextLimitPct=0.75`、`emergencyThresholdPct=0.95`。【读代码】
  生产里 tier-2 只占 60/1711 块（3.5%），是"安全阀"而非常规路径（论文 §6.2）——与阈值极高一致。

### 1.3 每回合 9 段流水线

`assign-refs`（铸 `mNNNNN` 稳定 id）→ `sync-blocks`（新内容投影到块边界）→ `merge-blocks`（合并相邻可压范围）→ `prune`（预算不变量）→ `filter`（按 role/content 的可见性）→ `hide-compress-calls`（删掉压缩工具噪声）→ `nudge-inject` → `emergency-truncate`（最后手段硬上限）→ `render-refs`（带 ref 标签的最终消息列表）。每段是纯函数、独立测试，host 从不直接改历史。【读论文 §3.1】

### 1.4 增量怎么做（核心）

1. **只压增量**：growth gate = 上下文超过窗口 45% **且** 自上次压缩后增长超过 50K tokens 才触发；当待压质量不足 50K 时，有效门槛降到 `0.45 × 50K = 22.5K`。每次压缩只碰"上次压缩之后新增的、任务已经消费过的一段"，基准在压缩后重置。【读论文 §3.4/§3.6；默认值见 §1.2】
   - 论文 §3.6 把它上升为设计决策：**每轮只压增量，O(increment) 而不是 O(history)**；收益同时体现在质量（丢的是已用完的高体量低密度工具输出）、资源（append-only，前缀不失效）、保留率（每段内容一生只折一次，ρ₁≈1/8 不随轮次复合衰减）、成本（计费 token 跟工作量走）。
2. **不是"按阈值自动压"**：模型决定压哪段、压多少、是否拒绝。nudge 只给"建议清单 + 真实尺寸"；`nudgeModelDecided` 还能先让模型二选一决定"现在压不压"。【读论文 §3.7；CONFIGURATION `compress.nudgeModelDecided`】
3. **六条交互机制**（论文 §4 第 5 点，load-bearing）：
   - 节流、只请求增量；
   - **不告诉模型填充比例**（避免"才 20% 很健康"锚定）；
   - 允许拒绝（质量阀门）；
   - 压缩时源内容临时获得完整注意力，压完即从工作集移除；
   - 推荐清单带真实尺寸（token 数/消息数/工具-文本构成）；
   - 压缩调用本身在消费后隐藏，不占后续上下文。
4. **多代递归**：摘要块本身可以再次进入压缩（tier-1 → tier-2 → tier-3）；per-tier 保留率约 tier-1 1/45（工具块 p75）、tier-2 1/4、tier-3 1/3（论文 §7，拟合值）→ "pagoda effect"，摘要质量增长逐层衰减。
5. **生产实测**（论文 §6.2，n=1,657 次压缩 / 17,783 块）：每次压缩源中位 2.6K tokens，输出中位 **185 tokens**、p90 1.2K、97.5% ≤2K；整体块压缩比中位 7.9×，工具主导块 24×（p75 45×）。

### 1.5 可逆怎么做

- 语义：`decompress` **默认只还原一层**（T2→T1 摘要，不还原原文）；`full:true` 才还原到原始消息；`toFile` 写到文件而不是把原文塞回上下文。【读代码：dist 中 decompress 工具描述原文；论文 §3.2】
- 生命周期：`decompress` 重新激活来源并把该 block 置为 inactive/consumed；之后再次压缩时该块作为"已消费 lineage"保留，可批量恢复到某个 run。【读论文 §3.2】
- 检索：`search_context` 在压缩摘要 + 可见消息里做关键词检索，先定位块再 decompress。【读代码】
- 状态：kernel state 持久化到磁盘（`persist.enabled=true`、`persist.tailTokens=16384` 保留近期原始消息、`persist.zstd` 可选）；`decompressTmpCap=50` 限制会话内临时解压块数。【读代码/CONFIGURATION】
- 关键点：**原文没有存在会话日志里，而是存在 kernel 自己的持久化 state 里**。我们无法照抄"存会话日志"（DSH 不允许写自定义事件），必须外置存储——这是本设计最本质的适配点。

### 1.6 前缀缓存友好（最容易被误解的一条）

- 论文口号是"append-only，压缩只 deactivate **suffix range**，KV 前缀永不失效"（§3.6）。代码里的实现更精确：
  - `prune(messages, state)` 先算出所有 active 块覆盖的消息 id，然后把它们从 wire 消息里删掉；
  - `collectSummaryAnchors()` 为每个 active 块决定插入位置：**`insertAt = 该块 directMessageIds 中最早那条消息的当前位置`**（只有位置解析不到时才回落到 last user message 之前）；
  - `renderSummary()` 生成一条 `role="system"` 的合成消息：`[Compressed conversation section] — <topic>\n<summary>`，插在**原范围开始处**，不是会话头部；`headSummaryAnchors` 只是兼容开关，默认 false；
  - `rebuildMessages()` 还会钉住第一条 user 消息（任务意图）、清理孤儿 tool-call/result/reasoning，保证不会把配对的工具调用拆坏。
  → 也就是说：**被折叠区间之前的字节完全不变**，只有折叠点之后的尾部需要重算。实测生产 cache-read 份额 94.2%（调用间隔 <2 分钟，占 89% 流量）、混合 90.7%，且 84% 的 miss 不是新增长导致的（是 provider TTL/重启），与"折叠点之前稳定"一致。【读论文 §6.3 + 读代码】
- 论文里 nudge 是**临时注入、压完即删**（`hide-compress-calls`），所以 nudge 本身也不留前缀痕迹。
- 对比：任何"把整个历史重写成一条摘要"的 compaction 都会在**第一个被改动的字节**处使前缀失效；DSH 的 checkpoint 也是"整段替换"，但它替换的是被压区间本身，区间之前的系统提示/更早消息仍然稳定——所以 DSH 压缩并不像想象中那样"全炸缓存"；真正炸缓存的做法是**每轮重写 system prompt** 或**把摘要插到头部**。

### 1.7 失败降级

- 触发侧：`maxContextLimitPct=0.75` 以上是"立即压"的压力带；`emergencyThresholdPct=0.95` 触发 emergency 截断（先砍超大工具输出）；`truncate.threshold=0.95`、`terminalEscapeAfter=3` 是最后手段硬截断；生产 4.5 天里 emergency 65 次、terminal truncation **0 次**。【读论文 §5 + 读代码】
- 配置 error 快速失败：`validateConfig()` 校验比例/阈值/tier 顺序（tier3 > tier2）/保留区等，加载时报错而不是运行中炸。【读代码】
- 持久化：`persist.debounceMs=500`、`sessions.max` LRU、`sessions.residentMaxBytes` 软上限、`ccrRetrievalTtlMs` 过期后原始内容不可再取（`acp_retrieve` 指针失效）。【读代码/CONFIGURATION】
- 工具侧：`compress` 支持多范围一次调用、允许拒绝；没有可压范围时 `acp_status` 明确说 `[No specific ranges detected — compress any consumed content.]`，不硬压。【读代码】
- 需要诚实指出：**billion-context 没有公开的"压缩服务整体挂掉"的 fail-open 证据**；它是本地 kernel，没有外部服务依赖。我们的场景多一跳 memory-server + 一次 LLM，必须自己设计 fail-open（见 §3.4）。

### 1.8 它和我们系统的硬约束差异

| 维度 | billion-context | 我们（deepmemory / DSH 0.1.7） |
|---|---|---|
| 接入层 | 代理拦截整个请求/响应 | DSH 插件钩子：`system-prompt/assemble`、`session/event`、工具面 |
| 压缩执行者 | kernel + 模型写摘要 | DSH `compaction-basic`（LLM 摘要）已在跑；我们不接管 |
| 状态存放 | 自己的 session state（磁盘） | **不能写会话日志自定义事件**（`session.append` 拒绝未知类型）→ 只能 memory-server DB 或文件 |
| 注入位置 | 直接改 wire 消息（原位插 system summary） | 只能通过 `systemPrompt.context()` 在**对话尾部**追加 runtime-context 快照（前缀稳定），或工具返回 |
| 摘要来源 | 模型在 `compress` 工具调用里现场写 | 可从 `compaction/summary` 事件拿到 DSH 摘要；也可另调 memory-server 的 LLM |
| 预算 | 摘要进入上下文，是窗口的一部分 | 注入有 `INJECT_BUDGET_CHARS=2600+300` 硬预算，和 12 条记忆共享 |
| 可逆 | block→state 内原文，`toFile` 支持 | 外置句柄 + `memory_call(layer='expand')`，可加 expand-to-file |

> 已确认不采用的方向：`deferLoading` / `defer_loading` 对我们无效（baseline 工具不进 tool-addition、DeepSeek 也不认），本设计全程不用它。

---

## 2. 我们照搬什么 / 不照搬什么

### 2.1 照搬（结构性、可验证）

| # | 机制 | 为什么照搬 | 落地形态 |
|---|---|---|---|
| A1 | **increment-only + 覆盖账本** | 这是"不重复"的机械保证：每段原文只折一次；`shadowedSeqs` 本身就给出了"这次新增了哪些事件" | `compression_covered(session_id, seq)` 唯一键；`compress()` 前先过滤已覆盖；空增量直接 skip |
| A2 | **block 数据模型 + lineage** | 可逆、多代、审计都需要稳定 id 和来源范围 | `compression_blocks` 表（见 §3.2）；`direct_block_ids` 留给 tier-2 |
| A3 | **原位插入 / append-only 渲染** | 前缀缓存友好；DSH 的 runtime-context 恰好是尾部追加，天然满足 | 注入只追加新块、绝不重排/重编号；预算不足时用**粘性 floor**（§3.6） |
| A4 | **decompress 的三种语义** | 默认还原一层、`full` 还原原文、`toFile` 不膨胀上下文——正好对应我们的 `layer='expand'` 设计 | `expand(handle, {mode:'summary'|'source', dest:'inline'|'file', cursor, maxChars})` |
| A5 | **growth-gated 触发（消费后增量）** | 避免"任务正在用的内容被压"；也避免高频小折 | 以 DSH `compaction/summary` 作为触发点（它本身就是压力驱动）；只折它 `shadowedSeqs` 中的**新增 raw 事件** |
| A6 | **压缩摘要工艺（doctrine 的 KEEP/DROP）** | 质量主要来自这里，且是纯 prompt、可版本化 | `BLOCK_SUMMARY_PROMPT`（原型里已写），版本号跟 Compressor 绑定 |
| A7 | **受保护区** | 压缩工具调用本身、最近用户消息、首条用户消息、最近 N 条/N tokens | 增量过滤掉 checkpoint 节点；首条 user 消息不折（DSH 面已保证）；`preserveRecent*` 用 DSH 默认值即可 |
| A8 | **块目录 / status 视图** | 用 ~10 token/块 的目录代替大段摘要，模型按需 expand——解决我们的预算瓶颈 | `renderInjection({mode:'map'})`，只显示 `bN=a–b` + 最近未覆盖块的摘要（BC `BLOCK_MAP_MAX_SHOWN=8`） |

### 2.2 不照搬（有明确理由）

| # | 机制 | 不照搬理由 |
|---|---|---|
| N1 | 代理/launcher 拦截请求与改写流式响应 | 我们在 DSH 插件层有钩子；代理会引入额外进程、TLS/MITM、和已有 3081/3091 拓扑冲突 |
| N2 | 注入 4 个上下文管理工具 | 工具面越多越贵；我们已经有 `memory_call`，只需加 `layer='expand'`，不需要 `compress`（DSH 自己会压缩）、不需要 `acp_status`（可并入 `memory_call` 或状态行） |
| N3 | 模型自决"何时压" | DSH 的 `compaction-basic` 已经是压力触发 + LLM 摘要；我们再插一套触发会双重压缩（billion-context 自己也要 `auto:false` 才能避免）。我们只在 DSH 已决定压缩后做**增量记账/可逆化** |
| N4 | 把会话原文状态放在代理内存/自有 state | 插件进程会重启；DSH 不许写自定义事件。必须外置到 memory-server（唯一已有持久化、已有 token 鉴权、已有备份的组件） |
| N5 | `headSummaryAnchors`（把摘要放头部） | 会破坏前缀缓存；DSH 的 runtime-context 本来就在尾部追加 |
| N6 | `tiers.tier2Trigger=1000 块` | 生产 3.5% 的稀有路径 + 我们的注入预算只有 2600 字符；先做单层块 + 预算裁剪，tier-2 作为容量上限后的 P2 |
| N7 | 把完整 block 汇总放进 `topic_summaries` | 会污染现有链、破坏旧会话兼容；新表 + 可空列/独立表更安全（ARCHITECTURE §6.3 已建议） |
| N8 | "压缩摘要链"永远置顶注入 | DSH checkpoint 已经在上下文里；同样的内容再注入一次就是重复。用 map 模式 + 按需 expand，或只注入 checkpoint 未覆盖的新块 |

### 2.3 一个重要的诚实结论

DSH 的 `compaction-basic` 与 billion-context 的"fold"在**结构上不是一类**：

- DSH：`replace(span) → 一条 consolidated checkpoint（合并旧 checkpoint + 新内容重写）`。它的摘要 prompt 原文就要求："If the conversation already contains a `<compacted-summary>` block, it is a PRIOR checkpoint. Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer information into a single consolidated summary"（`dsh-compaction-basic/lib/index.js:244,277`）——**这是设计使然，不是 bug**；
- BC：`deactivate(range) → 在原位置渲染一条**只覆盖该 range** 的 block summary`，旧 block 不被重写。

只要还在用 DSH 的 compaction 语义，"摘要被重新总结"就不可避免（这是它的设计）。我们能做的是：
1. **不让记忆侧再复制这套重复**（增量块 + map 注入 + 去重）；
2. **让被丢掉的原文可逆**（handle/expand）；
3. **可选地**（路线 B，见 §5.4）自己实现 DSH compaction provider，把"整段重写"换成"只折增量"，那才是 BC 语义的真正复刻。

---

## 3. Compressor 接口设计（可直接实现）

原型代码（可运行、无副作用、被验证脚本覆盖）：
- `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/compressor-core.mjs`（521 行）
- `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/verify_proto.mjs`（209 行）

### 3.1 接口

```ts
interface Compressor {
  name: string
  version: string

  // 把"一段尚未被折叠过的原始事件"折成一个可逆块。
  compress(ctx: {
    sessionId: string                 // 完整 session id；绝不用 12 位 topic_id
    workspaceId?: string
    events: SessionEvent[]            // 只含本次新增 raw 事件（已排除 checkpoint 节点）
    seqs: number[]                    // 与 events 对应的 seq；账本会去重
    summary?: string                  // 调用方已有摘要时直接给（如首次压缩复用 DSH 摘要）
    summarizer?: Summarizer           // 否则调用（memory-server LLM / DSH ctx.llm）
    budgetChars?: number              // 摘要预算，默认 900
    topic?: string
  }): Promise<{
    ok: boolean
    skipped?: 'no-new-content' | 'already-covered' | 'duplicate-block' | 'race-lost' | 'no-raw-content'
    handle?: string                   // 不透明句柄；expand 的唯一输入
    summary?: string
    reversible?: boolean
    seqStart?: number; seqEnd?: number
    rawChars?: number; foldedTokensEst?: number
    truncated?: boolean               // 原文超 cap 被截断（绝不谎报完整）
    tier?: 1 | 2 | 3
    reason?: string                   // fail-open 原因
  }>

  // 从外部存储取回原文；不读会话日志。
  expand(handle: string, opts?: {
    cursor?: number                   // 字符偏移
    maxChars?: number                 // 默认 8000，硬上限 20000
    mode?: 'source' | 'summary'       // source=原文；summary=块摘要（默认一层还原语义）
    dest?: 'inline' | 'file'          // file=写临时文件返回路径（BC 的 decompress-to-file）
  }): Promise<{
    ok: boolean
    text: string
    nextCursor: number | null
    totalChars?: number
    unavailable?: boolean             // 句柄不存在/已过期（明确告诉模型，不炸）
  }>

  // 生成注入文本：append-only、预算受限、可 sticky floor。
  renderInjection(ctx: {
    sessionId: string
    blocks?: Block[]                  // 省略则从 store 读
    budgetChars?: number              // 默认 2600（沿用 INJECT_BUDGET_CHARS）
    mode?: 'digest' | 'map'           // digest=逐块摘要；map=块目录+新块摘要
    checkpointCoveredThrough?: number // 当前 DSH checkpoint 覆盖到哪；map 模式据此判"新"
  }): Promise<{
    ok: boolean
    text: string
    kept: number; omitted: number
    floor: number                     // 粘性 floor（单调递增）
    mode: string
  }>

  status(sessionId: string): Promise<{
    ok: boolean
    blocks: number; coveredSeqs: number
    rawChars: number; summaryTokens: number
    activeBlocks: number; floor: number
  }>
}
```

辅助纯函数（原型已实现）：
- `readCompactionEvent(event)`：同时兼容 0.2.x（`source.kind='compact-checkpoint'`）与 0.1.7/v3 日志（`source.kind='plugin', plugin='compact'`）的 checkpoint 形态；解出 `{seqs, range, summaryText, shadowedTokens, compactionId}`。
- `planIncrement({seqs, isCheckpoint, covered})`：拆成 `{raw, checkpoint, refolded}`——增量语义的纯函数核心。

### 3.2 存储模型（memory-server 侧）

新增 3 张表 + 5 个端点，全部 additive，不动 `topic_summaries`：

```sql
CREATE TABLE IF NOT EXISTS compression_blocks (
  handle          TEXT PRIMARY KEY,           -- bh_<sha256(session+seqStart+seqEnd)[:20]>
  session_id      TEXT NOT NULL,
  workspace_id    TEXT DEFAULT '',
  block_id        INTEGER NOT NULL,           -- 会话内单调，渲染用
  seq_start       INTEGER NOT NULL,
  seq_end         INTEGER NOT NULL,
  topic           TEXT DEFAULT '',
  summary         TEXT NOT NULL,
  summary_tokens  INTEGER NOT NULL,
  raw_chars       INTEGER NOT NULL,
  raw_tokens      INTEGER NOT NULL,
  source_zstd     BLOB NOT NULL,              -- 原始事件 JSON（zstd）——可逆的本体
  source_sha256   TEXT NOT NULL,              -- 解压后字节的 sha256，expand 校验
  truncated       INTEGER NOT NULL DEFAULT 0,
  tier            INTEGER NOT NULL DEFAULT 1,
  direct_block_ids TEXT DEFAULT '[]',         -- tier-2 用
  generation      TEXT NOT NULL DEFAULT 'young',
  active          INTEGER NOT NULL DEFAULT 1,
  superseded_by   TEXT,
  created_at      REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cblocks_range
  ON compression_blocks(session_id, seq_start, seq_end, source_sha256);
CREATE INDEX IF NOT EXISTS ix_cblocks_session ON compression_blocks(session_id, seq_start);

CREATE TABLE IF NOT EXISTS compression_covered (
  session_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  handle     TEXT NOT NULL,
  PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS compression_sessions (
  session_id   TEXT PRIMARY KEY,
  workspace_id TEXT DEFAULT '',
  inject_floor INTEGER NOT NULL DEFAULT 0,   -- sticky floor：只增不减
  latest_checkpoint_end INTEGER NOT NULL DEFAULT 0,
  updated_at   REAL NOT NULL
);
```

- `claimSeqs + putBlock` 必须**同一事务**：先 `INSERT OR IGNORE` 到 covered（冲突即已折过），再写 block；这样崩溃/重试最多"少记"，不会"重折"。原型用 `claimSeqs → putBlock` 两步表达同一语义。
- `source_zstd` 用 zstd level 6（实测 3.92×）；单块 `raw_chars` cap 512 KB（超出中间截断 + `truncated=1`，expand 明确告知"非完整原文"）。
- GC：按 `created_at` + session 保留策略（默认 90 天/会话 50 MB），`active=0` 且无引用的块优先；**绝不自动删除有 handle 被引用的块**。
- 安全：入库前复用 memory-server 的 `redactSensitive()`（`server.py:125-216` 区域）对常见凭据模式脱敏；`getBlock(handle)` 必须校验 `session_id` 与请求会话一致（防跨会话句柄猜测）；handle 是不可猜的 20 字节 sha 前缀，但**仍要校验归属**。

端点（Bearer token 与 `/v1/topic-summaries` 同级）：
```
POST /v1/compression/claim            {session_id, seqs, payload?} -> {claimed, already}
POST /v1/compression/blocks           {block} -> {inserted, block}
GET  /v1/compression/blocks?session_id=&active=1 -> {blocks}
GET  /v1/compression/blocks/:handle?cursor=&max_chars= -> {text,next_cursor,total_chars,sha256}
POST /v1/compression/floor            {session_id, seq} -> {seq}
GET  /v1/compression/stats?session_id= -> {blocks,covered,raw_chars,summary_tokens}
```

### 3.3 插件侧集成点（生产 live 文件，行号为当前 1620 行版本）

文件：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`

1. **捕获**：`ctx.on('session/event', ...)` 的 `compaction/summary` 分支（`:823-855`）现在只把 `summary.slice(0,3000)` POST 到 `/v1/topic-summaries`。改为：
   - 保留旧写入（灰度期双跑，旧链仍可用）；
   - 新增 `captureIncrement(session, event)`：用 `event.data.shadowedSeqs`（0.1.7/0.2.1 都有，已验证）→ `session.eventAt(seq)` 或 `session.snapshotEvents(min,max)` 取原始事件；排除 `isCheckpointEvent()`（两种 source 形态）；只保留 message 类事件；同步抓取文本到内存队列（廉价），**不在钩子里 await LLM**。
   - 由异步 worker（`setTimeout`/下一回合）做：调 memory-server LLM 摘要 → `POST /v1/compression/blocks`；失败**只记日志**，下个压缩点重试（队列不持久，进程重启丢块——可接受，绝不影响回合）。
2. **注入读取**：`refreshMemoryCache` 的摘要链段（`:700-714`）改为：
   - `mode==='map'`（推荐默认）：`GET /v1/compression/blocks` → `compressor.renderInjection({mode:'map', checkpointCoveredThrough})`；文本 ≤ `compression.inject_budget_chars`（默认 2600，新增配置；不要和 12 条记忆抢同一个常量）；
   - 旧会话/无块：`fallback` 到现有 `/v1/topic-summaries` 路径（但**只取最新 1 条**，消除 5 条重复；见 §5.2 阶段 1 的"止血"）。
3. **展开**：`memory_call` 工具（`:1361+`）增加 `layer='expand'`；参数 `handle`、`cursor`、`max_chars`、`mode`、`dest`；返回 `next_cursor`；`dest='file'` 时写 `/tmp/deepmemory-expand/<handle>-<offset>.txt`（0600）并返回路径 + 200 字符预览，供 `read` 工具按需读——这是 BC `decompress-to-file` 的等价物。
4. **配置**：沿用 `compression.*`（schema 已存在：`compression.enabled/min_turns/summary_model/inject_summary/trigger_bytes/retain_head`，见 `/www/deepmemory-v063-deploy/memory-server/config_schema.json`）。新增：
   `compression.blocks_mode`（off|shadow|map|digest，默认 off）、`compression.inject_budget_chars`（2600）、`compression.summary_budget_chars`（900）、`compression.source_max_chars`（524288）、`compression.block_summary_model`（空=沿用 `summary_model`）、`compression.max_blocks_per_session`（400）、`compression.expand_max_chars`（20000）。
5. **fail-open 包装**（**不可协商**）：`compress/expand/renderInjection/status` 全部 try/catch；超时（存储 3s、摘要 20s）；任何异常返回 `degraded:true` 并让调用方走旧路径；**绝不让压缩逻辑的异常冒泡到 `session/event` / `assemble` 钩子**（0.1.5 事故教训：钩子抛错 = 每个回合炸）。
6. **子 agent**：沿用 `isDelegatedSession(session)` 直接 return（`:821`），不折桶、不落块。
7. **server_url 防护**：测试机 6240 绝不能指向生产 6230（历史事故）；灰度期先只开 `shadow`（只写不读），确认写入目标正确。

### 3.4 增量语义（可证明的不重复）

```
新压缩事件 E (type=compaction/summary)
  ├─ seqs = E.data.shadowedSeqs
  ├─ checkpointSeqs = seqs 中 source.kind ∈ {compact-checkpoint, plugin/compact}
  ├─ rawSeqs = seqs \ checkpointSeqs
  ├─ fresh = rawSeqs \ covered(sessionId)      ← 覆盖账本
  └─ if fresh 为空: skip（记录 metrics；这就是"重复压缩点"被机械丢弃）
     else: 取 fresh 的事件文本 → 摘要 → 一个 block，事务内 claim+insert
```

- **每个 raw seq 只属于一个 block**（唯一索引 + 账本；实测 125 次压缩 raw refold = 0，`seq-ledger-disjoint` 7/7 通过）。
- **checkpoint 节点永不进入我们的摘要输入** → 从结构上消灭了"把上一份摘要几乎等量重写一遍"的合并式重复（实测输入中旧 checkpoint 文本量中位 95.75%）。
- 幂等：`(session_id, seq_start, seq_end, source_sha256)` 唯一；重复投递返回 `duplicate-block`。
- 首次压缩的特殊处理：由于 DSH 第一次压缩的 `summary` 是真正的全量摘要，直接用它作为 block#1 的 summary（**零额外 LLM**）；只有当 `fresh` 非空且已有 block 时才调用增量摘要器。这能把 LLM 调用次数压到"每新增一段一次"，且避免对 169K token 的首次增量做摘要（首次压缩真实日志有 169,361 tokens，超过很多模型的友好摘要区）。
- 若某次增量很大（>50K tokens，按 `estTokens` 估算），先按 seq 切成 ≤50K 的 chunk 分别摘要，再做一个 200 字符以内的 merge（map-reduce）；失败则只保留 chunk 中前一半并标 `truncated`——绝不丢块。

### 3.5 前缀缓存友好（我们的版本）

三段论证：

1. **注入位置**：继续用 `systemPrompt.context()`（live `:753-768`）。DSH 的 runtime-context 投影（`dsh-agent-loop` 的 `RuntimeContextProjection` 类，`lib/index.js:239-330`）在内容变化时**在已缓存历史之后追加**一条 `user/message`（source kind=`runtime-context`），并明说"appends changed nonempty text after the cached history"；只有系列不连续/能力不支持时才替换（会破坏前缀）。我们不改这个机制，只在文本内容上保证 append-only。
2. **渲染 append-only**：`renderInjection` 只追加新块；旧块在预算内时字节不变。预算超限时用**粘性 floor**：一旦某个块被挤出，它的 `inject_floor` 只增不减，后续渲染永不把它放回。这样 `text(k)` 是 `text(k+1)` 的前缀（原型 92/92 通过），只在 floor 前进的那一次产生一次有界改动。数字 id 单调、不重排、不带时间戳/使用率（避免哈希漂移）。
3. **DSH 侧不动**：DSH 的 checkpoint 仍是原位替换（§1.6），我们不重写历史消息、不改 system prompt、不把摘要插头部。因此压缩事件本身的前缀稳定性（实测折叠点之前 token 占比中位 **85.2%**，74%–96.5%）不被我们破坏；我们的注入变化只发生在尾部。

> 补充：如果未来把注入改到 system prompt（为省 token），会立刻破坏前缀缓存——**不要做**。

### 3.6 注入渲染（两种模式）

- `digest`：`[摘要#b12 seq 900–1200] <summary>`，预算内尽量多放（新块优先分配预算，输出按时间升序）。
- `map`（推荐默认）：`Active blocks (24): b1=10–371 · b2=371–741 · …`（只显示最近 8 个，`…+16 older`）+ 当前 DSH checkpoint **未覆盖**的新块摘要（按预算）+ 一句 expand 指引。
  - 依据：BC 的 `formatBlockMap()`（`BLOCK_MAP_MAX_SHOWN=8`）与 `acp_status` 就是"目录先行、按需展开"；我们实测 map 模式 444–2597 字符，目录永远能放下（原型 `mapHasIds` 全 true）。
  - `checkpointCoveredThrough`：取最近一次 `compaction/summary` 的 `shadowedRange.end`；`seqEnd <= 它` 的块只进目录不给摘要（DSH checkpoint 已覆盖），只有新块给摘要 → 避免与 DSH 上下文重复。
- 预算优先级：**目录 > 最新块摘要 > 更旧块摘要**；目录永远不裁剪。
- 文本里绝不出现"使用了百分之多少"之类数字（BC 的 "No ratio disclosure"）。

### 3.7 成本与容量（实测/推断分开）

- 摘要 LLM：每次"有新内容"的压缩 1 次（首次 0 次）；8 份真实日志共 125 次压缩、99 个有效增量块、其中首块 7 个 → 约 **92 次增量摘要 / 7 个会话**（平均 13 次/会话，跨越数周）。按 `deepseek-flash` 计价远低于现有每回合抽取。【实测计数 + 推断成本】
- 存储：123 块原文 31.9 MB → zstd **8.15 MB**（3.92×）；平均 66 KB/块（zstd）。会话上限 50 MB 可容纳 ~750 块，远大于实测 28 块/会话最大值（`session-0655` 28 次压缩）。【实测】
- 注入：legacy 1267 字符（≈330 tokens）；digest 模式在 2600 预算下保留 9 块（240 字符代理）；map 模式 444–2597 字符。**真实 LLM 摘要中位 185 tokens（BC 数据）时，2600 字符只能放 3–4 块** → 必须用 map 或把摘要预算降到 ~400 字符/块，否则预算会被摘要吃光。这是本设计最需要灰度调参的地方。【读论文 + 实测】

### 3.8 与 `ARCHITECTURE.md §6.2` 草案的差异

- 草案 `Compressor.compress(ctx)` 允许只给 `content: string`；本设计改为 `events + seqs`，因为**增量过滤和可逆存原文都需要逐事件 seq**（不能只有拼好的字符串）。
- 草案 `expand(handle, window)` 增加 `mode/dest`（对应 BC 的 tier-up/full/toFile）。
- 草案没有 `renderInjection`；本设计显式加入，因为"注入怎么拼"是前缀缓存与预算语义的所在，不能留给调用方即兴发挥。
- 新增覆盖账本 `compression_covered`——这是"不重复"的唯一机械保证，比"摘要提示词里叫模型别重复"可靠得多。

---

## 4. 最小验证（只在测试机数据上，离线）

### 4.1 方法与边界

- 数据：测试机 home `/www/dsh-test-home/sessions`（0.2.1-rc.2，v4 日志）。动手前确认：`ls`/`du` 稳定，`sessions` 目录 2.0 GB、756 个 `.zstd`；session 文件 mtime 到 2026-10-21 18:48；未发现 rsync/cp 克隆进程（只有一次 `timeout 120 find`）。**只读**；为防竞态，先 `cp -a` 8 份日志到 `/tmp/bc-exp/sessions/`，分析均基于副本。
- 另外从**生产** home `/www/dsh/home/sessions` 只读复制了 1 份 v3 日志（`session-0655645d…`，12.4 MB）做 0.1.7 形态对照；未写生产。
- 不修改任何插件；不重启进程；不写 memory-server。实验 DB 在 `/tmp/bc-exp/blocks.db`。
- 摘要质量**未测**：本验证用确定性抽取式代理（每块 ≤250/900 字符）代替真实 LLM 摘要，用于证明**结构/体积/可逆/幂等**，不代表摘要质量。见 §4.6。

### 4.2 数据集

| 日志 | 会话 | 压缩次数 | 原文 tokens（估） |
|---|---|---|---|
| `session-7fc0…`（Minecraft 实验） | 1 | 24 | 2,120,578 |
| `session-4de6…`（SRTP 实验） | 1 | 11 | 794,213 |
| `session-0655…`（测试 v4） | 1 | 28 | 1,767,763 |
| `626abc82…`（Minecraft） | 1 | 13 | 773,048 |
| `session-edfc…`（工作区） | 1 | 8 | 667,196 |
| `session-feee…`（Remote Control） | 1 | 10 | 726,785 |
| `session-b512…`（文献） | 1 | 6 | 161,063 |
| `prod session-0655…`（生产 v3 对照） | 1 | 25 | 878,912 |
| **合计** | **7 个不同会话 / 8 份日志** | **125** | **7,889,558** |

### 4.3 实测结果

**（1）重复性（用户问题的量化）**

| 指标 | 中位 | 范围 |
|---|---|---|
| 新摘要**输入**中旧 checkpoint 文本量 / 新摘要长度（reconsolidated_ratio） | **95.75%** | 80.0%–98.5% |
| 最近 5 条摘要**全文** 8-word shingle 重叠（取两两最大） | **12.55%** | 7.8%–49.4% |
| 最近 5 条注入中，**首行完全相同**的对数 | **80/80** | — |
| 去掉 `[摘要#N]` 后最长公共前缀 | **214 字符** | 105–240（240=切片上限） |
| 最近 5 条注入的 40 字符 shingle 重复率 | **23.8%** | 17.3%–37.1% |
| 后一份摘要"代表"的源事件集合与前一份的重叠 | **91.2%** | 74.4%–97.8% |
| 单块压缩比（原文 tokens / 摘要文本 tokens） | **15.75×** | 6.15×–24.62× |

真实例子（生产 v3 日志）：两次连续压缩——
`compaction#1: shadowedRange 16–934 (364 events, 169,361 tok)`；
`compaction#2: shadowedRange 1998–1998 (1 event, 2,759 tok)`，而这 1 个事件就是 compaction#1 刚生成的 checkpoint 节点；两条摘要的 `## Primary Request and Intent` 段落几乎逐字相同。【实测】

**（2）增量分块（不重复证明）**

- 125 次真实压缩中，原始事件被重复折叠的次数：**0**（DSH 自己也不重折 raw）。
- 按本设计切分后：Python 侧 123 个可逆块（8 份日志各自独立会话）；JS 侧把 0655 的生产 v3 与测试 v4 合并为同一会话，99 个块落库，后到的 26 个重复范围被覆盖账本拒绝。`seq-ledger-disjoint` **7/7** 通过；`duplicate-compress-skipped` **99/99** 通过。
- 结构证明：每个 `compression_covered.seq` 唯一；block 的 `seq_start/seq_end` 来自 `fresh` 的最小/最大值；checkpoint 节点不进入摘要输入。→ **不存在两条注入块覆盖同一段原文**。【实测 + 结构】

**（3）可逆（expand 取回原文）**

- Python 端：123 个块原文写入 SQLite（zstd），`expand` 全部读回并 sha256 比对：**123/123 一致**；原文 31,928,900 B → 8,151,141 B（**3.92×**）。
- JS 端：`compressor-core.mjs` 对 99 个真实块做压缩 + 分页 expand（`maxChars=8192`，共 2,465 次 expand 调用，19,776,286 字符），**99/99 与存储原文 sha256 一致**；缺句柄返回明确的 `unavailable` 文本；store 抛错时 `compress` 返回 `{ok:false, reason:'exception'}` 而不抛异常。
- 分页边界：UTF-16 代理对不被劈开；`next_cursor` 连续；越界/负数归一化。

**（4）注入体积与 append-only**

| 场景 | 字符数 | 说明 |
|---|---|---|
| 现状（5 条 ×240） | 1,213–1,269 | 约 330–450 tokens，含 ~24% 重复内容 |
| 本设计 digest（240 字符/块代理） | 1,621–2,441 | 携带 6–9 个**互不重复**的块 |
| 本设计 map（8 块目录 + 新块摘要） | 444–2,597 | 目录永远保留；老块不再占摘要预算 |
| append-only 性质 | **92/92 通过** | 新块只追加；floor 前进时一次性有界变化 |

**（5）前缀缓存稳定性（DSH 侧，与本次改动无关）**

- 每次压缩"折叠点之前的 token / (之前 + 被折)"中位 **85.2%**（74.1%–96.5%），第一压除外（0%）；即 DSH 原位 checkpoint 天然保留大部分前缀。→ 我们的注入只应发生在尾部，不能改历史/system prompt。

### 4.4 复现

```bash
# 1) 从测试机只读复制（勿写 sessions 目录）
mkdir -p /tmp/bc-exp/sessions
cp -a /www/dsh-test-home/sessions/<project>/<session>/session.v4.jsonl.zstd /tmp/bc-exp/sessions/
# 2) 分析（只依赖 /root/miniconda3/bin/python 的 zstandard；sqlite3 标准库）
/root/miniconda3/bin/python \
  "/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/analyze_sessions.py" \
  /tmp/bc-exp/sessions/*.zst --out /tmp/bc-exp/report-all.json --export-blocks /tmp/bc-exp/blocks.jsonl
# 3) 原型接口验证（Node 22.23.2，无外部依赖）
cd "/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor"
node verify_proto.mjs /tmp/bc-exp/blocks.jsonl
# 产物：/tmp/bc-exp/report-all.json、/tmp/bc-exp/verify-proto.json、/tmp/bc-exp/blocks.db
```

### 4.5 局限（必须写清楚）

1. **没跑真实 LLM 摘要**：体积/质量/块摘要长度都是代理值；真实摘要长度、压缩比、信息保留率需要在测试机用 `deepseek-flash` 做 A/B。
2. **没在 0.2.1 运行时跑插件**：只验证了 0.2.1 的包 API（`dsh-session@0.2.1-rc.2` 的 `eventAt`/`snapshotEvents`、`dsh-compaction@0.2.1-rc.2` 的 `shadowedSeqs`）与真实 v4 日志形态；插件行为未改、未重启。
3. **只覆盖 7 个会话**，且是单机、单用户样本；长尾（>100 次压缩的马拉松会话）没有覆盖。
4. `topic_summaries` 生产库对 `session-0655` 只有 8 行，而生产 v3 日志有 25 次压缩，且行 `created_at` 延到 2026-10-12/10-18（晚于日志 mtime 09-28 22:53）——**未查清**（可能是测试机写生产库、插件部署窗口、或 topic_id 碰撞）。列为不确定项 §6-U4。

---

## 5. 与现有摘要链的迁移方案（灰度 / 回滚）

### 5.1 配置项（server settings，`compression.*`）

| key | 默认 | 作用 |
|---|---|---|
| `compression.blocks_mode` | `off` | `off`=完全旧路径；`shadow`=只写块不读（双跑对数）；`map`=map 注入；`digest`=逐块摘要注入 |
| `compression.inject_budget_chars` | 2600 | 摘要/目录注入预算（独立于 12 条记忆的 `INJECT_BUDGET_CHARS`） |
| `compression.summary_budget_chars` | 900 | 单块摘要上限 |
| `compression.source_max_chars` | 524288 | 单块原文 cap（超出截断并标记） |
| `compression.block_summary_model` | 空 | 空=沿用 `summary_model`；默认 `deepseek-flash` |
| `compression.max_blocks_per_session` | 400 | 触发告警/停止新建 |
| `compression.expand_max_chars` | 20000 | 单次 expand 硬上限 |
| 既有 `compression.inject_summary` | true | 旧链接入开关（保留） |

### 5.2 阶段（严格测试机 → 生产；每一步都要用户同意才能重启）

| 阶段 | 内容 | 是否改代码/重启 | 回滚 |
|---|---|---|---|
| **P0 止血** | 不改代码：若只想立刻消掉用户看到的 5 条重复，可把读取侧改为"只取最新 1 条摘要"（`limit=1`）。这是最小改动，但只是止血，不解决不可逆与重写衰减。 | 改插件 → 需重启（**先问用户**） | 改回 `limit=5` |
| **P1 存储先行** | server 加 3 张表 + 5 端点 + config schema（additive，旧行为不变） | server 重启（测试机 6240 → 生产 6230）；需用户同意 | 停用端点，表可留 |
| **P2 shadow 双跑（测试机）** | 插件在 `compaction/summary` 捕获增量并写块（异步、fail-open）；注入仍用旧链；记录 blocks/失败率/存储量 | 插件改动 → 重启 3091 | `blocks_mode=off`（配置热读，60s 内生效） |
| **P3 map 灰度（测试机新会话）** | 新会话走 map 注入；旧会话 fallback 到"最新 1 条"；`memory_call(layer='expand')` 上线 | 插件+工具 schema 改动 → 重启 | `blocks_mode=off`（立即回旧链） |
| **P4 生产灰度** | 同 P3，只对新会话；观察 1–2 周指标（§5.5） | 同 P3 | 同上；旧链数据一行未动，随时可回 |
| **P5（可选）digest 模式** | 有更多预算/评测后，对新会话用 digest（更完整的摘要） | 配置 | 切回 map |
| **P6（长期，独立项目）** | 路线 B：自定义 DSH compaction provider（见 §5.4） | 新插件 + 重启 | 卸载 provider，恢复 `compaction-basic` |

**顺序铁律**（`dsh-deepmemory/AGENTS.md`）：测试机 3091 验证通过 → 才允许生产；每次插件改动先 `node --check` + ESM 冒烟 + `session.models` 断言 + 真实 prompt 冒烟。**任何重启先征得用户同意**。

### 5.3 回滚

- 配置级：`compression.blocks_mode=off` → 注入立刻回旧路径；`inject_summary=false` → 连旧摘要链也不注入。
- 数据级：新表独立，`DROP`/归档不影响 `topic_summaries`、记忆、抽取。
- 代码级：插件保留 `.bak-*`；回滚后旧链从现有行继续可用（旧行没有 handle，按 legacy 处理）。
- 注意：**已写入的块不会因为回滚而消失**；expand 句柄仍可用，适合"先回滚注入、保留可逆能力"。

### 5.4 路线 B（自定义 DSH compaction provider）评估

**可行性（读代码）**：
- DSH 的 seam 是 `ctx.compaction`（`@deepseek-ai/dsh-compaction` 的 `CompactionEngine extends Service`，`lib/types/index.js`），**"A successful run replaces the selected surface span with one summary node"**；调用方是 `ctx.compaction.compactNow(...)` 与自动路径的 `compactIfNeeded(...)`。
- `compaction-basic` 是唯一实现：`selectCompactableRange()` 从第一个非 system surface node 开始、保留 recent tail（`retainRatio=0.16`，`W-O` 的 16%）、不拆 tool 对；`summarize()` 是"the sole hook"，可由子类覆盖；`compactIfNeeded()` / `compactRegion()` 也是实例方法，子类可覆盖。
- 因此理论上可以写一个 provider：选择范围时**跳过已有的 checkpoint 节点**（只折 checkpoint 之后的新增段），每次产出一条**只覆盖该段**的 checkpoint（DSH 的 checkpoint 文本会带固定 preamble，但语义仍是增量块），从而在 DSH 上下文里复刻 fold、不再整段重写。
- **但**：每次 compaction 只能产出一个 summary 节点（seam 合同），无法像 BC 一样一次产出多个 block；checkpoint 是 user 消息、有固定前缀；tier-2 需要自己实现（在 provider 内做"块→块"折叠，但 surface 上表现为又一 checkpoint）。**风险高、需要单独设计与充分测试**，不建议与 P1–P4 混做。

**建议**：先把路线 A（记忆侧增量可逆 + map 注入 + expand）跑通；如果 P4 后仍观察到"checkpoint 重写导致质量衰减"（用 recall probe/日志审计判断），再立项 B。

### 5.5 验收指标

- 重复：最近 5 条注入首行完全相同对数 = **0**（现在 10/10）；注入文本 40 字符 shingle 重复率 < 5%。
- 增量：`raw_refold_total = 0`；`skipped/already-covered` 比例可解释。
- 可逆：抽样 20 个 handle，`expand` sha256 全等；expand 失败率 < 0.1%（且失败有明确文本）。
- 故障：人为停 memory-server 30s，回合仍正常完成（fail-open），无 `turn/end reason=error`。
- 缓存：prefix cache 命中率不低于基线（用 DSH usage 的 cache-read 份额对比 1 周）。
- 体积：`compression_blocks` 总大小/会话 < 50 MB；注入 tokens 不高于现状 +10%。

---

## 6. 风险与不确定项

| # | 风险 | 级别 | 证据/缓解 |
|---|---|---|---|
| R1 | **过期/错乱句柄**：memory-server DB 丢失或块被 GC，`expand` 取不回原文 | 中 | expand 返回明确 unavailable 文本（不炸）；块与原文同库；GC 只删无引用块；备份沿用 memory-server backup |
| R2 | **摘要 LLM 失败/超时** | 中 | fail-open：不建块、不注入、走旧链；后台重试；绝不阻塞回合（0.1.5 事故教训） |
| R3 | **存储膨胀**（工具输出含大文件/二进制） | 中 | 单块 512KB cap + 截断标记；会话 50MB cap；zstd 实测 3.92×；GC 90 天 |
| R4 | **敏感信息落库**（token/密码出现在工具输出里） | 高 | 入库前 `redactSensitive`；handle 归属校验；库文件 0600（现有 memory.db 权限）；与现有记忆库同一安全边界，不新增暴露面 |
| R5 | **注入预算与记忆抢额度** | 中 | 新增独立 `compression.inject_budget_chars`；map 模式优先目录；先 map 后 digest |
| R6 | **插件多副本漂移**（live 1620 / 仓库 1155 / 暂存 1751 / 测试 1748） | 高 | 只改 live 一份并同步回仓库快照；改动前记录 md5；测试机副本只通过 sync 脚本更新（本任务不动） |
| R7 | **重启需求**：插件改动必须重启 dsh-web | 高 | 按约束**先征得用户同意**；避开 running 会话（AGENTS.md 红线 2） |
| R8 | **0.1.7 vs 0.2.1 checkpoint source 形态不同**（`plugin/compact` vs `compact-checkpoint`） | 中 | `isCheckpointEvent` 同时识别；用真实 v3/v4 日志回归；未来版本变化时 fail-safe 为"识别不出就不折" |
| R9 | **`shadowedSeqs` 缺失/语义变化**（老 0.1.x 或未来版本） | 中 | 事件字段缺失 → 跳过本块（fail-open）；不猜范围 |
| R10 | **topic_id 12 位截断碰撞**（ARCHITECTURE §5.6） | 中 | 新表用完整 session_id；handle 绑定完整 session；旧链保持原样 |
| R11 | **首次压缩 169K tokens 摘要不可行** | 中 | 复用 DSH 首次摘要文本作为 block#1（零 LLM）；后续增量才调 LLM |
| R12 | **未经真实 LLM 评测**：增量摘要质量未知 | 高 | 这是 P3 灰度必须做的 A/B（recall probe + 人工审计）；本设计不宣称质量提升，只宣称结构/可逆/缓存属性 |

不确定项（明确不知道）：
- **U1**：DSH 0.1.7 的 `session.eventAt()` 在 `session/event` 钩子执行期间读取被 shadow 的事件是否 100% 可用——包 API 存在（`dsh-session@0.1.7-rc.2/lib/types/index.js:488/501`），但未在运行中的 0.1.7 进程里实测；P2 必须先在测试机做一个最小验证。
- **U2**：0.2.1 的 compaction 事件是否仍带 `shadowedSeqs`——只核对了类型定义（`dsh-compaction@0.2.1-rc.2/lib/types/types.d.ts:35-43`）与 v4 日志（有），未跑运行时。
- **U3**：`compaction/prune`（工具输出修剪）也会 shadow 事件；本设计 MVP 不折它（只处理 `compaction/summary`），因此被 prune 的原文仍然不可逆。后续可作为 P2 扩展（同型 handle，摘要 stub）。
- **U4**：生产 `topic_summaries` 对 session-0655 只有 8 行 vs 日志 25 次压缩、且 `created_at` 晚于日志 mtime——原因未查（可能测试机写生产库 / 插件部署窗口 / topic_id 碰撞）。
- **U5**：billion-context 在 `dist/index.js` 的 `tier2Trigger=1000` 是"块数"还是"token 数"——代码判据是 `activeBlocks.filter(tier===1).length >= tier2Trigger`（块数），但生产 tier-2 60/1711 与 1000 阈值不一致，说明生产配置可能覆盖或模型自行折块；本设计不依赖该值。
- **U6**：本报告所有体积数字基于确定性抽取代理，不是 LLM 摘要；真实摘要中位 185 tokens（BC 数据）只作参考。

---

## 附录 A：产物与证据索引

| 路径 | 说明 |
|---|---|
| `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/analyze_sessions.py` | 会话日志分析器（zstd 多帧解码、compaction 事件解析、重复度/覆盖度/缓存稳定性/可逆存储） |
| `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/compressor-core.mjs` | `Compressor` 原型（compress/expand/renderInjection/status + MemoryStore + HTTPStore 契约 + 摘要 prompt） |
| `/www/deepseek harness workspace/dsh-deepmemory/experiments/bc-compressor/verify_proto.mjs` | 真实块上的接口验证（可逆/幂等/append-only/粘性 floor/隔离/fail-open） |
| `/tmp/bc-exp/report-all.json` | 8 份日志的完整分析结果（含每会话逐块指标） |
| `/tmp/bc-exp/verify-proto.json` | JS 原型验证结果 |
| `/tmp/bc-exp/blocks.jsonl` | 导出的真实块（53 MB，含每事件原文；验证输入） |
| `/tmp/bc-exp/sessions/` | 8+1 份日志副本（只读原件） |
| `/tmp/bc-exp/blocks.db` | 可逆实验 SQLite（123 块） |

代码引用：
- live 插件：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`
  - 摘要注入读取 `:700-714`；预算截断 `:726-730`；`systemPrompt.context` `:753-768`；`compaction/summary` 写入 `:823-855`；`memory_call` `:1361+`；子 agent 短路 `:821`。
- 生产 memory-server：`/www/deepmemory-v063-deploy/memory-server/server.py`
  - `topic_summaries` schema `:511-515`；GET `:3400-3410`；POST `:3844-3862`；`llm_chat` `:864`；`/v1/topic-summaries/auto` `:3813+`。
- 生产 config schema：`/www/deepmemory-v063-deploy/memory-server/config_schema.json`（`compression` 段已存在）。
- DSH 0.1.7：
  - `/opt/dsh-rc2-core/node_modules/.pnpm/@deepseek-ai+dsh-compaction@0.1.7-rc.2_af1b37758173bb780f5029f3df48da13/node_modules/@deepseek-ai/dsh-compaction/lib/types/types.d.ts:35-43`（`compaction/summary` 字段）
  - `/opt/dsh-rc2-core/node_modules/.pnpm/@deepseek-ai+dsh-session@0.1.7-rc.2_.../node_modules/@deepseek-ai/dsh-session/lib/types/index.js:488,501`（`eventAt`/`snapshotEvents`）
  - `/opt/dsh-rc2-core/node_modules/.pnpm/@deepseek-ai+dsh-compaction-basic@0.1.7-rc.2_.../node_modules/@deepseek-ai/dsh-compaction-basic/lib/index.js`（`selectCompactableRange` 约 `:407`；`summarizeWithLlm` prompt 约 `:244`；checkpoint 合并指令约 `:277`；`compactIfNeeded` 约 `:914`；`compactionRetries` 默认 1 在 `:82`）
  - `/opt/dsh-rc2-core/node_modules/.pnpm/@deepseek-ai+dsh-agent-loop@0.1.7-rc.2_.../node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js:239-330`（`RuntimeContextProjection`：snapshot 尾部追加）
- DSH 0.2.1 测试核心：`/opt/dsh-rc2-test-core/node_modules/.pnpm/@deepseek-ai+dsh-session@0.2.1-rc.2_.../lib/types/index.js:559,572`；`@deepseek-ai+dsh-compaction@0.2.1-rc.2/lib/types/types.d.ts:35-43`。
- billion-context：`/tmp/billion-ctx/package/dist/index.js`（`prune`/`collectSummaryAnchors`/`renderSummary`/`rebuildMessages`/`advanceSurvival`/`createInitialState`/`resolveConfig` 等函数段）；`/tmp/billion-ctx/package/CONFIGURATION.md`（`compress.*` 参数表 `:232-301`）；`/tmp/billion-ctx/package/dsh.bundle.patch.yml`。

## 附录 B：与 `ARCHITECTURE.md §6` 的关系

- `ARCHITECTURE.md §6.2` 的 `Compressor` 草案是"预留接口"，本报告把它落成可运行原型，并按 DSH 的真实事件模型修正了输入（`events+seqs`）与输出（增加 `renderInjection`/`status`/`mode`/`dest`）。
- `§6.3` 的落点（`topic_summaries` 加可空 `handle` 列）被本设计**改为独立表**：原因（a）旧表无范围/无 tier，（b）需要 `covered` 账本做唯一约束，（c）生产/测试两份 server 已分叉，改旧表风险更高。
- `§6.4` 的"不要动 `systemPrompt.context`+assemble 双写机制"被严格遵守：本设计只增加尾部注入文本，不改装配机制。
