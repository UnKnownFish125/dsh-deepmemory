// harness-memory — long-term memory plugin for the DeepSeek Harness.
//
// Preset-plane plugin (mounted as a relative-path row from agent.cordis.yml).
// Talks to the local dsh-memory-server (systemd, http://localhost:6230)
// which owns SQLite + FAISS + BM25 + graph storage.
//
// Capabilities (P2):
//  - system-prompt/assemble: one-time session cache hydration; later calls read only
//  - agent/turn-stopping: refresh session context after durable memory changes
//  - agent/turn-stopping: cheap-LLM extraction — rich fields, atoms, entities,
//    relations, source retention, card update
//  - tools: memory_recall / memory_save / memory_briefing (persona-aware)
//  - /memory command: on|off|status|clean (per-conversation, persisted)
//  - config centre reload (deepmemory.* keys) every minute
//  - daily importance decay with access reinforcement (server-side)

import fs from 'node:fs'
import { defineTool } from '/usr/local/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js'

export const name = 'deepmemory'

export const inject = ['tools']

export function apply(ctx, config = {}) {
  const state = { injectCount: 0, extractCount: 0, lastConfigLoad: 0, lastInjectionDetail: [] }
  const queryBySession = new Map()
const userCountBySession = new Map()
  const recentBytes = new Map()
  const CARD_KIND = config.preset_mode === 'daily' ? 'daily' : 'task'
  const buckets = new Map()
  const enabledCache = new Map()
  const memoryCache = new Map()
  const recentBySession = new Map()
  const initializedSessions = new Set()
  const refreshes = new Map()
  let SERVER = 'http://localhost:' + String(process.env.MEMORY_SERVER_PORT || '6230')
  const TOKEN_FILES = [
    process.env.MEMORY_API_TOKEN_FILE,
    process.env.DSH_HOME ? `${process.env.DSH_HOME}/.dsh-memory-api-token` : '',
    process.env.HOME ? `${process.env.HOME}/.dsh-memory-api-token` : '',
  ].filter((path, index, paths) => path && paths.indexOf(path) === index)
  function readToken() {
    for (const path of TOKEN_FILES) {
      try {
        const token = fs.readFileSync(path, 'utf8').trim()
        if (token) return token
      } catch {}
    }
    return ''
  }
  let WORKSPACE = 'deepseek-harness'
  let EXTRACT_THRESHOLD = 4
  let RECALL_K = 5
  const INJECT_ORDER = 50
  // 使用指引：固定文本（不随会话/回合变化 → 前缀缓存安全），引导"优先已注入 + 合并调用 + 提前规划"
  const GUIDE = '[记忆使用指引] 以上为当前会话相关记忆（完整、按重要度排序）。' +
    '优先直接基于注入内容作答，不要重复调用检索；仅当存在明确信息缺口时才调用 memory_recall，' +
    '且一次检索到位（k 最大 10，避免多次小调用）；发现值得长期保留的新信息时用 memory_save 一次写入。'
  let INJECT_ENABLED = true
  let INJECT_CARD = true
  let EXTRACT_ENABLED = true
  let TOOLS_ENABLED = true
  let DECAY_RATE = 0.01
  let EXTRACT_PROVIDER = ''
  let EXTRACT_MODEL = ''

  const EXTRACT_SYSTEM = [
    '你是长期记忆抽取器。从对话片段中提取值得长期记住的内容，并做结构化解构。',
    '规则：',
    '1. memories 只提取：事实(fact)、偏好(preference)、决定(decision)、计划(plan)、事件约定(episode)。忽略闲聊和过程细节。',
    '2. 每条记忆 content 用简洁完整的一句话；key_facts 提取其中的关键实体与主题短语（分号分隔，≤5 个，用于检索）；persona_summary 为面向模型注入的一句话表述（无特殊表述时留空）。',
    '3. domain：项目/技术/工作任务=work，个人生活/习惯/人际=life。scope：仅当前对话=session，当前项目/工作区=workspace，用户个人长期适用=global。**凡是用户/助手在本轮对话中说出或确认的内容（约定、指示、决策、偏好、任务背景）一律 scope=session**，只有明确跨对话/项目级才 workspace，用户长期偏好=global。importance：0-1，偏好与重要约定 0.7+。',
    '4. atoms：把每条记忆拆成独立事实单元（可 0-3 条），每单元含 atom_type（factual 事实/preference 偏好/decision 决定/episodic 事件/planned 计划/relational 关系）、content（独立自包含一句话）、ttl_days（factual=180, preference=60, decision=30, episodic=7, planned=2, relational=90）、decay_type（exponential/linear/step）、importance。',
    '5. entities：抽取记忆中的实体名词列表（人名/项目/工具/概念），每项 {name, kind: person|project|tool|concept|other}。',
    '6. relations：实体之间的关系边列表（可 0-3 条），每项 {source, relation, target}，source/target 必须是 entities 里出现过的实体名，relation 用短动词短语（如 "使用"、"依赖"、"属于"、"负责"）。',
    '6b. credential-redaction：若对话内容包含密钥/令牌/口令/私钥，不要输出其字面值；能记忆就只记录引用名/env var，值为 REDACTED。',
    // 6c 已撤：topic_id 从未产出真主题（273 条仅 27 条且全为 "0"），不再让抽取模型打标签（字段保留向后兼容）

    '7. card：增量更新当前会话状态卡（goal/current_plan 各一句话；key_decisions 追加新决定≤3条；in_progress/next_steps 各≤4条；无需变化时 card 为 null）。',
    '8. tasks：仅当对话中出现明确任务/子任务时才输出数组（title + status∈planned|todo|in_progress|completed|failed + 可选 parent/blocked/reason）；无明确任务时 tasks 为 []。',
    '8. 严格只输出一个 JSON 对象（不要 markdown 代码块）：',
    '{"memories":[{"content":"...","key_facts":"词1;词2","persona_summary":"...或空","type":"fact","domain":"work","scope":"workspace","importance":0.7,"topic":"任务看板开发","atoms":[{"atom_type":"factual","content":"...","ttl_days":180,"decay_type":"exponential","importance":0.6}],"entities":[{"name":"...","kind":"project"}],"relations":[{"source":"...","relation":"...","target":"..."}]}],"card":{"goal":"...","current_plan":"...","key_decisions":["..."],"in_progress":["..."],"next_steps":["..."]},"tasks":[{"title":"...","status":"todo"}]}',
    '没有值得记忆的内容时 memories 为 []。',
  ].join('\n')


function redactSensitive(text) {
  if (typeof text !== 'string' || !text) return text || ''
  let out = text
  const replacers = [
    [/gh[pousr]_[A-Za-z0-9_]{20,}/g, '[REDACTED:git-token]'],
    [/github_pat_[A-Za-z0-9_]{20,}/g, '[REDACTED:git-token]'],
    [/sk-[A-Za-z0-9_-]{16,}/g, '[REDACTED:api-key]'],
    [/AIza[0-9A-Za-z_-]{20,}/g, '[REDACTED:api-key]'],
    [/AKIA[0-9A-Z]{16}/g, '[REDACTED:aws-key]'],
    [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, '[REDACTED:jwt]'],
    [/((?:token|key|secret|password|passwd|pwd)\s*[:=]\s*)[^\s;,}\]]+/gi, '$1[REDACTED:<secret>]'],
    [/\b(Bearer\s+)[A-Za-z0-9._~+\/=-]{12,}/gi, '$1[REDACTED:<token>]'],
    [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED:private-key]'],
  ]
  for (const [re, replacement] of replacers) out = out.replace(re, replacement)
  return out
}

  async function http(method, path, body) {
    try {
      const base = new URL(SERVER)
      if (base.protocol !== 'http:' && base.protocol !== 'https:') throw new Error('unsupported server protocol')
      const url = new URL(path, base)
      const token = readToken()
      const options = {
        method,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        signal: AbortSignal.timeout(25000),
      }
      if (body !== undefined && body !== null) options.body = JSON.stringify(body)
      const response = await fetch(url, options)
      const data = await response.json()
      if (!response.ok) return { ok: false, error: `HTTP ${response.status}`, data }
      return { ok: true, data }
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  }

  async function readKey(path) {
    const res = await http('GET', '/v1/settings/deepmemory.' + path)
    if (res.ok && res.data && res.data.value !== undefined && res.data.value !== null) return res.data.value
    return undefined
  }

  async function readKeyOr(paths) {
    for (const p of paths) {
      const v = await readKey(p)
      if (v !== undefined) return v
    }
    return undefined
  }

  async function loadConfig() {
    try {
      const v = await readKeyOr(['server_url'])
      if (v !== undefined) SERVER = String(v)
      const w = await readKeyOr(['workspace'])
      if (w !== undefined) WORKSPACE = String(w)
      const t = await readKeyOr(['reflection_engine.summary_trigger_messages', 'reflection_engine.summary_trigger_rounds', 'extract_threshold'])
      if (t !== undefined) EXTRACT_THRESHOLD = Number(t) || 4
      const k = await readKeyOr(['recall_engine.top_k', 'recall_k'])
      if (k !== undefined) RECALL_K = Number(k) || 5
      const ie = await readKeyOr(['injection.inject_enabled', 'inject_enabled'])
      if (ie !== undefined) INJECT_ENABLED = Boolean(ie)
      const ic = await readKeyOr(['injection.inject_card', 'inject_card'])
      if (ic !== undefined) INJECT_CARD = Boolean(ic)
      const ee = await readKeyOr(['reflection_engine.extract_enabled', 'extract_enabled'])
      if (ee !== undefined) EXTRACT_ENABLED = Boolean(ee)
      const ep = await readKeyOr(['reflection_engine.extract_provider', 'extract_provider'])
      if (ep !== undefined && ep !== null && String(ep).trim()) EXTRACT_PROVIDER = String(ep).trim()
      const em = await readKeyOr(['reflection_engine.extract_model', 'extract_model'])
      if (em !== undefined && em !== null && String(em).trim()) EXTRACT_MODEL = String(em).trim()
      const te = await readKeyOr(['agent_tools.tools_enabled', 'tools_enabled'])
      if (te !== undefined) TOOLS_ENABLED = Boolean(te)
      const dr = await readKeyOr(['importance_decay.decay_rate', 'decay_rate'])
      if (dr !== undefined) DECAY_RATE = Number(dr) || 0.01
      state.lastConfigLoad = Date.now()
      console.log('[deepmemory] config: inject=' + INJECT_ENABLED + ' card=' + INJECT_CARD + ' extract=' + EXTRACT_ENABLED + ' decay=' + DECAY_RATE + ' k=' + RECALL_K + ' thr=' + EXTRACT_THRESHOLD + ' ws=' + WORKSPACE)
    } catch (e) {
      console.log('[deepmemory] config load failed, using previous values')
    }
  }

  function sessionIdOf(session) {
    try {
      if (session && session.header && typeof session.header.id === 'string') return session.header.id
      if (session && typeof session.id === 'string') return session.id
      if (session && session.header && typeof session.header.sessionId === 'string') return session.header.sessionId
    } catch (e) {}
    return ''
  }

  async function isEnabled(sessionId) {
    if (!sessionId) return true
    if (enabledCache.has(sessionId)) return enabledCache.get(sessionId)
    const res = await http('GET', '/v1/settings/session_enabled:' + encodeURIComponent(sessionId))
    const val = res.ok && res.data && typeof res.data.value === 'boolean' ? res.data.value : true
    enabledCache.set(sessionId, val)
    return val
  }

  async function setEnabled(sessionId, value) {
    if (!sessionId) return
    enabledCache.set(sessionId, value)
    await http('POST', '/v1/settings/set', { key: 'session_enabled:' + sessionId, value: value })
  }

  function formatMemories(results, limit) {
    if (!results || !results.length) return ''
    const k = limit || RECALL_K
    // 稳定性 + 重要度语义：按 importance 降序（高重要度在前，行序即重要度），
    // id 兜底（importance 不变则文本稳定）；不写浮点进文本（模型看行序感知）
    let items = (results.slice(0, k)).slice().sort(function (a, b) {
      const ai = Number(a.importance || 0)
      const bi = Number(b.importance || 0)
      if (bi !== ai) return bi - ai
      return String(a.id || '').localeCompare(String(b.id || ''))
    })
    // ④ 偏好保底：importance>=0.8 的偏好/高价值记忆保证在注入内（即使语义弱）
    const threshold = 0.8
    const preferred = results.filter(function (r) { return (r.type === 'preference' || Number(r.importance || 0) >= threshold) })
    const ids = new Set(items.map(function (r) { return r.id }))
    for (const x of preferred) {
      if (items.length >= k + 3) break
      if (!ids.has(x.id)) { items.push(x); ids.add(x.id) }
    }
    // 保底追加后按 importance 重排（高重要度恒在前）
    items.sort(function (a, b) {
      const ai = Number(a.importance || 0)
      const bi = Number(b.importance || 0)
      if (bi !== ai) return bi - ai
      return String(a.id || '').localeCompare(String(b.id || ''))
    })
    // 分类组织：preference/rule → 规则类；goal/decision → 决策；fact → 事实
    const groups = []
    const byType = function (pred) { return items.filter(pred) }
    const pushGroup = function (title, arr) {
      if (!arr.length) return
      const lines = arr.map(function (r) {
        const sid = String(r.id || '').slice(-6)
        const sc = r.scope || '?'
        return '  - [' + sid + ' ' + sc + '] ' + String(r.content || '').slice(0, 240)
      })
      groups.push('[' + title + ']\n' + lines.join('\n'))
    }
    pushGroup('规则与偏好', byType(function (r) { return r.type === 'preference' }))
    pushGroup('决定与目标', byType(function (r) { return r.type === 'decision' || r.type === 'goal' || r.type === 'plan' }))
    pushGroup('事实与事件', byType(function (r) { return r.type === 'fact' || r.type === 'episode' }))
    if (!groups.length) {
      const lines = items.map(function (r) {
        const sid = String(r.id || '').slice(-6)
        return '  - [' + sid + ' ' + (r.scope || '?') + '] ' + String(r.content || '').slice(0, 240)
      })
      return '[长期记忆召回]\n' + lines.join('\n') + '\n[/长期记忆]\n'
    }
    return '[长期记忆召回]\n' + groups.join('\n') + '\n[/长期记忆]\n'
  }

  async function resolveModelRoute(llm, preferredProvider, preferredModel) {
    const provider = String(preferredProvider || EXTRACT_PROVIDER || '').trim()
    const model = String(preferredModel || EXTRACT_MODEL || '').trim()
    let preferred = { provider, model }
    if (provider && model) return preferred
    let providers = []
    try {
      providers = (await llm.listProviders()).map((p) => p.id || p.provider || p.name).filter(Boolean)
    } catch {}
    const candidates = []
    if (provider) candidates.push(provider)
    if (providers.includes('uuapi') && !candidates.includes('uuapi')) candidates.push('uuapi')
    for (const p of providers) if (!candidates.includes(p)) candidates.push(p)
    for (const p of candidates) {
      try {
        const models = await llm.listModels(p)
        const pick = model
          ? models.find((m) => String(m.id || m.name || '').includes(model))
          : models.find((m) => /flash|v4|chat/i.test(String(m.id || m.name || '')))
        const chosen = pick || models[0]
        if (chosen) return { provider: p, model: chosen.id || chosen.name }
      } catch {}
    }
    return { provider: provider || 'uuapi', model: model || 'deepseek-v4-flash' }
  }

  async function extract(dialog, signal) {
    const llm = ctx.get('llm')
    if (!llm) return null
    const route = await resolveModelRoute(llm, null, null)
    let out = ''
    try {
      const stream = llm.stream({
        provider: route.provider,
        model: route.model,
        system: EXTRACT_SYSTEM,
        messages: [{ role: 'user', content: [{ type: 'text', text: redactSensitive(dialog).slice(0, 8000) }] }],
        temperature: 0.2,
        signal: signal || undefined,
      })
      for await (const chunk of stream) {
        if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') out += chunk.text
        else if (chunk && (chunk.type === 'error' || chunk.type === 'aborted')) break
      }
    } catch (e) {
      console.error('[deepmemory] extract stream failed', String(e))
      return null
    }
    const start = out.indexOf('{')
    const end = out.lastIndexOf('}')
    if (start < 0 || end <= start) {
      console.error('[deepmemory] extract no JSON in LLM output (len=' + out.length + '): ' + out.slice(0, 300).replace(/\s+/g, ' '))
      return null
    }
    try {
      return JSON.parse(out.slice(start, end + 1))
    } catch (e) {
      console.error('[deepmemory] extract parse failed: ' + out.slice(0, 240).replace(/\s+/g, ' '))
      return null
    }
  }

  function recentQuery(sessionId, seed) {
    const recent = recentBySession.get(sessionId) || []
    // 优先用真实上下文：最近消息全文（前 1500 字符），尾部拼 seed 保底
    const context = recent.slice(-6).join('\n')
    if (context) return (context.slice(0, 1500) + '\n' + String(seed || '').slice(0, 200)).slice(0, 1800)
    return String(seed || '').slice(0, 300)
  }

  function cardText(card) {
    if (!card) return ''
    const payload = card.payload || {}
    const lines = []
    if (payload.goal) lines.push('目标: ' + String(payload.goal).slice(0, 120))
    if (payload.current_plan) lines.push('当前方案: ' + String(payload.current_plan).slice(0, 200))
    if (payload.next_steps && payload.next_steps.length) lines.push('下一步: ' + payload.next_steps.slice(0, 3).join('；'))
    return lines.length ? '[会话状态]\n' + lines.join('\n') + '\n[/会话状态]\n' : ''
  }

  // 会话刷新去重/等待：user/message 启动，assemble await；
  // 回合内冻结 = 仅 userCount 变化（新用户消息）才启动新刷新
  const pendingRefresh = new Map()
  function ensureSessionRefresh(sessionId, completionLimit) {
    const cur = (state.userCounts && state.userCounts[sessionId]) || 0
    const last = userCountBySession.get(sessionId) || 0
    if (initializedSessions.has(sessionId) && cur === last) return pendingRefresh.get(sessionId) || null
    userCountBySession.set(sessionId, cur)
    const q = recentQuery(sessionId, '当前会话目标、计划、决定、偏好和相关工作上下文')
    const limit = completionLimit || Math.max(1, Math.min(20, Number(RECALL_K)))
    const p = refreshMemoryCache(sessionId, q, limit).finally(function () {
      pendingRefresh.delete(sessionId)
    })
    pendingRefresh.set(sessionId, p)
    return p
  }

  async function refreshMemoryCache(sessionId, query, limit) {
    if (!sessionId) return false
    if (refreshes.has(sessionId)) return refreshes.get(sessionId)
    const refresh = (async () => {
      const sres = await http('POST', '/v1/memories/search', {
        query: query || '当前会话目标、计划、决定、偏好和相关工作上下文',
        k: limit || RECALL_K,
        session_id: sessionId,
        workspace_id: WORKSPACE,
      })
      if (!sres.ok) return false
      let nextCardText = ''
      if (INJECT_CARD) {
        const cres = await http('GET', '/v1/v2/cards/' + CARD_KIND + '/' + encodeURIComponent(sessionId))
        if (!cres.ok) return false
        nextCardText = cardText(cres.data && cres.data.card)
      }
      const nextText = nextCardText + formatMemories((sres.data && sres.data.results) || [], limit || RECALL_K)
      const previous = memoryCache.get(sessionId) || ''
      initializedSessions.add(sessionId)
      if (nextText === previous) return true
      memoryCache.set(sessionId, nextText)
      state.injectCount += 1
      const detail = ((sres.data && sres.data.results) || []).map(function (r) {
        return (r.id || '?') + '[s' + (r.scope || '?') + '/i' + (r.importance || 0) + '/score' + (r.final_score || 0) + ']'
      })
      console.log('[deepmemory] session memory cache updated sid=' + String(sessionId).slice(0, 12) + ' (total ' + state.injectCount + ') mems=' + JSON.stringify(detail) + ' size=' + String(memoryCache.get(sessionId) || '').length)
      state.lastInjectionDetail = detail
      return true
    })().finally(() => refreshes.delete(sessionId))
    refreshes.set(sessionId, refresh)
    return refresh
  }

  // 记忆注入：按 agent scope 同步读取会话缓存，不进入消息序列。
  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt) {
    ctx.effect(() => systemPrompt.section({
      name: 'deepmemory',
      order: INJECT_ORDER,
      text: (context) => {
        const agent = context && context.agent
        const sessionId = agent && agent.id ? String(agent.id) : ''
        const base = sessionId ? (memoryCache.get(sessionId) || '') : ''
        return base ? base + '\n\n' + GUIDE : ''
      },
    }))
  } else {
    console.error('[deepmemory] systemPrompt unavailable')
  }

  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    const assembled = await next()
    const agent = context && context.agent
    const sessionId = agent && agent.id ? String(agent.id) : ''
    if (!sessionId) return assembled
    try {
      if (Date.now() - state.lastConfigLoad > 60000) await loadConfig()
      const automation = await http('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId))
      const automationConfig = automation.ok && automation.data && automation.data.config ? automation.data.config : {}
      const automationValue = automationConfig['context_automation.enabled']
      const automationEnabled = automationValue === undefined || automationValue === null
        ? true
        : !(typeof automationValue === 'string' && ['false', '0', 'no', 'off', ''].includes(automationValue.trim().toLowerCase())) && Boolean(automationValue)
      const completionLimit = Math.max(1, Math.min(20, Number(automationConfig['context_automation.memory_completion_k'] || RECALL_K)))
      if (!INJECT_ENABLED || !automationEnabled || !(await isEnabled(sessionId))) {
        memoryCache.delete(sessionId)
        initializedSessions.delete(sessionId)
      } else {
        // 回合内冻结：待 user/message 已启动的刷新（若 userCount 变化则新启动），
        // 完成后注入即新文本——首请求带新注入，回合内不再变
        await ensureSessionRefresh(sessionId, completionLimit)
      }
    } catch (e) {
      console.error('[deepmemory] prompt cache refresh failed', String(e))
    }
    const text = (memoryCache.get(sessionId) || '') ? String(memoryCache.get(sessionId) || '') + '\n\n' + GUIDE : ''
    return {
      ...assembled,
      sections: assembled.sections.map((section) => section.name === 'deepmemory' ? { ...section, text: text } : section),
    }
  })

  ctx.on('session/event', (session, event) => {
    try {
      const t = event && event.type
      const sid = sessionIdOf(session)
      if (t === 'compaction/summary') {
        if (sid) initializedSessions.delete(sid)
        return
      }
      if (t !== 'user/message' && t !== 'assistant/message') return
      if (!sid || enabledCache.get(sid) === false) return
      const data = event.data || {}
      const msg = t === 'user/message' ? data : (data.message || {})
      let text = ''
      if (Array.isArray(msg.content)) {
        for (const b of msg.content) {
          if (b && typeof b === 'object' && b.type === 'text' && typeof b.text === 'string') text += b.text
        }
      } else if (typeof msg.content === 'string') {
        text = msg.content
      }
      if (t === 'user/message') {
        state.userCounts = state.userCounts || {}
        state.userCounts[sid] = (state.userCounts[sid] || 0) + 1
        // 用户消息到达即启动刷新（assemble 会 await 它）：
        // 保证本回合首请求已带新注入（miss=首请求允许），回合内不再切换
        ensureSessionRefresh(sid)
      }
      if (text && text.trim()) {
        let bucket = buckets.get(sid)
        if (!bucket) { bucket = []; buckets.set(sid, bucket) }
        bucket.push({ role: (t === 'user/message' ? 'user' : 'assistant'), text: text.slice(0, 800) })
        if (bucket.length > 40) bucket.shift()
        let recent = recentBySession.get(sid)
        if (!recent) { recent = []; recentBySession.set(sid, recent) }
        recent.push((t === 'user/message' ? '用户: ' : '助手: ') + text.slice(0, 600))
        if (recent.length > 10) recent.shift()
        // 按长度累计（触发摘要链/上下文刷新用）
        recentBytes.set(sid, (recentBytes.get(sid) || 0) + text.length)
      }
    } catch (e) {}
  })

  ctx.on('agent/turn-stopping', async (payload) => {
    if (!EXTRACT_ENABLED) return
    const sid = payload.agent && payload.agent.id ? String(payload.agent.id) : ''
    if (!sid || !(await isEnabled(sid))) return
    const bucket = buckets.get(sid)
    if (!bucket || bucket.length < EXTRACT_THRESHOLD) return
    buckets.delete(sid)
    const dialog = bucket.map((m) => (m.role === 'user' ? '用户: ' : '助手: ') + m.text).join('\n')
    console.log('[deepmemory] extracting from ' + bucket.length + ' messages...')
    const result = await extract(dialog, payload.signal)
    if (!result) return
    let memoryChanged = false
    if (result.memories && result.memories.length) {
      const items = result.memories.filter((m) => m && m.content).map((m) => {
        const rawContent = redactSensitive(m.content)
        const rawKeyFacts = redactSensitive(m.key_facts || '')
        const rawPersona = redactSensitive(m.persona_summary || '')
        const rawAtom = Array.isArray(m.atoms) ? m.atoms.map((a) => Object.assign({}, a, { content: redactSensitive(a.content || '') })) : []
        const rawEntities = Array.isArray(m.entities) ? m.entities.map((e) => Object.assign({}, e, { name: redactSensitive(e.name || '') })) : []
        const rawRelations = Array.isArray(m.relations) ? m.relations.map((r) => Object.assign({}, r, { source: redactSensitive(r.source || ''), target: redactSensitive(r.target || ''), relation: redactSensitive(r.relation || '') })) : []
        return {
        content: String(rawContent).slice(0, 500),
        key_facts: String(rawKeyFacts).slice(0, 600),
        persona_summary: String(rawPersona).slice(0, 500),
        type: m.type || 'fact',
        domain: m.domain || 'work',
        scope: m.scope || 'workspace',
        importance: typeof m.importance === 'number' ? m.importance : 0.5,
        workspace_id: WORKSPACE,
        session_id: sid,
        dialog_scoped: true,
        topic_id: String(m.topic || '').slice(0, 60),
        atoms: rawAtom,
        entities: rawEntities,
        relations: rawRelations,
        source: redactSensitive(dialog).slice(0, 2000),
        }
      })
      if (items.length) {
        const res = await http('POST', '/v1/memories/add_batch', { items: items })
        if (res.ok) {
          memoryChanged = true
          state.extractCount += items.length
          console.log('[deepmemory] extracted ' + items.length + ' memories (total ' + state.extractCount + '): ' + JSON.stringify((res.data && res.data.added) || []).slice(0, 400))
        }
      }
    }
    // AI 驱动状态卡更新：抽取到 card 即写回 v2 卡（增量修订，expected_version 防止覆盖）
    if (INJECT_CARD && result.card && typeof result.card === 'object') {
      try {
        const cur = await http('GET', '/v1/v2/cards/' + CARD_KIND + '/' + encodeURIComponent(sid))
        const existing = cur.ok && cur.data && cur.data.card ? cur.data.card : null
        const put = await http('PUT', '/v1/v2/cards/' + CARD_KIND + '/' + encodeURIComponent(sid), {
          expected_version: existing ? Number(existing.version || 0) : 0,
          payload: {
            goal: redactSensitive(String(result.card.goal || '')),
            current_plan: redactSensitive(String(result.card.current_plan || '')),
            key_decisions: Array.isArray(result.card.key_decisions) ? result.card.key_decisions.slice(0, 4).map((x)=>redactSensitive(String(x))) : [],
            in_progress: Array.isArray(result.card.in_progress) ? result.card.in_progress.slice(0, 4).map((x)=>redactSensitive(String(x))) : [],
            next_steps: Array.isArray(result.card.next_steps) ? result.card.next_steps.slice(0, 4).map((x)=>redactSensitive(String(x))) : [],
          },
          actor: 'main_agent',
          reason: 'AI turn-stopping state card sync',
        })
        if (put.ok) {
          memoryChanged = true
          console.log('[deepmemory] AI updated state card v' + ((put.data && put.data.card && put.data.card.version) || '?') + ' session ' + sid.slice(0, 12))
        } else {
          console.log('[deepmemory] AI card write skipped: ' + (put.error || 'unknown'))
        }
      } catch (e) {
        console.log('[deepmemory] AI card write failed: ' + String(e))
      }
    }
    // AI 任务板更新：仅明确的 tasks 输出才落盘
    if (result.tasks && Array.isArray(result.tasks) && result.tasks.length) {
      try {
        for (const t of result.tasks.slice(0, 5)) {
          const title = redactSensitive(String(t && t.title || '')).trim().slice(0, 120)
          if (!title) continue
          const status = ['planned', 'todo', 'in_progress', 'completed', 'failed'].includes(t.status) ? t.status : 'todo'
          const created = await http('POST', '/v1/v2/tasks', {
            title: title,
            status: status,
            workspace_id: WORKSPACE,
            session_id: sid,
            description: redactSensitive(String(t.description || '')).slice(0, 500),
            blocked: status === 'in_progress' ? Boolean(t.blocked) : false,
            block_reason: redactSensitive(String(t.reason || t.block_reason || '')),
          })
          if (created.ok) {
            console.log('[deepmemory] AI task created: ' + title + ' [' + status + ']')
          } else {
            console.log('[deepmemory] AI task create failed: ' + (created.error || 'unknown'))
          }
        }
      } catch (e) {
        console.log('[deepmemory] AI tasks write failed: ' + String(e))
      }
    }
    // 注入刷新唯一入口=assemble 的 userCount 守卫（回合内冻结）。
    // turn-stopping 只写库（抽取/状态卡/任务），不刷注入：
    // 内存库更新在下一回合首请求（userCount 变）时自然并入，避免回合中途断前缀。
    // (修前: if (memoryChanged) await refreshMemoryCache(...) —— 回合中段刷新击穿缓存)
  })

  const commands = ctx.get('commands')
  if (commands) {
    ctx.effect(() => commands.register({
      name: 'memory',
      description: 'toggle or inspect the long-term memory system for this conversation',
      input: { hint: '[on|off|status|clean]' },
      handler: async (invocation) => {
        const sid = invocation.agent && invocation.agent.id ? String(invocation.agent.id) : ''
        const arg = (invocation.rawInput || '').trim().toLowerCase()
        if (arg === 'on' || arg === 'enable') {
          await setEnabled(sid, true)
          initializedSessions.delete(sid)
          return { kind: 'success', text: '记忆已开启（本会话）。注入、捕获、抽取全部生效。' }
        }
        if (arg === 'off' || arg === 'disable') {
          await setEnabled(sid, false)
          if (buckets.has(sid)) buckets.delete(sid)
          memoryCache.delete(sid)
          initializedSessions.add(sid)
          return { kind: 'success', text: '记忆已关闭（本会话）。不再注入、捕获、抽取；可用 /memory on 重新开启。' }
        }
        if (arg === 'clean') {
          const res = await http('POST', '/v1/maintenance/decay', { force: true, decay_rate: DECAY_RATE })
          if (!res.ok) return { kind: 'error', text: '衰减执行失败: ' + res.error }
          return { kind: 'success', text: '已执行衰减：' + (res.data.decayed || 0) + ' 条记忆降权，' + (res.data.archived || 0) + ' 条归档。当前活跃 ' + res.data.documents + ' 条。' }
        }
        const on = await isEnabled(sid)
        const stats = await http('GET', '/v1/stats')
        const docs = stats.ok ? stats.data.documents : '?'
        return { kind: 'success', text: '记忆状态：' + (on ? '开启' : '关闭') + '。记忆库 ' + docs + ' 条；已注入 ' + state.injectCount + ' 次；已抽取 ' + state.extractCount + ' 条。' }
      },
    }))
  }

  function textRender(value) {
    return [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }]
  }

  const outSchema = { type: 'object', additionalProperties: true }

  const recallTool = defineTool({
    name: 'memory_recall',
    description: 'Recall long-term memories semantically. Use concise recall keywords instead of copying the full message. Prefer the injected memory block; call this ONLY when a clear gap exists, and retrieve everything needed in ONE call (set k up to 10) instead of multiple small calls.',
    parameters: {
      query: { type: 'string', required: true, description: 'Concise recall keywords for long-term memory.' },
      k: { type: 'integer', description: 'Maximum number of memories to return.', default: 5 },
      persona: { type: 'string', description: 'Optional persona id filter. Leave empty for shared memories.' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!TOOLS_ENABLED) return { count: 0, results: [], error: 'deepmemory tools disabled' }
      const res = await http('POST', '/v1/memories/search', { query: String(args.query || ''), k: args.k || 5, workspace_id: WORKSPACE, persona_id: String(args.persona || '') })
      if (!res.ok) return { count: 0, results: [], error: res.error }
      const items = (res.data.results || []).map((r) => ({ id: r.id, content: redactSensitive(r.content), type: r.type, domain: r.domain, scope: r.scope, importance: r.importance, score: r.final_score }))
      return { count: items.length, results: items }
    },
  })

  const saveTool = defineTool({
    name: 'memory_save',
    description: 'Save one durable long-term memory. Use for user preferences, key facts, decisions, or plans the user asks to remember.',
    parameters: {
      content: { type: 'string', required: true, description: 'The memory content, concise and self-contained.' },
      type: { type: 'string', description: 'fact | preference | decision | episode | plan', default: 'fact' },
      domain: { type: 'string', description: 'work | life', default: 'work' },
      scope: { type: 'string', description: 'session | workspace | global', default: 'workspace' },
      importance: { type: 'number', description: 'Importance 0-1.', default: 0.6 },
      persona: { type: 'string', description: 'Optional persona id binding. Leave empty for shared memories.' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!TOOLS_ENABLED) return { saved: false, error: 'deepmemory tools disabled' }
      const payload = {
        content: redactSensitive(String(args.content || '')),
        type: args.type || 'fact',
        domain: args.domain || 'work',
        scope: args.scope || 'workspace',
        workspace_id: WORKSPACE,
        importance: typeof args.importance === 'number' ? args.importance : 0.6,
        persona_id: String(args.persona || ''),
      }
      const res = await http('POST', '/v1/memories/add', payload)
      if (!res.ok) return { saved: false, error: res.error }
      return { saved: true, id: res.data.id }
    },
  })

  const briefingTool = defineTool({
    name: 'memory_briefing',
    description: 'Get a memory briefing relevant to a subtask or subagent. Returns memories relevant to the task description.',
    parameters: {
      task: { type: 'string', required: true, description: 'Task description the briefing should cover.' },
      k: { type: 'integer', description: 'Maximum number of memories.', default: 8 },
      persona: { type: 'string', description: 'Optional persona id filter. Leave empty for shared memories.' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!TOOLS_ENABLED) return { count: 0, briefing: '', error: 'deepmemory tools disabled' }
      const res = await http('POST', '/v1/memories/search', { query: String(args.task || ''), k: args.k || 8, workspace_id: WORKSPACE, persona_id: String(args.persona || '') })
      if (!res.ok) return { count: 0, briefing: '', error: res.error }
      const lines = (res.data.results || []).map((r) => '- ' + redactSensitive(String(r.content || '')))
      return { count: lines.length, briefing: lines.join('\n') }
    },
  })

  ctx.effect(() => ctx.tools.register(recallTool))
  ctx.effect(() => ctx.tools.register(saveTool))
  ctx.effect(() => ctx.tools.register(briefingTool))

  loadConfig().then(() => console.log('[deepmemory] ready (preset plugin P2: relations + cross-turn query + graph route)'))
}
