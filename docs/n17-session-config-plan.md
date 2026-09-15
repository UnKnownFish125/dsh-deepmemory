# N17 会话级配置按会话解析 — 实施方案（只读分析产出）

- 产出日期：2026-09-16
- 分析对象：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`
  （md5 `66340d0756b1899ca9e9f2e2f90eca4c`，988 行，2026-09-15 23:51）
- 后端：`/www/deepmemory-v063-deploy/memory-server/server.py`
  （`get_session_config` L2002 / `set_session_config` L2028 / 路由 L2993 GET、L3515 POST set、L3521 POST reset）
- 本次为**只读分析**：未修改任何既有文件、未重启服务、未写数据库。唯一写出的文件就是本文档。

---

## ① 结论与建议

**结论**

1. N17 属实，且比审核描述更"窄"也更容易修：preset 里真正从**全局 settings** 读取、又被核心链路消费的配置变量只有 **10 个**（`SERVER`/`WORKSPACE`/`EXTRACT_THRESHOLD`/`RECALL_K`/`INJECT_ENABLED`/`INJECT_CARD`/`EXTRACT_ENABLED`/`TOOLS_ENABLED`/`DECAY_RATE`/`EXTRACT_PROVIDER`/`EXTRACT_MODEL`，其中 `WORKSPACE` 是死变量），全部集中在 `loadConfig()`（L216–245）一处赋值。
2. "按会话解析"不需要重构模块级变量本身：**把全局变量降级为"回落快照"，另加一个按 `sessionId` 解析并短 TTL 缓存的 `effectiveConfig(sid)`，结果以参数下发**。模块级 `let` 只被 `loadConfig()` 写（全局语义不变），per-session 路径**零写入**，因此结构上不可能污染其它会话。
3. 数据面已经现成：`GET /v1/config/session?session_id=X` 一次性返回 **defaults+overrides 合并后的全量配置**（实测 2 ms），并额外返回 `overrides` 键名数组。当前 preset 只从中读了 `context_automation.enabled` / `context_automation.memory_completion_k` 两个键（L599–605），其余全部走 21 次串行 `/v1/settings/deepmemory.*`（L203–214、L216–239，N18 隐患）。
4. 建议**分 4 步走，先做最小闭环 P0：只让 `extract_enabled` 生效**。P0 的 diff **完全不碰 `system-prompt/assemble` 钩子**（风险最高的那一段 0 行改动），改动只落在 `agent/turn-stopping` 内部 + 一个新增 helper，从结构上排除"重演 liangshen 事故"的可能。

**建议**：做 P0 + P1（约 80 行 diff，2 个细粒度开关 `extract_enabled` / `inject_card` / `tools_enabled` 生效），P2（召回与模型路由）视使用需求再做，P3（顺手吃掉 N18 的 21 次串行读）另开窗口。**不做** `inject_enabled` 的会话级（`context_automation.enabled` + `/memory off` 已覆盖，改它要动 assemble 核心路径，收益为负）。

**⚑ 开修前必须先确认一件事（本方案最大的不确定点）**：`harness-memory` 预设挂的**不是**这个文件，而是它自己的一份**9 月 8 日旧副本**（`/www/dsh/home/.agent-presets/harness-memory/memory-plugin/plugin-v3.js`，610 行、32391 字节、md5 `4d89e6d0c2f0eab575e1cf5512635758`、**零个 N 系列修复标记**，不在 `/www/scripts/verify_deepmemory_copies.py` 的核对组里）。详细证据见 §7.1。

---

## ② 配置点盘点（完整 grep，非抽样）

扫描方式：`apply()`（L63 起）作用域内 depth=1 的全部声明，逐一 grep 消费点；行号对应当前 md5 `66340d07…`。

### 2.1 真正的"配置来源 = 模块级变量"的点

| # | 变量 | 定义行 | 赋值行（loadConfig） | 用途 | 被谁消费（行号） | 是否建议会话级 |
|---|---|---|---|---|---|---|
| 1 | `SERVER` | L78 | L219 | memory-server 基址；`http()` L183 使用，**全链路** | L183 | ❌ **不做**（会话级改后端 = 面扩大 + 跨环境写风险） |
| 2 | `WORKSPACE` | L116 | L221 | **死变量**：只在 L241 日志里出现；真实归属由 `resolveWorkspace(sid)` L99 按 `storages/workspace.json` 解析 | L241（仅日志） | ➖ 建议直接删/标注 |
| 3 | `EXTRACT_THRESHOLD` | L117 | L223 | 抽取触发消息条数 | L710（turn-stopping） | ✅ 做（P2） |
| 4 | `RECALL_K` | L118 | L225 | 注入/召回条数 | L281（`formatMemories` 兜底）、L444（`ensureSessionRefresh`）、L462（`perCategory`）、L549（`formatMemories`）、L605（assemble completionLimit 兜底） | ✅ 做（P2） |
| 5 | `INJECT_ENABLED` | L133 | L227 | 注入总开关 | L606（assemble） | ❌ 不做（见 §1） |
| 6 | `INJECT_CARD` | L134 | L229 | 状态卡：注入 + AI 写卡 | L539（`refreshMemoryCache` 拉卡）、L781（turn-stopping 写卡） | ✅ 做（P1） |
| 7 | `EXTRACT_ENABLED` | L135 | L231 | 抽取总开关 | L706（turn-stopping 首行） | ✅ **P0 就做它** |
| 8 | `TOOLS_ENABLED` | L136 | L237 | 3 个记忆工具总开关 | L921（`memory_recall`）、L944（`memory_save`）、L973（`memory_briefing`） | ✅ 做（P1，低风险） |
| 9 | `DECAY_RATE` | L137 | L239 | `/memory clean` 触发的衰减率 | L893 | ❌ 保持全局（衰减是**全库**操作，不是会话操作） |
| 10 | `EXTRACT_PROVIDER` | L138 | L233 | 抽取模型路由 | L331（`resolveModelRoute`）← L359（`extract`）← L716（turn-stopping） | ✅ 做（P2） |
| 11 | `EXTRACT_MODEL` | L139 | L235 | 同上 | L332 | ✅ 做（P2） |

配置读取入口（全部要经手）：

- `readKey(path)` L202–206 → `GET /v1/settings/deepmemory.<path>`
- `readKeyOr(paths)` L208–214（串行 N 次）
- `loadConfig()` L216–245（**最多 21 次串行 HTTP**，N18），调用点：L987（apply 末尾，启动）、L598（assemble，>60 s 重载）
- **另一处零散配置读**：L527 `readKeyOr(['compression.inject_summary','inject_summary'])`，在 `refreshMemoryCache` 内**每次刷新都读**（建议并入 `effectiveConfig`，顺带省 2 次 HTTP）

### 2.2 名字像配置、但**不**来自配置中心的常量（盘点完整性用）

| 变量 | 定义行 | 说明 | 消费点 |
|---|---|---|---|
| `CARD_KIND` | L68 | 来自 **preset** 配置 `config.preset_mode`，不是配置中心 | L540、L783、L816 |
| `INJECT_BUDGET_CHARS` | L119 | 硬编码常量，未接配置中心 | L551、L552 |
| `INJECT_ORDER` | L120 | **死变量**（注入 order 在 L580 直接写 `order: 50`） | 无 |
| `OPERATION_INTENTS` | L122 | 常量词表 | L125 |
| `GUIDE` | L130 | 固定文本 | L585、L617 |
| `EXTRACT_SYSTEM` | L141 | 固定提示词 | L382 |
| `ENABLED_TTL_MS` | L73 | `enabledCache` 的 TTL（已按会话分键） | L260 |

### 2.3 其它进程级状态（不是配置，但决定"污染面"）

`state` L64（含 `injectCount`/`extractCount`/`lastConfigLoad`/`userCounts`/`lastUserText`/`turnCounts`）、`queryBySession` L65（**死变量**）、`userCountBySession` L66、`recentBytes` L67、`buckets` L69、`enabledCache` L70 + `enabledCacheAt` L72、`memoryCache` L74、`recentBySession` L75、`initializedSessions` L76、`refreshes` L77、`_wsCache` L98、`pendingRefresh` L437。

**结论：这些全部按 `sid` 分键，本身无跨会话污染风险**；唯一全局语义的是 `state.lastConfigLoad`（L64/L240/L598，60 s 全局重载计时器，保持不变）。

### 2.4 相邻副本（同架构、需一并处理）

| 副本 | 行数/字节 | md5 | 谁挂载 | 差异 |
|---|---|---|---|---|
| `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | 988 / 55475 | `66340d07…` | `harness-memory-task` L249、`harness-memory-daily` L96 | **本文档目标**（N01–N25 全套） |
| `/www/dsh/home/.agent-presets/harness-memory/memory-plugin/plugin-v3.js` | 610 / 32391 | `4d89e6d0…` | **`harness-memory`（记忆增强模式）L299** | 9 月 8 日版本，**0 个 N 标记**；同款模块级配置：L33 `SERVER`、L48 `WORKSPACE`（值还是拼错的 `'deepseek-hardness'`）、L49–58、L131–157 `loadConfig`、L342–348 `context_automation`、L399 turn-stopping |
| `/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js` | 988 | `66340d07…` | 测试机 | 与生产一致 ✅ |
| `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/memory-plugin/plugin-v3.js` | 988 | `66340d07…` | 仓库源码副本 | 与生产一致 ✅ |
| `/www/deepseek harness workspace/dsh-deepmemory/agent-preset/_memory-plugin/plugin-v3.js` | 847 / 47155 | `27fdd5f8…` | 仓库内第二份 | 陈旧（9 月 9 日），与实盘不一致 ⚠️ |

`/www/scripts/verify_deepmemory_copies.py` 的 `GROUPS["preset plugin-v3.js"]` 只核对 66340d07 那三处（生产/测试/仓库 `agent-preset/memory-plugin/`），**不含** `harness-memory/memory-plugin/`。

### 2.5 后端事实（实测，只读）

```
GET http://127.0.0.1:6230/v1/config/session?session_id=X
  → {"config": {<全量 defaults+overrides 合并>}, "overrides": ["key", ...]}
```
生产 6230 当前全局配置里的相关键（实测）：

| 逻辑项 | 生产实际存在的键 | 备注 |
|---|---|---|
| 抽取开关 | `extract_enabled: true` | **只有扁平键**，无 `reflection_engine.extract_enabled` |
| 注入开关 | `inject_enabled: true`、`inject_card: true` | 只有扁平键 |
| 工具开关 | `tools_enabled: true` | 只有扁平键 |
| 召回条数 | `recall_k: 5` **和** `recall_engine.top_k: 5` | 两套键并存（同值） |
| 抽取阈值 | `extract_threshold: 4`、`reflection_engine.summary_trigger_rounds: 4` | 两套 |
| 抽取模型 | `extract_model: "deepseek-v4-flash"`、`reflection_engine.extract_model` 同值 | 两套 |
| 上下文自动化 | **不存在**（缺省） | 故 L601–605 走默认 enabled=true / limit=RECALL_K |
| 摘要链 | **不存在** | 故 L527 `inject_summary` 恒 undefined → 摘要链当前不注入 |

⚠️ **由此得出一个必须遵守的实现细节**：全局目前用扁平键、会话覆盖**可能**用扁平键或 dotted 键（UI 把 `get_session_config` 的合并结果整份写回，见 §7.3，键名与 defaults 一致）。因此读取必须"**override 感知**"（见 §3.2 `pickKey`），不能只按固定顺序读第一个命中的键，否则"全局 dotted + 会话扁平"（或反之）的组合会读错。

UI 侧写入口（读侧证据）：`/www/deepmemory-v063-deploy/web-plugin/client.js` L686（读）、L700（写，`Object.keys(values)` 全量写成 override）、L712（单键 reset）。

现存会话覆盖实测：抽查 3 个活跃会话 `overrides` 均为 `[]`（该功能目前无人真正在用——与 N17"写了不生效"一致）。

---

## ③ 方案设计

### 3.1 核心不变量

1. **模块级 `let` 只允许 `loadConfig()` 写**（全局语义）；per-session 值只以函数参数/返回值流动。
2. **每个会话的配置解析结果 = 一个新建的普通对象**（`cfg`），不共享引用；缓存 `Map<sid, {at, cfg}>`，key 一定带 sid。
3. **任何失败都不改变行为**：解析失败 → 回落 `state.globalCfg`（`loadConfig()` 的成果）→ 再回落硬编码 `DEFAULTS`。三层都保证字段齐全，**永不为 undefined**。
4. **不做"每回合一次 HTTP"**：TTL 7 s + in-flight 去重（同一会话并发 assemble 只发 1 次）。
5. 现有 `/v1/settings/session_enabled:<sid>` 会话总开关（`isEnabled` L256–268，TTL 5 s）保持不变，与本方案正交。

### 3.2 新增代码（建议插在 L245 `loadConfig()` 之后，约 70 行）

```js
// ── N17：会话级有效配置（per-session，绝不写回模块级 let）──────────────
const CFG_TTL_MS = 7000
const cfgCache = new Map()      // sid -> { at, cfg }
const cfgInflight = new Map()   // sid -> Promise<cfg>

// 硬默认：任何一层都拿不到时的最终兜底（与 L133-139 初值一致）
const CFG_DEFAULTS = {
  INJECT_ENABLED: true, INJECT_CARD: true, EXTRACT_ENABLED: true, TOOLS_ENABLED: true,
  EXTRACT_THRESHOLD: 4, RECALL_K: 5, DECAY_RATE: 0.01,
  EXTRACT_PROVIDER: '', EXTRACT_MODEL: '', INJECT_SUMMARY: true,
  CONTEXT_AUTOMATION_ENABLED: true, MEMORY_COMPLETION_K: 5,
}

// override 感知的取键：会话覆盖优先于拼写差异，其次按既定优先级（dotted → 扁平）
function pickKey(map, overrideSet, keys) {
  for (const k of keys) {
    if (overrideSet.has(k) && map[k] !== undefined && map[k] !== null) return map[k]
  }
  for (const k of keys) {
    if (map[k] !== undefined && map[k] !== null) return map[k]
  }
  return undefined
}

function normalizeCfg(map, overrideSet, fallback) {
  const src = map || {}
  const ov = overrideSet || new Set()
  const g = fallback || CFG_DEFAULTS
  const num = (keys, d, lo, hi) => {
    const n = Number(pickKey(src, ov, keys))
    let v = Number.isFinite(n) ? n : d
    if (lo !== undefined) v = Math.max(lo, v)
    if (hi !== undefined) v = Math.min(hi, v)
    return v
  }
  const bool = (keys, d) => {
    const v = pickKey(src, ov, keys)
    if (v === undefined) return d
    return !(typeof v === 'string' && ['false', '0', 'no', 'off', ''].includes(v.trim().toLowerCase()))
      && Boolean(v)
  }
  const str = (keys, d) => {
    const v = pickKey(src, ov, keys)
    return (v === undefined || v === null || !String(v).trim()) ? d : String(v).trim()
  }
  return {
    INJECT_ENABLED: bool(['injection.inject_enabled', 'inject_enabled'], g.INJECT_ENABLED),
    INJECT_CARD: bool(['injection.inject_card', 'inject_card'], g.INJECT_CARD),
    EXTRACT_ENABLED: bool(['reflection_engine.extract_enabled', 'extract_enabled'], g.EXTRACT_ENABLED),
    TOOLS_ENABLED: bool(['agent_tools.tools_enabled', 'tools_enabled'], g.TOOLS_ENABLED),
    EXTRACT_THRESHOLD: num(['reflection_engine.summary_trigger_messages',
      'reflection_engine.summary_trigger_rounds', 'extract_threshold'], g.EXTRACT_THRESHOLD, 1, 100),
    RECALL_K: num(['recall_engine.top_k', 'recall_k'], g.RECALL_K, 1, 20),
    EXTRACT_PROVIDER: str(['reflection_engine.extract_provider', 'extract_provider'], g.EXTRACT_PROVIDER),
    EXTRACT_MODEL: str(['reflection_engine.extract_model', 'extract_model'], g.EXTRACT_MODEL),
    DECAY_RATE: num(['importance_decay.decay_rate', 'decay_rate'], g.DECAY_RATE, 0, 1),
    INJECT_SUMMARY: bool(['compression.inject_summary', 'inject_summary'], g.INJECT_SUMMARY),
    // 把 assemble 现在单独 GET 的两个键并进来，避免第二次 HTTP
    CONTEXT_AUTOMATION_ENABLED: bool(['context_automation.enabled'], true),
    MEMORY_COMPLETION_K: num(['context_automation.memory_completion_k'], g.RECALL_K, 1, 20),
  }
}

function globalCfg() { return state.globalCfg || CFG_DEFAULTS }

async function effectiveConfig(sessionId) {
  const sid = String(sessionId || '')
  const hit = cfgCache.get(sid)
  if (hit && Date.now() - hit.at < CFG_TTL_MS) return hit.cfg
  if (cfgInflight.has(sid)) return cfgInflight.get(sid)     // singleflight
  const p = (async () => {
    let cfg = null
    try {
      if (sid) {
        const res = await http('GET', '/v1/config/session?session_id=' + encodeURIComponent(sid), undefined, 4000)
        if (res.ok && res.data && res.data.config) {
          const ov = new Set(Array.isArray(res.data.overrides) ? res.data.overrides.map(String) : [])
          cfg = normalizeCfg(res.data.config, ov, globalCfg())
        }
      }
    } catch (e) {
      console.log('[deepmemory] effectiveConfig fallback to global: ' + String(e))
    }
    if (!cfg) cfg = globalCfg()                            // 失败/无 sid → 全局快照
    cfgCache.set(sid, { at: Date.now(), cfg })
    return cfg
  })().finally(() => cfgInflight.delete(sid))
  cfgInflight.set(sid, p)
  return p
}
```

`loadConfig()` 末尾（L240 附近）追加一行"公布全局快照"，同时保留原有 10 个 `let`（它们就是快照来源，也是 N25/红线 8 类事故的回退面）：

```js
state.globalCfg = normalizeCfg({}, new Set(), {   // 由现有模块级 let 组装
  INJECT_ENABLED, INJECT_CARD, EXTRACT_ENABLED, TOOLS_ENABLED,
  EXTRACT_THRESHOLD, RECALL_K, DECAY_RATE, EXTRACT_PROVIDER, EXTRACT_MODEL,
  INJECT_SUMMARY: true,
  CONTEXT_AUTOMATION_ENABLED: true,
  MEMORY_COMPLETION_K: RECALL_K,
})
```

`http()` 增加可选超时（L181）：

```js
async function http(method, path, body, timeoutMs = 25000) {   // 配置读取用 4000
  ...  signal: AbortSignal.timeout(Math.max(500, Number(timeoutMs) || 25000)),
```

### 3.3 函数签名改法（逐点，行号=当前文件）

| # | 函数 / 钩子 | 现签名（行号） | 改后签名 | 内部读值点 | 调用方改动 |
|---|---|---|---|---|---|
| 1 | `http` | `http(method, path, body)` L181 | `http(method, path, body, timeoutMs = 25000)` | L190 | 仅配置读取处传 4000 |
| 2 | `loadConfig` | `loadConfig()` L216 | 不变 | 末尾追加 `state.globalCfg = …` | 不变 |
| 3 | **新增** `normalizeCfg` / `pickKey` / `globalCfg` / `effectiveConfig` | — | 见 §3.2 | — | — |
| 4 | `formatMemories` | `(results, limit)` L277 | `(results, cfg, limit)` | L281 → `cfg.RECALL_K` | L549 |
| 5 | `resolveModelRoute` | `(llm, preferredProvider, preferredModel)` L330 | `(llm, cfg)` | L331/332 | L359 |
| 6 | `extract` | `(dialog, signal)` L356 | `(dialog, signal, cfg)` | L359 | L716 |
| 7 | `ensureSessionRefresh` | `(sessionId, completionLimit)` L438 | `(sessionId, cfgOrPromise, completionLimit)`；函数体首行 `const cfg = await Promise.resolve(cfgOrPromise).catch(() => globalCfg())` | L444 → `cfg.RECALL_K` | L612（assemble，已 await）、L701（session/event，**不 await**） |
| 8 | `refreshMemoryCache` | `(sessionId, query, limit)` L452 | `(sessionId, query, cfg, limit)` | L462 `cfg.RECALL_K`、L527 → `cfg.INJECT_SUMMARY`（删掉 readKeyOr）、L539 `cfg.INJECT_CARD === true`、L549 `cfg.RECALL_K` | L445（ensureSessionRefresh 内） |
| 9 | `system-prompt/assemble` 钩子 L592–623 | — | L598–605 三行替换为 `const cfg = await effectiveConfig(sessionId)`；`automationEnabled` → `cfg.CONTEXT_AUTOMATION_ENABLED`；`completionLimit` → `cfg.MEMORY_COMPLETION_K` | L606 `cfg.INJECT_ENABLED !== true`、L612 | `await next()`（L593）与 L618–622 返回值结构**一字不动** |
| 10 | `session/event` 钩子 L625–703 | — | L701 → `ensureSessionRefresh(sid, effectiveConfig(sid))`（**不 await**，保持现有 fire-and-forget；该钩子若阻塞会拖慢事件处理） | L661 `enabledCache` 快路径不变 | — |
| 11 | `agent/turn-stopping` 钩子 L705–869 | — | 函数体首行 `const cfg = await effectiveConfig(sid)`（须在取到 sid 之后、判空之前，包 try/catch） | L706 `cfg.EXTRACT_ENABLED !== true`、L710 `cfg.EXTRACT_THRESHOLD`、L716 `extract(..., cfg)`、L781 `cfg.INJECT_CARD === true` | — |
| 12 | 3 个工具 `execute` | L920 / L943 / L972 | 各在体内 `const cfg = await effectiveConfig(sid)` | L921 / L944 / L973 → `cfg.TOOLS_ENABLED !== true` | — |
| 13 | `/memory clean` | L892–895 | **不改**（保持全局 `DECAY_RATE`，L893） | — | — |

调用链（改后）：

```
system-prompt/assemble ── effectiveConfig(sid) ─┬─ ensureSessionRefresh(sid, cfg, completionLimit)
                                                │        └─ refreshMemoryCache(sid, q, cfg, limit) ─ normalizeCfg 结果参与渲染
                                                └─ cfg.INJECT_ENABLED / cfg.CONTEXT_AUTOMATION_ENABLED
session/event ── ensureSessionRefresh(sid, effectiveConfig(sid))     [不 await]
agent/turn-stopping ── effectiveConfig(sid) ─┬─ cfg.EXTRACT_ENABLED / cfg.EXTRACT_THRESHOLD
                                             ├─ extract(dialog, signal, cfg) ─ resolveModelRoute(llm, cfg)
                                             └─ cfg.INJECT_CARD（写卡）
tools.execute ×3 ── effectiveConfig(sid) ── cfg.TOOLS_ENABLED
```

### 3.4 每个开关的语义归属（避免"看起来修好了其实没修"）

当前消费点里 `formatMemories` 的 `limit` 在 assemble 路径上恒有值（L549 传入 `Math.max(limit || RECALL_K, results.length)`），所以 `RECALL_K` 的会话覆盖只有经 `MEMORY_COMPLETION_K`/`ensureSessionRefresh` 兜底才生效——P2 必须同时改 L444+L605 这两条兜底，否则"会话改召回条数"仍无效。

`EXTRACT_ENABLED=false` 时 `buckets` 仍会累积消息（session/event 不查配置，且 L691 上限 40 条）。建议在 turn-stopping 的早退分支顺手 `buckets.delete(sid)`（1 行），避免"关着抽取还一直攒桶、开着以后一次性抽一堆旧消息"。

`INJECT_CARD` 影响 `memoryCache[sid]` 的**渲染结果**（L539 拉卡、L549 拼文本）。若会话中途改 `inject_card`，缓存文本不会自动重建（现有失效条件只有 L607 与 userCount 变化）。建议加"渲染签名"：`cacheSig.set(sid, cfg.INJECT_CARD + '|' + cfg.RECALL_K + '|' + cfg.INJECT_SUMMARY)`，不一致时 `memoryCache.delete(sid); initializedSessions.delete(sid)`（约 6 行，P1 一起做）。

---

## ④ 改动面与风险

### 4.1 改动面

| 区域 | 行号 | 风险 | 失败后果 | 必需防御 |
|---|---|---|---|---|
| **新增 helper + 缓存** | 插在 L245 后（~70 行） | 低 | 语法/加载失败 → **整个 preset 挂不上，所有会话记忆全灭** | 四步 preflight 第 1/2 步必过；新增代码全部在 `apply()` 内（不引入顶层副作用） |
| **`system-prompt/assemble` 钩子** | L598–612 | **高**（红线 8：liangshen 事故就在这条链路，改坏 = 所有会话每回合 error） | 全站不可用 | P0 **完全不碰**此钩子；P3 若碰：`await next()` 仍为第一句、返回值结构不变、全程 try/catch（L597–616 已有） |
| **`agent/turn-stopping` 钩子** | L706–716、L781 | **高**（N10 教训：此处抛错会把**已生成完回答**的回合标成 error） | 用户看到"回答完就报错" | `effectiveConfig` 永不抛；新代码在该 async 体内，任何异常被既有结构吞掉/降级；`cfg` 读取用 `!== true` 语义 |
| **3 个工具 execute** | L921/944/973 | 中低 | 工具报错（单次调用失败，非全站） | `effectiveConfig` 内部消化异常；`cfg.TOOLS_ENABLED !== true` |
| **`refreshMemoryCache`** | L452–572 | 中（注入文本生成，回合内冻结语义） | 注入为空/文本异常 | 参数化 `cfg`；删除 L527 的 readKeyOr（失败即视为 false，与原行为一致）；不改 L551–557 的裁剪与写缓存逻辑 |
| **`session/event`** | L701 | 中 | 事件处理被拖慢（不得 await 网络） | 传 promise 不 await；保持 L702 的 `try{}catch{}` |
| **`loadConfig`** | L240 附近 +1 行 | 低 | 快照缺失 → 回落 `CFG_DEFAULTS` | `globalCfg()` 三层兜底；保留原有 10 个 `let` 与 60 s 重载 |
| **`http` 加超时参数** | L181/L190 | 低 | — | 默认值 25000 与原行为完全一致（向后兼容） |
| **日志补 sid** | L715 / L718 / L771 | 极低 | — | 仅改字符串拼接 |
| **不做**：`inject_enabled` / `server_url` / `DECAY_RATE` | — | — | — | 明确写入文档，防后续"顺手也改一下" |

三处副本必须同步（否则 `verify_deepmemory_copies.py` 失败）：
`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`、
`/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js`、
`/www/deepseek harness workspace/dsh-deepmemory/agent-preset/memory-plugin/plugin-v3.js`。

### 4.2 防御清单（必须全部满足才允许上测试机）

- **D1 永不抛**：`effectiveConfig` 的 try/catch 覆盖 HTTP、JSON、normalize 全部；返回值一定是对象。
- **D2 字段永不为 undefined**：`normalizeCfg` 的 `bool/num/str` 都以 `CFG_DEFAULTS` 兜底；消费点统一写 `!== true` / `=== true`（字段缺失时退化成"当前全局行为"，而不是静默关掉功能）。**严禁**把 `if (!EXTRACT_ENABLED)` 直译成 `if (!cfg.EXTRACT_ENABLED)`——一旦 `cfg` 异常就是"抽取静默全停"。
- **D3 配置 HTTP 硬超时 4 s**（`http(..., 4000)`），绝不用默认 25 s。
- **D4 singleflight**：`cfgInflight` 保证同会话并发只发一次；TTL 7 s 保证每回合最多 1 次（当前 assemble 是**每回合都发**，改后是净减少）。
- **D5 零写回**：per-session 路径不出现 `INJECT_ENABLED = …` 这类赋值；grep 审核 `grep -nE '^\s*(INJECT_|EXTRACT_|TOOLS_|RECALL_K|DECAY_RATE|SERVER|WORKSPACE)[A-Z_]*\s*=' plugin-v3.js` 应只命中 L219/221/223/225/227/229/231/233/235/237/239。
- **D6 assemble 钩子不变量**：`const assembled = await next()` 仍是函数体第一句；`return { ...assembled, sections: assembled.sections.map(...) }`（L618–622）结构不变；`next()` 只调用一次。
- **D7 灰度顺序**：P0（只 `extract_enabled`）→ 测试机观察 ≥1 天 → P1 → P2。
- **D8 备份与回滚**：改前三处 `cp x x.bak-n17-$(date +%Y%m%d-%H%M%S)`；回滚 = 覆盖 `.bak-*` + 重启；回滚不需要动数据库。
- **D9 升级免疫**：本方案不触碰任何 DSH session API（红线 8 的雷区），只用插件自己的 `ctx.on` 与内存 Map。

---

## ⑤ 验证方案

### 5.0 阶段铁律

**测试机 3091 验证通过 → 才允许碰生产 3081/6230**（`dsh-deepmemory/AGENTS.md` 部署两阶段铁律）。生产重启前必须确认**没有 running 会话**（AGENTS.md 红线 2）。

### 5.1 四步 preflight（测试机 3091）

```bash
# 0) 备份（改前；生产/测试/仓库三处都要）
cp "/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js" \
   "/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js.bak-n17-$(date +%Y%m%d-%H%M%S)"

# 1) JS 语法
cp "/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js" /tmp/chk-n17.mjs \
  && /usr/local/node/bin/node --check /tmp/chk-n17.mjs

# 2) ESM 加载冒烟（防 CJS 宽松漏检；注意 apply() 不会被调用，故不会触发 loadConfig/网络）
/usr/local/node/bin/node --input-type=module \
  -e "await import('file:///www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js')"

# 3) 重启测试机 + 挂载断言（ok:true 且 groups 数不减）
systemctl restart dsh-test.service && sleep 8
#    ⚠️ 直连 127.0.0.1:3091 会被判 unauthorized（本机实测）——必须带 nginx 代理注入的 cookie。
#    取 cookie 用命令替换，**不要把值打印到终端/日志**：
COOKIE=$(grep -o 'dsh-auth-[A-Za-z0-9._-]*=[A-Za-z0-9._-]*' /etc/nginx/dsh-auth-cookie-test.conf | head -1)
curl -s -X POST http://127.0.0.1:3091/api/session.models \
  -H 'Content-Type: application/json' -H "Cookie: $COOKIE" \
  -d '{"type":"client-request","rpcId":"p","method":"session.models","payload":{"sessionId":"<测试活跃会话>"}}'
#    （交付文档另用 payload 形式 {"args":{"sessionId":"…"}}，两者都可尝试；断言 ok:true）

# 4) 真实消息冒烟 + journal（**必须真发一条消息**，光看启动日志不算，红线 8 教训）
journalctl -u dsh-test.service --since '3 min ago' -o cat | grep -E '\[deepmemory\]|rror' | tail -30
#    期望：出现 "[deepmemory] ready …" 与 "[deepmemory] config: inject=… extract=…"
#          且**没有** "effectiveConfig fallback to global"（除非故意制造后端故障）

# 5) 副本一致性（三处 md5 一致，退出码 0）
/opt/AstrBot/venv/bin/python3 /www/scripts/verify_deepmemory_copies.py
```

### 5.2 对照实验：A 会话关抽取、B 会话开启，互不影响

**第 0 步（必做前置·隔离）**——先证明 3091 的 preset 打的是测试库 6240：

```bash
PID=$(systemctl show -p MainPID --value dsh-test.service); echo "dsh-test pid=$PID"
# 在 3091 打开/发一条消息（产生一次注入）后立刻看连接：
ss -tnp | grep "pid=$PID" | grep -E ':(6230|6240)'
T=$(cat /www/dsh-test-home/.dsh-memory-api-token)
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:6240/v1/memories/injection-log   # 期望 injection 非 null
```

⚠️ **实测风险**：测试库 6240 的 settings 里 `deepmemory.server_url = "http://127.0.0.1:6230"`（生产地址！），而 `loadConfig()` L218–219 会用这个值覆盖 `SERVER`（初始值是 `MEMORY_SERVER_PORT=6240`）。也就是说 3091 的 preset **很可能在首次 loadConfig 之后改打生产 6230**。若第 0 步确认打的是 6230：**立即停止实验**，先把测试库的 `deepmemory.server_url` 改为 `http://127.0.0.1:6240`（只写测试库），否则 A/B 实验会把会话覆盖写进**生产库**、并污染生产记忆。

**第 1 步**：在 3091 新建两个会话 A、B（同一 preset「任务工作模式」→ 挂 `_memory-plugin`）。要求 sid 前 12 字符不同（日志只打 `slice(0,12)`，形如 `session-abcd`）。

**第 2 步**：只给 A 写覆盖（B 不写）：

```bash
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:6240/v1/config/session/set \
  -d '{"session_id":"<A>","key":"reflection_engine.extract_enabled","value":false}'
# 断言 A：config 里该键 = false，overrides 含该键
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:6240/v1/config/session?session_id=<A>"
# 断言 B：仍为 true，overrides = []
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:6240/v1/config/session?session_id=<B>"
```

（同时建议对 A 写 `inject_card=false` 以便 P1 验证注入侧差异；B 保持默认 true。）

**第 3 步（复现旧缺陷，可选但强烈建议）**：在**未改**的旧版上先跑一遍 A/B——预期 A 依然抽取（证明 N17 缺陷真实存在），留作对照基线。

**第 4 步**：交替驱动 **A → B → A → B**，每个会话各 3 轮（每轮 1 条用户消息 + 等回答）。`EXTRACT_THRESHOLD=4`，两条消息即入桶 4 条（user+assistant 各 1 条/轮，2 轮越阈），3 轮足够。

**第 5 步**：读 journal 断言（测试机）：

```bash
journalctl -u dsh-test.service --since '15 min ago' -o cat | grep -E '\[deepmemory\]'
```

期望：

| 观察项 | A（extract=false） | B（默认 true） |
|---|---|---|
| `session memory cache updated sid=…AAAA` | ✅ 有（注入不受抽取开关影响） | ✅ 有 |
| `extracting from N messages…` | ❌ 无 | ✅ 有 |
| `extracted X/Y memories (total …)` | ❌ 无 | ✅ 有 |
| `AI updated state card`（若 A 另关 inject_card） | ❌ 无 | ✅ 有 |

**互不影响的判据**：交替顺序下，"A 关闭期间 B 正常抽取" **且** "B 抽取之后 A 仍不抽取"。若是把会话值写回模块级变量的错误实现，必然出现"两者都不抽"（最后写入者=A 的 false 传播到进程）或"两者都抽"（A 被 B 的 true 覆盖）——**A→B→A→B 的四次交替就是判别器**。

**第 6 步（可选、推荐）**：反向对调（A 开、B 关）重复一遍，确认行为"随会话"而不是"随最后写入者"。

**第 7 步（防御性验证）**：故意让配置读取失败——`systemctl stop dsh-test-memory`（**仅测试机**，且必须先过第 0 步隔离）→ 发一条消息 → 期望：注入/抽取仍按**全局**值工作、journal 出现 `[deepmemory] effectiveConfig fallback to global: …`、**turn 不报错**（关键：不能出现 `turn/end reason=error`）→ `systemctl start dsh-test-memory`。

**第 8 步**：数据库侧只读取证（可选）：

```bash
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:6240/v1/memories/list?session_id=<A>&limit=5"
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:6240/v1/memories/list?session_id=<B>&limit=5"
# 期望：A 实验期间新增 = 0，B 新增 > 0
```

### 5.3 ⚠️ 实验可观测性的硬缺口（必须随 P0 一起补，共 3 行）

现有日志**无法把抽取行为归属到会话**：

- L715 `[deepmemory] extracting from N messages...` —— 不含 sid
- L718 `[deepmemory] extract failed; keep bucket for retry (N messages)` —— 不含 sid
- L771 `[deepmemory] extracted X/Y memories (total Z)` —— 不含 sid

A/B 实验的两个会话会交替打日志，仅凭这三行无法区分谁触发的。**建议 P0 一并加 `(sid ' + sid.slice(0,12) + ')`**，这样 §5.2 第 5 步可以用 `grep -E "sid=(session-AAAA|session-BBBB)"` 自动断言。这是让验证"可证据化"的最小改动。

### 5.4 生产（第 3 阶段）验收

1. 同步三处副本 + `verify_deepmemory_copies.py` 退出码 0；
2. 确认无 running 会话 → `systemctl restart dsh-web.service`；
3. `session.models` 断言（带 cookie）+ 真实消息冒烟 + `journalctl -u dsh-web` 检查 `[deepmemory]` 无 error；
4. 在生产任一会话用 UI 写一次 `extract_enabled=false` 覆盖，观察该会话不再抽取、另一个打开着的会话照常抽取（生产只做只读观察，不长期留覆盖）；
5. 观察窗口 ≥30 min，`turn/end reason=error` 计数为 0。

---

## ⑥ 工作量与拆分建议

### 6.1 规模

| 项 | 行数 |
|---|---|
| 新增 `pickKey`/`normalizeCfg`/`globalCfg`/`effectiveConfig`/缓存/`CFG_DEFAULTS` | ~70 |
| `loadConfig` 公布快照 | +12 |
| `http` 超时参数 | +2 |
| 15 个消费点改签名/读值 | ~35（改动） |
| 渲染签名失效（P1） | +6 |
| 日志补 sid | 3 改 |
| **合计** | **净增 ~80 行，改动/新增点 ~25 处**（988 → ~1070 行） |

时间：编码 1–2 h；测试机验证（含 A/B 对照 + 故障回落）1–2 h；生产窗口 ~20 min（含重启）。**不涉及后端改动、不涉及数据库迁移。**

### 6.2 是否值得做

**值得，但只值得做 P0+P1**：

- 收益：修掉"UI 上给单个会话关掉抽取/状态卡，实际不生效"的欺骗性缺陷；顺带把每回合 1 次 `context_automation` GET 变成 7 s TTL（**净减少** HTTP）；顺带可吃 N18（P3）。
- 成本：核心链路虽然敏感，但本方案把 per-session 值完全参数化、不写模块级变量、三层兜底，"改坏所有会话"的经典失败模式被结构性挡住；P0 更把最高风险的 assemble 钩子变成 **0 行改动**。
- 不做的理由（若窗口紧张）：该功能目前**零人使用**（实测 3 个活跃会话 `overrides` 全空），属于"修一个好功能"而非"救一个线上故障"。

### 6.3 建议拆分（每步独立可验证、可回滚）

| 阶段 | 内容 | 触及 | 风险 | 行数 |
|---|---|---|---|---|
| **P0（先做）** | `effectiveConfig` + 缓存 + `http` 超时参数 + 日志补 sid + **仅** `extract_enabled` 生效（L706） | turn-stopping 一处读值 + 新增 helper | 中低（assemble 0 改动） | ~45 |
| **P1** | `inject_card`（L539/781）+ `tools_enabled`（L921/944/973）+ 渲染签名失效 | refreshMemoryCache / turn-stopping / 3 tools | 中低 | ~35 |
| **P2** | `recall_k`（L444/462/549/605）+ `extract_threshold`（L710）+ `extract_model`/`extract_provider`（L331/332） | 召回与模型路由 | 中（模型路由错 → 抽取失败，但不影响注入） | ~20 |
| **P3（可选）** | 合并 assemble 里重复的 `/v1/config/session` GET（每回合省 1 次 HTTP）；用一次 `GET /v1/config` 取全量全局值替代 `loadConfig` 的 21 次串行读（**N18**） | assemble + loadConfig | 中（动 assemble，需单独窗口） | ~30 |
| **不做** | `inject_enabled` 会话级、`server_url`、`DECAY_RATE` | — | — | 0 |

**P0 的关键卖点**：`system-prompt/assemble`（L592–623）**一行不改** → 即使 P0 全错，最坏后果也只是"某会话抽取行为不对"，不会出现"所有会话每回合报错"。这正是红线 8 要求的爆炸半径控制。

---

## ⑦ 不确定点 / 开修前必须先确认

1. **【高】到底哪个副本在跑。** `harness-memory`（preset 名「记忆增强模式」）在 `agent.cordis.yml:299` 挂的是 `./memory-plugin/plugin-v3.js`，即 `/www/dsh/home/.agent-presets/harness-memory/memory-plugin/plugin-v3.js`：610 行、9 月 8 日、**0 个 N 标记**（N01–N25 一个都没有）、`WORKSPACE` 默认值还是拼错的 `'deepseek-hardness'`。它不在 `verify_deepmemory_copies.py` 的核对组里。
   → 必须先确认本次 N17 要修的 preset 是哪一个（或两者都改）；若主 preset 用旧副本，则"只改 `_memory-plugin`"等于**主路径依旧没修**，而且旧副本连 N10/N09 等既有修复都缺，是否仍在用需要拍板。
   建议的确认方式（只读）：看目标会话在 journal 中的 `[deepmemory] session memory cache updated sid=…` 与 preset 归属，或直接在 3091 建两个会话分别用两种 preset 观察日志差异。
2. **【高】测试机跨环境写风险。** 6240 库的 `deepmemory.server_url = http://127.0.0.1:6230`，`loadConfig()` 会把它当作 `SERVER`。→ A/B 实验前必须按 §5.2 第 0 步确认连接目标；否则"在测试机写会话覆盖"实际写进了**生产库 6230**。
3. **【中】UI 保存语义（N16）会让修复"看起来变坏"。** `client.js:700` 一次把 `values` 里**所有**键都 POST 成 session override。N17 修好之后，"在某个会话里点一次保存"会把当前全部默认值锁死在该会话（此后全局改配置对该会话不再生效），单键 reset 要一个个点。→ 建议同窗口一起收窄 UI：只写与 defaults 不同的键（`client.js:700` 过滤），或保存后立刻分类回写。否则用户会认为"N17 修出了新 bug"。
4. **【中】键名优先级。** 全局目前是扁平键 + 少数 dotted 键并存（见 §2.5）。必须使用 override 感知的 `pickKey`（§3.2），否则会出现"全局 dotted 有效、会话扁平无效"之类的静默错读。UI 保存后 override 里会同时出现两套键名（写入的是合并结果）。
5. **【低】生效延迟语义。** 会话配置改动最长 7 s（TTL）+ 下一次 assemble 才生效；`/memory off` 仍是 5 s（`ENABLED_TTL_MS`）。若要"改完立刻生效"，可把 `effectiveConfig` 的 TTL 降到 3 s（代价是每回合最多 1 次额外 HTTP，2 ms，可接受）。
6. **【低】`state.globalCfg` 的时序。** `loadConfig()` 在 L987 异步启动，首个 assemble 可能早于它完成 → 必须有 `CFG_DEFAULTS` 兜底（方案已含）；`loadConfig` 失败时 L243 只打日志、保留旧值，快照也保留旧值，语义与现状一致。
7. **【低】文档行号时效性。** 本文所有行号对应 md5 `66340d0756b1899ca9e9f2e2f90eca4c`（988 行）。任何先于 N17 落地的补丁都会使行号漂移，施工前请以 `grep -n` 复核，不要按行号盲改。
8. **【低】N18 与 P3 的取舍。** 把 `loadConfig()` 的 21 次串行读换成 1 次 `GET /v1/config` 收益明显（最坏 525 s → 一次请求），但 `get_config_values()` 会连 `session.<sid>.<key>` 形式的其它会话覆盖键一起返回（非 secrets 时），需要在客户端侧忽略 `session.` 前缀，属额外边界；建议独立窗口做。

---

## 附：检索命令（复核用）

```bash
# 模块级（apply 作用域）声明全量
python3 - <<'EOF'
import re
L=open('/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js',encoding='utf-8').read().split('\n')
s=next(i for i,l in enumerate(L) if l.startswith('export function apply'))
d=0
for i in range(s,len(L)):
    if d==1 and re.match(r'^  (let|const|var|function|async function|class)\s',L[i]): print(i+1,L[i].strip()[:90])
    d+=L[i].count('{')-L[i].count('}')
EOF
# 写回模块级变量的点（应只有 loadConfig 内 10 处）
grep -nE '^\s*(INJECT_|EXTRACT_|TOOLS_|RECALL_K|DECAY_RATE|SERVER|WORKSPACE)[A-Z_]*\s*=' \
  /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js
# 配置读取全量
grep -n 'readKey\|/v1/settings\|/v1/config\|loadConfig' \
  /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js
```
