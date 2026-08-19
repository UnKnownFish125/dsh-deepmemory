// harness-memory — long-term memory plugin for the DeepSeek Harness.
//
// Preset-plane plugin (mounted as a relative-path row from agent.cordis.yml).
// Talks to the local dsh-memory-server (systemd, http://localhost:6230)
// which owns SQLite + FAISS + BM25 + graph storage.
//
// Capabilities (P2):
//  - agent/pre-step: recall injection (workspace card + top memories),
//    query expanded with recent-turn context slices
//  - agent/turn-stopping: cheap-LLM extraction — rich fields, atoms, entities,
//    relations, source retention, card update
//  - tools: memory_recall / memory_save / memory_briefing (persona-aware)
//  - /memory command: on|off|status|clean (per-conversation, persisted)
//  - config centre reload (deepmemory.* keys) every minute
//  - daily importance decay with access reinforcement (server-side)

import fs from 'node:fs'
import { defineTool } from '/usr/local/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js'

export const name = 'deepmemory'

export const inject = ['tools', 'timer']

export function apply(ctx) {
  const state = { injectCount: 0, extractCount: 0, memText: '', lastConfigLoad: 0, recent: [] }
  const buckets = new Map()
  const enabledCache = new Map()
  let SERVER = 'http://localhost:6230'
  const TOKEN_FILE = process.env.MEMORY_API_TOKEN_FILE || `${process.env.DSH_HOME || process.env.HOME}/.dsh-memory-api-token`
  function readToken() {
    try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim() } catch { return '' }
  }
  let WORKSPACE = 'deepseek-hardness'
  let EXTRACT_THRESHOLD = 4
  let RECALL_K = 5
  const INJECT_ORDER = 50
  let INJECT_ENABLED = true
  let INJECT_CARD = true
  let EXTRACT_ENABLED = true
  let TOOLS_ENABLED = true
  let DECAY_RATE = 0.01

  const EXTRACT_SYSTEM = [
    '你是长期记忆抽取器。从对话片段中提取值得长期记住的内容，并做结构化解构。',
    '规则：',
    '1. memories 只提取：事实(fact)、偏好(preference)、决定(decision)、计划(plan)、事件约定(episode)。忽略闲聊和过程细节。',
    '2. 每条记忆 content 用简洁完整的一句话；key_facts 提取其中的关键实体与主题短语（分号分隔，≤5 个，用于检索）；persona_summary 为面向模型注入的一句话表述（无特殊表述时留空）。',
    '3. domain：项目/技术/工作任务=work，个人生活/习惯/人际=life。scope：仅当前对话=session，当前项目/工作区=workspace，用户个人长期适用=global。importance：0-1，偏好与重要约定 0.7+。',
    '4. atoms：把每条记忆拆成独立事实单元（可 0-3 条），每单元含 atom_type（factual 事实/preference 偏好/decision 决定/episodic 事件/planned 计划/relational 关系）、content（独立自包含一句话）、ttl_days（factual=180, preference=60, decision=30, episodic=7, planned=2, relational=90）、decay_type（exponential/linear/step）、importance。',
    '5. entities：抽取记忆中的实体名词列表（人名/项目/工具/概念），每项 {name, kind: person|project|tool|concept|other}。',
    '6. relations：实体之间的关系边列表（可 0-3 条），每项 {source, relation, target}，source/target 必须是 entities 里出现过的实体名，relation 用短动词短语（如 "使用"、"依赖"、"属于"、"负责"）。',
    '7. card：增量更新工作区状态卡（goal/current_plan 各一句话；key_decisions 追加新决定≤3条；in_progress/next_steps 各≤4条；无需变化时 card 为 null）。',
    '8. 严格只输出一个 JSON 对象（不要 markdown 代码块）：',
    '{"memories":[{"content":"...","key_facts":"词1;词2","persona_summary":"...或空","type":"fact","domain":"work","scope":"workspace","importance":0.7,"atoms":[{"atom_type":"factual","content":"...","ttl_days":180,"decay_type":"exponential","importance":0.6}],"entities":[{"name":"...","kind":"project"}],"relations":[{"source":"...","relation":"...","target":"..."}]}],"card":{"goal":"...","current_plan":"...","key_decisions":["..."],"in_progress":["..."],"next_steps":["..."]}}',
    '没有值得记忆的内容时 memories 为 []。',
  ].join('\n')

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

  function textOf(content) {
    if (!Array.isArray(content)) return ''
    let s = ''
    for (const b of content) {
      if (b && typeof b === 'object' && b.type === 'text' && typeof b.text === 'string') s += b.text
    }
    return s
  }

  function findLatestUserText(messages) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m && m.role === 'user') {
        const t = textOf(m.content)
        if (t && t.trim()) return { index: i, text: t }
      }
    }
    return null
  }

  function formatMemories(results) {
    if (!results || !results.length) return ''
    const lines = results.slice(0, RECALL_K).map((r) => '- [' + (r.type || 'fact') + '/' + (r.scope || '?') + '] ' + String(r.content || '').slice(0, 240))
    return '[长期记忆召回]\n' + lines.join('\n') + '\n[/长期记忆]\n'
  }

  async function pickCheapModel(llm) {
    try {
      const models = await llm.listModels('deepseek-official')
      const names = (models || []).map((m) => (m && (m.id || m.name)) || '').filter(Boolean)
      const cheap = names.find((n) => /chat/i.test(n) && !/reasoner/i.test(n)) || names[0]
      if (cheap) return cheap
    } catch (e) {
      console.error('[deepmemory] listModels failed', String(e))
    }
    return null
  }

  async function extract(dialog, signal) {
    const llm = ctx.get('llm')
    if (!llm) return null
    const model = await pickCheapModel(llm)
    if (!model) return null
    let out = ''
    try {
      const stream = llm.stream({
        provider: 'deepseek-official',
        model: model,
        system: EXTRACT_SYSTEM,
        messages: [{ role: 'user', content: [{ type: 'text', text: dialog.slice(0, 8000) }] }],
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
    if (start < 0 || end <= start) return null
    try {
      return JSON.parse(out.slice(start, end + 1))
    } catch (e) {
      console.error('[deepmemory] extract parse failed: ' + out.slice(0, 200))
      return null
    }
  }

  // 记忆注入：systemPrompt section（同步读缓存），不进消息序列
  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt) {
    ctx.effect(() => systemPrompt.section({
      name: 'deepmemory',
      order: INJECT_ORDER,
      text: () => state.memText || '',
    }))
  } else {
    console.error('[deepmemory] systemPrompt unavailable')
  }

  ctx.on('agent/pre-step', async (payload, next) => {
    if (payload.step !== 1) return next()
    let decision
    try { decision = await next() } catch (e) { throw e }
    const agentId = payload.agent && payload.agent.id ? String(payload.agent.id) : ''
    try {
      if (Date.now() - state.lastConfigLoad > 60000) await loadConfig()
      if (!INJECT_ENABLED || !(await isEnabled(agentId))) { state.memText = ''; return decision }
      if (!decision || decision.kind !== 'enter' || !Array.isArray(decision.messages)) return decision
      const found = findLatestUserText(decision.messages)
      if (!found) return decision
      // 跨轮次上下文扩展：最新消息 + 最近几轮消息片段
      const recent = state.recent.length ? '\n最近上下文: ' + state.recent.join(' | ') : ''
      const query = (found.text.slice(0, 300) + recent).slice(0, 700)
      const sres = await http('POST', '/v1/memories/search', { query: query, k: RECALL_K, session_id: agentId, workspace_id: WORKSPACE })
      let cardText = ''
      if (INJECT_CARD) {
        const cres = await http('GET', '/v1/cards/' + WORKSPACE)
        if (cres.ok && cres.data && cres.data.card) {
          const c = cres.data.card
          const lines = []
          if (c.goal) lines.push('目标: ' + String(c.goal).slice(0, 120))
          if (c.current_plan) lines.push('当前方案: ' + String(c.current_plan).slice(0, 200))
          if (c.next_steps && c.next_steps.length) lines.push('下一步: ' + c.next_steps.slice(0, 3).join('；'))
          if (lines.length) cardText = '[工作区状态]\n' + lines.join('\n') + '\n[/工作区状态]\n'
        }
      }
      const memText = cardText + formatMemories(sres.ok ? (sres.data && sres.data.results) || [] : [])
      if (memText.trim()) {
        state.memText = memText
        state.injectCount += 1
        console.log('[deepmemory] memory cached for system prompt (turn ' + payload.turn + ', total ' + state.injectCount + ')')
      }
      return decision
    } catch (e) {
      console.error('[deepmemory] pre-step cache failed', String(e))
      return decision
    }
  })

  ctx.on('session/event', (session, event) => {
    try {
      const t = event && event.type
      if (t !== 'user/message' && t !== 'assistant/message') return
      const sid = sessionIdOf(session)
      if (!sid || enabledCache.get(sid) === false) return
      const data = event.data || {}
      const msg = data.message || {}
      let text = ''
      if (Array.isArray(msg.content)) {
        for (const b of msg.content) {
          if (b && typeof b === 'object' && b.type === 'text' && typeof b.text === 'string') text += b.text
        }
      } else if (typeof msg.content === 'string') {
        text = msg.content
      }
      if (text && text.trim()) {
        let bucket = buckets.get(sid)
        if (!bucket) { bucket = []; buckets.set(sid, bucket) }
        bucket.push({ role: (t === 'user/message' ? 'user' : 'assistant'), text: text.slice(0, 800) })
        if (bucket.length > 40) bucket.shift()
        state.recent.push((t === 'user/message' ? '用户: ' : '助手: ') + text.slice(0, 120))
        if (state.recent.length > 6) state.recent.shift()
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
    if (result.memories && result.memories.length) {
      const items = result.memories.filter((m) => m && m.content).map((m) => ({
        content: String(m.content).slice(0, 500),
        key_facts: String(m.key_facts || ''),
        persona_summary: String(m.persona_summary || ''),
        type: m.type || 'fact',
        domain: m.domain || 'work',
        scope: m.scope || 'workspace',
        importance: typeof m.importance === 'number' ? m.importance : 0.5,
        workspace_id: WORKSPACE,
        session_id: sid,
        atoms: Array.isArray(m.atoms) ? m.atoms : [],
        entities: Array.isArray(m.entities) ? m.entities : [],
        relations: Array.isArray(m.relations) ? m.relations : [],
        source: dialog.slice(0, 2000),
      }))
      if (items.length) {
        const res = await http('POST', '/v1/memories/add_batch', { items: items })
        state.extractCount += items.length
        console.log('[deepmemory] extracted ' + items.length + ' memories (total ' + state.extractCount + '): ' + JSON.stringify((res.data && res.data.added) || []).slice(0, 400))
      }
    }
    if (result.card) {
      const cres = await http('POST', '/v1/cards/upsert', {
        workspace_id: WORKSPACE,
        goal: String(result.card.goal || ''),
        current_plan: String(result.card.current_plan || ''),
        key_decisions: Array.isArray(result.card.key_decisions) ? result.card.key_decisions : [],
        in_progress: Array.isArray(result.card.in_progress) ? result.card.in_progress : [],
        next_steps: Array.isArray(result.card.next_steps) ? result.card.next_steps : [],
      })
      if (cres.ok) console.log('[deepmemory] card updated v' + (cres.data && cres.data.version))
    }
  })

  ctx.interval(async () => {
    try {
      const res = await http('POST', '/v1/maintenance/decay', { decay_rate: DECAY_RATE })
      if (res.ok && res.data && !res.data.skipped) console.log('[deepmemory] decay: ' + JSON.stringify(res.data))
    } catch (e) {}
  }, 6 * 3600 * 1000)

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
          return { kind: 'success', text: '记忆已开启（本会话）。注入、捕获、抽取全部生效。' }
        }
        if (arg === 'off' || arg === 'disable') {
          await setEnabled(sid, false)
          if (buckets.has(sid)) buckets.delete(sid)
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
    description: 'Recall long-term memories semantically. Use concise recall keywords instead of copying the full message. Call when the user references past facts, preferences, decisions, or older context.',
    parameters: {

        query: { required: true, type: 'string', description: 'Concise recall keywords for long-term memory.' },
        k: { type: 'integer', description: 'Maximum number of memories to return.', default: 5 },
        persona: { type: 'string', description: 'Optional persona id filter. Leave empty for shared memories.' },
      
},
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!TOOLS_ENABLED) return { count: 0, results: [], error: 'deepmemory tools disabled' }
      const res = await http('POST', '/v1/memories/search', { query: String(args.query || ''), k: args.k || 5, workspace_id: WORKSPACE, persona_id: String(args.persona || '') })
      if (!res.ok) return { count: 0, results: [], error: res.error }
      const items = (res.data.results || []).map((r) => ({ id: r.id, content: r.content, type: r.type, domain: r.domain, scope: r.scope, importance: r.importance, score: r.final_score }))
      return { count: items.length, results: items }
    },
  })

  const saveTool = defineTool({
    name: 'memory_save',
    description: 'Save one durable long-term memory. Use for user preferences, key facts, decisions, or plans the user asks to remember.',
    parameters: {

        content: { required: true, type: 'string', description: 'The memory content, concise and self-contained.' },
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
        content: String(args.content || ''),
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

        task: { required: true, type: 'string', description: 'Task description the briefing should cover.' },
        k: { type: 'integer', description: 'Maximum number of memories.', default: 8 },
        persona: { type: 'string', description: 'Optional persona id filter. Leave empty for shared memories.' },
      
},
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!TOOLS_ENABLED) return { count: 0, briefing: '', error: 'deepmemory tools disabled' }
      const res = await http('POST', '/v1/memories/search', { query: String(args.task || ''), k: args.k || 8, workspace_id: WORKSPACE, persona_id: String(args.persona || '') })
      if (!res.ok) return { count: 0, briefing: '', error: res.error }
      const lines = (res.data.results || []).map((r) => '- ' + String(r.content || ''))
      return { count: lines.length, briefing: lines.join('\n') }
    },
  })

  ctx.effect(() => ctx.tools.register(recallTool))
  ctx.effect(() => ctx.tools.register(saveTool))
  ctx.effect(() => ctx.tools.register(briefingTool))

  loadConfig().then(() => console.log('[deepmemory] ready (preset plugin P2: relations + cross-turn query + graph route)'))
}
