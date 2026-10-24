/**
 * Host half of the memory UI surface plugin.
 * Registers a same-origin proxy route `/mem-api/*` that forwards to the
 * local memory-server (localhost:6230). The browser half only talks to this
 * same-origin route, so no CORS or private-network policy applies.
 */
import http from 'node:http'
import fs from 'node:fs'
import zlib from 'node:zlib'

export const name = 'deepmemory'

// 0.1.7 起不再注入 'settings'：ctx.settings 已由 SettingsProvider 改为
// SettingsForms 服务，没有 register()；本插件也没有其它地方用到它。
export const inject = ['webServer', 'timer']

const TARGET_HOST = 'localhost'
const TARGET_PORT = Number(process.env.MEMORY_SERVER_PORT || 6230)
const PREFIX = '/mem-api'
const TOKEN_FILES = [
  process.env.MEMORY_API_TOKEN_FILE,
  process.env.DSH_HOME ? `${process.env.DSH_HOME}/.dsh-memory-api-token` : '',
  process.env.HOME ? `${process.env.HOME}/.dsh-memory-api-token` : '',
].filter((path, index, paths) => path && paths.indexOf(path) === index)
let consolidationRun = null
let consolidationRunForce = false
let queuedForceRun = null

function readToken() {
  for (const path of TOKEN_FILES) {
    try {
      const token = fs.readFileSync(path, 'utf8').trim()
      if (token) return token
    } catch {}
  }
  return ''
}

function memoryRequest(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body))
    const token = readToken()
    const headers = { Accept: 'application/json' }
    if (payload) {
      headers['Content-Type'] = 'application/json'
      headers['Content-Length'] = String(payload.length)
    }
    if (token) headers.Authorization = `Bearer ${token}`
    const req = http.request({
      host: TARGET_HOST,
      port: TARGET_PORT,
      path,
      method,
      headers,
      timeout: 60000,
    }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        let data
        try { data = text ? JSON.parse(text) : {} } catch { data = { error: text || `HTTP ${res.statusCode}` } }
        if ((res.statusCode || 500) >= 400) reject(new Error(data.error || `HTTP ${res.statusCode}`))
        else resolve(data)
      })
    })
    req.on('timeout', () => req.destroy(new Error('memory-server request timeout')))
    req.on('error', reject)
    if (payload) req.write(payload)
    req.end()
  })
}

function boolValue(value, fallback) {
  if (value === undefined || value === null) return fallback
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['false', '0', 'no', 'off', ''].includes(normalized)) return false
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true
  }
  return Boolean(value)
}

async function resolveModelRoute(llm, preferred) {
  const preferredProvider = preferred && preferred.provider ? String(preferred.provider).trim() : ''
  const preferredModel = preferred && preferred.model ? String(preferred.model).trim() : ''
  if (preferredProvider && preferredModel) return { provider: preferredProvider, model: preferredModel, source: 'configured' }
  let providers = []
  try {
    providers = (await llm.listProviders()).map((p) => p.id || p.provider || p.name).filter(Boolean)
  } catch {}
  const candidates = []
  if (preferredProvider) candidates.push(preferredProvider)
  // 2026-10-01：DS 官模 flash 固定第一顺位（支持图片），uuapi 退第二顺位
  for (const preferred of ['deepseek-official', 'uuapi']) {
    if (providers.includes(preferred) && !candidates.includes(preferred)) candidates.push(preferred)
  }
  for (const p of providers) if (!candidates.includes(p)) candidates.push(p)
  for (const p of candidates) {
    try {
      const models = await llm.listModels(p)
      const pick = preferredModel
        ? models.find((m) => String(m.id || m.name || '').includes(preferredModel))
        : models.find((m) => /flash|v4|chat/i.test(String(m.id || m.name || '')))
      const chosen = pick || models[0]
      if (chosen) return { provider: p, model: chosen.id || chosen.name, source: 'catalog' }
    } catch {}
  }
  return { provider: preferredProvider || 'deepseek-official', model: preferredModel || 'deepseek-flash', source: 'fallback' }
}

// N18：Host 的两个 LLM 流（整合摘要 / 状态卡提取）原先**没有任何超时或取消** ——
// 上游卡住时 agent/turn-stopping 会长时间挂住。统一给一个硬超时。
function hostTimeoutSignal(ms) {
  try {
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
      return AbortSignal.timeout(ms)
    }
  } catch (e) { /* 老运行时无 AbortSignal.timeout：退化为无超时 */ }
  return undefined
}

// N11：与 preset 同源的敏感信息脱敏。Host 原先直接送原始对话给 LLM，
// 对话含凭据时会原值出网（存储端脱敏无法撤销已发生的出网）。
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

async function summarizeGroup(llm, route, group) {
  // N11：整合摘要同样是出网调用，正文先脱敏
  const source = [group.primary_content].concat(group.contents || []).filter(Boolean).map((x) => redactSensitive(String(x)))
  let output = ''
  const stream = llm.stream({
    provider: route.provider,
    model: route.model,
    signal: hostTimeoutSignal(60000),
    system: '你是长期记忆整合器。将同组记忆合并为一段准确、自包含、无重复的规范摘要。保留事实、决定、约束和时间关系，不添加新信息。只输出摘要正文。',
    messages: [{ role: 'user', content: [{ type: 'text', text: source.map((text, index) => `${index + 1}. ${text}`).join('\n').slice(0, 12000) }] }],
    temperature: 0.1,
    maxTokens: 1200,
  })
  for await (const chunk of stream) {
    if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') output += chunk.text
    if (chunk && (chunk.type === 'error' || chunk.type === 'aborted')) throw new Error('consolidation summary stream failed')
  }
  const summary = output.trim()
  if (!summary) throw new Error('consolidation summary was empty')
  return summary.slice(0, 4000)
}

async function extractSessionCard(llm, dialog, existingCard, route = null) {
  const r = route || await resolveModelRoute(llm, {})
  // N11：出网前统一脱敏（原实现把真实对话原样送模型；preset 对应路径为 redactSensitive(dialog)）
  dialog = redactSensitive(String(dialog || ''))
  let lastError = ''
  for (let attempt = 0; attempt < 2; attempt++) {
    let output = ''
    try {
      const stream = llm.stream({
        provider: r.provider,
        model: r.model,
        signal: hostTimeoutSignal(60000),
        system: [
          '你是会话状态卡提取器。根据对话片段提取当前会话状态。',
          '只输出一个 JSON 对象，不要 markdown：',
          '{"card":null} 或 {"card":{"goal":"...","current_plan":"...","key_decisions":["..."],"in_progress":["..."],"next_steps":["..."]}}',
          '输出必须是合并现有状态与新增对话后的完整卡；goal/current_plan 各一句；数组各最多 4 条；没有明确任务、主题或状态变化时 card 为 null。',
        ].join('\n'),
        messages: [{ role: 'user', content: [{ type: 'text', text: (
          '现有状态卡：\n' + JSON.stringify(existingCard || {}) + '\n\n新增对话：\n' + dialog
        ).slice(0, 12000) }] }],
        temperature: 0.1,
        maxTokens: 1600,
      })
      for await (const chunk of stream) {
        if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') output += chunk.text
        if (chunk && (chunk.type === 'error' || chunk.type === 'aborted')) throw new Error('state card extraction stream failed: ' + String(chunk.error || chunk.reason || 'aborted'))
      }
    } catch (error) {
      lastError = 'llm_stream:' + String((error && error.message) || error)
      if (attempt === 1) break
      continue
    }
    const start = output.indexOf('{')
    const end = output.lastIndexOf('}')
    if (start < 0 || end <= start) {
      lastError = 'no_json(len=' + output.length + ')'
      if (attempt === 1) break
      continue
    }
    try {
      const parsed = JSON.parse(output.slice(start, end + 1))
      return parsed.card || null
    } catch (error) {
      lastError = 'parse:' + String((error && error.message) || error)
      if (attempt === 1) break
      continue
    }
  }
  throw new Error('state card extraction failed: ' + lastError)
}

function eventText(event) {
  const type = event && event.type
  if (type !== 'user/message' && type !== 'assistant/message') return ''
  const data = event.data || {}
  const message = type === 'user/message' ? data : (data.message || data)
  if (message.source && message.source.kind === 'plugin' && message.source.plugin === 'compact') return ''
  const content = message.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content.filter((item) => item && item.type === 'text' && typeof item.text === 'string').map((item) => item.text).join('')
}

function stateCardKind(agent) {
  const preset = String((agent && agent.session && agent.session.header && agent.session.header.agentPreset) || '')
  return preset.includes('task') ? 'task' : 'daily'
}

function presetHasMemoryCompletion(agent) {
  const preset = String((agent && agent.session && agent.session.header && agent.session.header.agentPreset) || '')
  return preset.startsWith('harness-memory')
}

function memoryCompletionText(card, results, limit) {
  const sections = []
  const payload = card && card.payload ? card.payload : null
  if (payload) {
    const lines = []
    if (payload.goal) lines.push('目标: ' + String(payload.goal).slice(0, 120))
    if (payload.current_plan) lines.push('当前方案: ' + String(payload.current_plan).slice(0, 200))
    if (Array.isArray(payload.next_steps) && payload.next_steps.length) lines.push('下一步: ' + payload.next_steps.slice(0, 3).join('；'))
    if (lines.length) sections.push('[会话状态]\n' + lines.join('\n') + '\n[/会话状态]')
  }
  const memories = (results || []).slice(0, limit)
  if (memories.length) {
    sections.push('[长期记忆召回]\n' + memories.map((item) => {
      return '- [' + String(item.type || 'fact') + '/' + String(item.scope || '?') + '] ' + String(item.content || '').slice(0, 240)
    }).join('\n') + '\n[/长期记忆]')
  }
  return sections.join('\n')
}

/* N07-HOST-SESSION-EVENTS: 0.1.5 Session 没有 `events` getter；snapshotEvents() 返回冻结数组。
   任何取不到事件的情况都退化为空数组，绝不抛错（本文件跑在
   agent/turn-stopping 钩子里，抛错会炸掉整个回合）。 */
function sessionEventList(session) {
  if (!session) return []
  try {
    if (typeof session.snapshotEvents === 'function') {
      const events = session.snapshotEvents()
      return Array.isArray(events) ? events : []
    }
    if (Array.isArray(session.events)) return session.events
  } catch (error) {
    return []
  }
  return []
}

function latestMemoryQuery(agent) {
  const parts = []
  const events = sessionEventList(agent && agent.session)
  for (let index = events.length - 1; index >= 0 && parts.length < 6; index--) {
    const event = events[index]
    const text = eventText(event).trim()
    if (text) parts.unshift(text.slice(0, 160))
  }
  return parts.join(' | ').slice(0, 700) || '当前会话目标、计划、决定、偏好和相关工作上下文'
}

function cadenceCompactionRange(agent, cadenceTurn, retainTurns = 3) {
  const turnBySeq = new Map()
  let currentTurn = 0
  for (const event of sessionEventList(agent && agent.session)) {
    if (event.type === 'turn/start') currentTurn = Number((event.data && event.data.turn) || currentTurn)
    turnBySeq.set(event.seq, currentTurn)
  }
  const surface = Array.from(agent.session.surface.nodes)
  const cutoff = cadenceTurn - retainTurns
  let endIndex = -1
  for (let index = 0; index < surface.length; index++) {
    if (Number(turnBySeq.get(surface[index]) || 0) > cutoff) break
    endIndex = index
  }
  if (endIndex < 0) return null
  return { start: surface[0], end: surface[endIndex] }
}

async function sessionEvents(ctx, sessionId) {
  let agent
  try {
    agent = ctx.get('agents')?.get(sessionId)
  } catch (error) {
    agent = undefined
  }
  if (agent && agent.session) return sessionEventList(agent.session)
  const persistence = ctx.get('sessionPersistence')
  if (!persistence || typeof persistence.open !== 'function') return null
  /* 0.1.5 只有 open/read/close（没有 inspect）：读句柄拿全量事件后必须关闭。 */
  let handle
  try {
    handle = await persistence.open(sessionId, 'read')
    const slice = await handle.read(0)
    return (slice && slice.events) || []
  } catch (error) {
    return null
  } finally {
    if (handle) {
      try {
        await handle.close()
      } catch (error) {}
    }
  }
}

// ── 上下文注入可见性（2026-10-18）────────────────────────────────────────
// 数据来源优先级：宿主进程内 agent.session.snapshotEvents() → sessionPersistence →
// 会话日志文件（v4/v3/v0，行 JSON + zstd）。第三种是退路，只在前两种拿不到时用。
//
// 注入文本不是从插件内存里读的：memoryCache 是 preset 插件（plugin-v3.js）模块内
// 私有变量，Host 插件与它不共享作用域。日志里的 runtime-context sections 是
// **实际下发给模型的文本**，比缓存更权威。

const INJECT_SECTION_NAME = 'deepmemory'
const INJECT_SEGMENT_MARKERS = [
  { key: 'card', label: '会话状态卡', marker: '[会话状态]' },
  { key: 'summary', label: '历史脉络摘要', marker: '[历史脉络摘要]' },
  { key: 'operations', label: '操作规则', marker: '[操作规则]' },
  { key: 'memories', label: '记忆条目', marker: '[长期记忆召回]' },
  { key: 'guide', label: '记忆使用指引', marker: '[记忆使用指引]' },
]
const TRUNCATION_MARKER_RE = /\[\^ 预算裁剪 (\d+) 字符 \^\]/

// 工具 → 来源插件行的内置映射。0.1.7/0.2.x 的 request/header.tools 只有
// name/description/parameters，没有注册者元数据，所以来源只能按 preset 插件行推断。
// 这里覆盖内置工具；第三方工具走 token 启发式（见 resolveToolSource）。
const TOOL_SOURCE_HINTS = {
  bash: { pkg: 'dsh-tool-bash', tokens: ['tool-bash', 'bash'] },
  pwsh: { pkg: 'dsh-tool-pwsh', tokens: ['tool-pwsh', 'pwsh'] },
  read: { pkg: 'dsh-tool-fs', tokens: ['tool-fs', 'fs-search'] },
  write: { pkg: 'dsh-tool-fs', tokens: ['tool-fs', 'fs-search'] },
  edit: { pkg: 'dsh-tool-fs', tokens: ['tool-fs', 'fs-search'] },
  read_image: { pkg: 'dsh-tool-fs', tokens: ['tool-fs', 'fs-search'] },
  glob: { pkg: 'dsh-tool-fs-search', tokens: ['fs-search', 'tool-fs'] },
  grep: { pkg: 'dsh-tool-fs-search', tokens: ['fs-search', 'tool-fs'] },
  create_goal: { pkg: 'dsh-tool-goal', tokens: ['tool-goal', 'goal'] },
  get_goal: { pkg: 'dsh-tool-goal', tokens: ['tool-goal', 'goal'] },
  update_goal: { pkg: 'dsh-tool-goal', tokens: ['tool-goal', 'goal'] },
  job_list: { pkg: 'dsh-tool-jobs', tokens: ['tool-jobs', 'jobs'] },
  job_output: { pkg: 'dsh-tool-jobs', tokens: ['tool-jobs', 'jobs'] },
  job_kill: { pkg: 'dsh-tool-jobs', tokens: ['tool-jobs', 'jobs'] },
  present: { pkg: 'dsh-tool-present', tokens: ['tool-present', 'present'] },
  ralph: { pkg: 'dsh-tool-ralph', tokens: ['tool-ralph', 'ralph'] },
  subagent: { pkg: 'dsh-tool-subagent', tokens: ['tool-subagent', 'subagent'], row: 'tool-subagent' },
  subagent_fork: { pkg: 'dsh-tool-subagent', tokens: ['tool-subagent', 'subagent'], row: 'tool-subagent-fork' },
  list_agents: { pkg: 'dsh-tool-subagent-control', tokens: ['tool-subagent-control', 'subagent-control'], row: 'tool-subagent-list-agents' },
  send_message: { pkg: 'dsh-tool-subagent-control', tokens: ['tool-subagent-control', 'subagent-control'], row: 'tool-subagent-control' },
  interrupt_agent: { pkg: 'dsh-tool-subagent-control', tokens: ['tool-subagent-control', 'subagent-control'], row: 'tool-subagent-control' },
  todo_write: { pkg: 'dsh-tool-todo', tokens: ['tool-todo', 'todo'] },
  ask_user_question: { pkg: 'dsh-tool-ask-user', tokens: ['tool-ask-user', 'ask-user'] },
  exit_plan_mode: { pkg: 'dsh-plan-mode', tokens: ['plan-mode'] },
  web_search: { pkg: 'dsh-tool-web', tokens: ['tool-web', 'web-search'] },
  workflow: { pkg: 'dsh-tool-workflow', tokens: ['tool-workflow', 'workflow'] },
  skill: { pkg: 'dsh-tool-skill', tokens: ['tool-skill', 'skill'] },
  memory_briefing: { pkg: 'dsh-deepmemory', tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
  memory_recall: { pkg: 'dsh-deepmemory', tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
  memory_save: { pkg: 'dsh-deepmemory', tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
  memory_source: { pkg: 'dsh-deepmemory', tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
  memory_call: { pkg: 'dsh-deepmemory', tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
  notify: { pkg: 'dsh-shell-notify', tokens: ['shell-notify'] },
  sidebar_open: { pkg: 'dsh-better-sidebar', tokens: ['better-sidebar'] },
  literature_add: { pkg: 'dsh-literature', tokens: ['literature'] },
  literature_attach: { pkg: 'dsh-literature', tokens: ['literature'] },
  literature_link_evidence: { pkg: 'dsh-literature', tokens: ['literature'] },
  literature_search: { pkg: 'dsh-literature', tokens: ['literature'] },
  literature_update: { pkg: 'dsh-literature', tokens: ['literature'] },
}

function asText(value) {
  if (typeof value === 'string') return value
  if (value === undefined || value === null) return ''
  return String(value)
}

function messageContentText(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((item) => item && item.type === 'text' && typeof item.text === 'string')
    .map((item) => item.text)
    .join('')
}

function findSessionHeaderEvent(events) {
  for (const event of events || []) if (event && event.type === 'session') return event
  return null
}

function lastEventOfType(events, type, predicate) {
  for (let index = (events || []).length - 1; index >= 0; index--) {
    const event = events[index]
    if (!event || event.type !== type) continue
    if (!predicate || predicate(event)) return event
  }
  return null
}

export function sessionMeta(events) {
  const header = findSessionHeaderEvent(events) || {}
  const titleEvent = lastEventOfType(events, 'session/title')
  const descriptor = lastEventOfType(events, 'subagent/descriptor')
  return {
    id: asText(header.id),
    version: header.version || null,
    createdAt: header.createdAt || null,
    cwd: asText(header.cwd),
    parentSession: asText(header.parentSession) || null,
    origin: asText(header.origin) || null,
    delegationDepth: typeof header.delegationDepth === 'number' ? header.delegationDepth : null,
    agentPreset: asText(header.agentPreset) || null,
    title: titleEvent && titleEvent.data ? asText(titleEvent.data.title) : '',
    descriptor: descriptor && descriptor.data ? descriptor.data : null,
  }
}

function extractRuntimeContext(events) {
  const event = lastEventOfType(events, 'user/message', (item) => {
    const source = item.data && item.data.source
    return source && source.kind === 'runtime-context'
  })
  if (!event) return null
  const source = (event.data && event.data.source) || {}
  const sections = (Array.isArray(source.sections) ? source.sections : []).map((section) => ({
    name: asText(section && section.name),
    text: asText(section && (section.text !== undefined ? section.text : section.content)),
  }))
  return {
    event,
    seq: event.seq,
    time: event.time || null,
    form: asText(source.form),
    sections,
    messageText: messageContentText(event.data && event.data.content),
  }
}

function splitInjectionSegments(text) {
  const source = asText(text)
  const found = INJECT_SEGMENT_MARKERS
    .map((spec) => ({ spec, index: source.indexOf(spec.marker) }))
    .filter((item) => item.index >= 0)
    .sort((a, b) => a.index - b.index)
  const segments = []
  if (found.length && found[0].index > 0) {
    segments.push({ key: 'preamble', label: '其它/前言', chars: found[0].index, text: source.slice(0, found[0].index) })
  }
  for (let index = 0; index < found.length; index++) {
    const start = found[index].index
    const end = index + 1 < found.length ? found[index + 1].index : source.length
    const segmentText = source.slice(start, end)
    const segment = {
      key: found[index].spec.key,
      label: found[index].spec.label,
      chars: segmentText.length,
      text: segmentText,
    }
    const truncation = segmentText.match(TRUNCATION_MARKER_RE)
    if (truncation) {
      segment.truncated = true
      segment.removed_chars = Number(truncation[1]) || 0
    }
    if (segment.key === 'memories') {
      segment.groups = []
      const groupRe = /^\[([^\]]+)\]\s*$/gm
      let match
      while ((match = groupRe.exec(segmentText))) {
        if (match[1] === '长期记忆召回') continue
        const rest = segmentText.slice(match.index + match[0].length)
        const next = rest.search(/^\[[^\]]+\]\s*$/m)
        const body = next >= 0 ? rest.slice(0, next) : rest
        segment.groups.push({
          name: match[1],
          items: (body.match(/^\s*-\s+/gm) || []).length,
        })
      }
    }
    segments.push(segment)
  }
  if (!segments.length) {
    segments.push({ key: 'all', label: '全部注入', chars: source.length, text: source })
  }
  return segments
}

function systemPromptHeuristicSegments(text) {
  const source = asText(text)
  const markers = [
    { label: '偏置约束（literature bias）', index: source.indexOf('\n\n[约束前提]') },
    { label: '工作区指令（AGENTS/仓库规则）', index: source.indexOf('\n\nInstructions from:') },
    { label: '工作区指令（AGENTS/仓库规则）', index: source.indexOf('\n\n# Workspace Instructions') },
  ].filter((item) => item.index >= 0).sort((a, b) => a.index - b.index)
  if (!markers.length) return [{ label: '系统提示', chars: source.length, text: source }]
  const segments = []
  if (markers[0].index > 0) {
    segments.push({ label: '基础指令', chars: markers[0].index, text: source.slice(0, markers[0].index) })
  }
  for (let index = 0; index < markers.length; index++) {
    const start = markers[index].index
    const end = index + 1 < markers.length ? markers[index + 1].index : source.length
    segments.push({ label: markers[index].label, chars: end - start, text: source.slice(start, end) })
  }
  return segments
}

function extractSystemPrompt(events, header) {
  const event = lastEventOfType(events, 'system/message', (item) => {
    const message = item.data && item.data.message
    return message && message.role === 'system'
  })
  let text = event ? messageContentText(event.data.message.content) : ''
  let source = event ? 'system/message' : ''
  let sections = null
  if (header && Array.isArray(header.sections) && header.sections.length) {
    sections = header.sections.map((section) => ({
      name: asText(section && section.name),
      chars: asText(section && (section.text !== undefined ? section.text : section.content)).length,
      text: asText(section && (section.text !== undefined ? section.text : section.content)),
    }))
    text = sections.map((section) => section.text).join('\n\n')
    source = 'request/header.sections'
  }
  return {
    present: text.length > 0,
    chars: text.length,
    text,
    source,
    seq: event ? event.seq : null,
    sections,
    segments: sections
      ? sections.map((section) => ({ label: section.name || '未命名段', chars: section.chars, text: section.text }))
      : systemPromptHeuristicSegments(text),
    note: 'deepmemory 走 systemPrompt.context(order=50) 注入到对话流尾部的 runtime-context，不修改 system prompt 前缀；因此系统提示在跨回合保持稳定，记忆变化不会打断前缀缓存。',
  }
}

function extractRequestHeader(events) {
  const event = lastEventOfType(events, 'request/header')
  if (!event) return null
  const data = event.data || {}
  const header = data.header || {}
  const tools = Array.isArray(header.tools) ? header.tools : (Array.isArray(data.tools) ? data.tools : [])
  return { event, data, header, tools, reason: asText(data.reason) }
}

function extractToolFilter(events) {
  const descriptor = lastEventOfType(events, 'subagent/descriptor')
  const filter = descriptor && descriptor.data && descriptor.data.toolFilter
  return {
    deny: Array.isArray(filter && filter.deny) ? filter.deny.map(String) : [],
    allow: Array.isArray(filter && filter.allow) ? filter.allow.map(String) : [],
    source: filter ? 'subagent/descriptor' : '',
    descriptor: descriptor ? descriptor.data : null,
  }
}

function parsePluginRows(text) {
  const rows = []
  const re = /^[ \t]*-[ \t]*id:[ \t]*([A-Za-z0-9_.\/-]+)[ \t]*\r?\n[ \t]*name:[ \t]*['"]?([^'"\r\n#]+?)['"]?[ \t]*$/gm
  let match
  while ((match = re.exec(asText(text)))) {
    rows.push({ id: match[1], name: match[2].trim() })
  }
  const seen = new Set()
  return rows.filter((row) => {
    const key = row.id + '|' + row.name
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}


function loadPresetOnlyRows(home, preset) {
  const safePreset = preset ? String(preset).replace(/[^A-Za-z0-9._-]/g, '') : ''
  if (!safePreset) return { rows: [], file: '' }
  const file = home + '/.agent-presets/' + safePreset + '/agent.cordis.yml'
  try {
    return { rows: parsePluginRows(fs.readFileSync(file, 'utf8')), file }
  } catch (error) {
    return { rows: [], file: '' }
  }
}

// 2026-10-18（方案 A）：deepmemory 从 preset 级迁到 profile(root) 级。
// 根级行对**所有** agent 可见，插件内部再按 agentPreset 白名单自闸门；所以这里
// "是否挂载" = preset 有行（旧形态） 或 (宿主面有行 且 本 preset 在记忆白名单)。
// 只按 preset 行判断会让所有记忆会话永远显示"未挂载"并把 effective 判成 false。
const MEMORY_PRESET_IDS = new Set(['harness-memory', 'harness-memory-task', 'harness-memory-daily'])

function findMemoryPluginRow(rows) {
  return (rows || []).find((row) => /_memory-plugin|deepmemory/i.test(row.name) || /deepmemory|memory-plane/i.test(row.id)) || null
}

function loadHostPlaneRows(home) {
  // 宿主面：profile 的 cordis.patch.yml + 机级 cordis.patch.yml。
  // 不读 preset 源 —— preset 源在 0.1.7+/0.2.x 已不参与运行时挂载。
  const files = [
    home + '/profiles/web/cordis.patch.yml',
    home + '/cordis.patch.yml',
  ]
  const rows = []
  const seen = []
  for (const file of files) {
    try {
      rows.push(...parsePluginRows(fs.readFileSync(file, 'utf8')))
      seen.push(file)
    } catch (error) {}
  }
  return { rows, files: seen }
}

export function loadPluginRows(home, preset) {
  const files = []
  const safePreset = preset ? String(preset).replace(/[^A-Za-z0-9._-]/g, '') : ''
  if (safePreset) files.push(home + '/.agent-presets/' + safePreset + '/agent.cordis.yml')
  files.push(home + '/profiles/web/cordis.patch.yml')
  const rows = []
  for (const file of files) {
    try { rows.push(...parsePluginRows(fs.readFileSync(file, 'utf8'))) } catch (error) {}
  }
  return { rows, files: files.filter((file) => { try { return fs.statSync(file).isFile() } catch (error) { return false } }) }
}

function resolveToolSource(toolName, rows) {
  const name = asText(toolName)
  const hint = TOOL_SOURCE_HINTS[name]
  const candidates = rows || []
  const findRow = (tokens, pkg) => {
    if (!tokens) return null
    let match = null
    for (const row of candidates) {
      const haystack = (row.id + ' ' + row.name).toLowerCase()
      if (tokens.some((token) => haystack.includes(String(token).toLowerCase()))) {
        match = row
        if (pkg && row.name.toLowerCase().includes(String(pkg).toLowerCase())) return row
      }
    }
    return match
  }
  if (hint) {
    let row = hint.row ? candidates.find((item) => item.id === hint.row || item.id.endsWith('/' + hint.row)) : null
    if (!row) row = findRow(hint.tokens, hint.pkg)
    if (row) {
      return {
        plugin: row.name,
        row_id: row.id,
        candidate_package: hint.pkg,
        confidence: row.name.toLowerCase().includes(String(hint.pkg).toLowerCase()) ? 'exact' : 'inferred',
      }
    }
    return { plugin: hint.pkg, row_id: hint.row || '', candidate_package: hint.pkg, confidence: 'mapped-no-row' }
  }
  const prefixHints = [
    { re: /^memory_/, tokens: ['deepmemory', 'memory-plugin', 'harness-memory'] },
    { re: /^kb_/, tokens: ['literature'] },
    { re: /^literature_/, tokens: ['literature'] },
    { re: /^reach_/, tokens: ['bridge-tools', 'bridge', 'reach'] },
    { re: /^(spawn_teammate|wait_agent)$/, tokens: ['agent-team', 'team'] },
    { re: /^team_task_/, tokens: ['agent-team', 'team'] },
    { re: /^bridge_/, tokens: ['bridge'] },
  ]
  for (const hintDef of prefixHints) {
    if (!hintDef.re.test(name)) continue
    const row = findRow(hintDef.tokens, '')
    if (row) return { plugin: row.name, row_id: row.id, candidate_package: '', confidence: 'inferred' }
  }
  const lower = name.toLowerCase()
  let row = candidates.find((item) => {
    const base = item.name.split('/').pop().replace(/\.js$/, '').replace(/^dsh-/, '').replace(/^tool-/, '')
    return base && (lower === base || lower.startsWith(base + '_'))
  })
  if (!row) {
    for (const candidate of candidates) {
      const base = candidate.name.split('/').pop().replace(/\.js$/, '').replace(/^dsh-/, '').replace(/^tool-/, '')
      if (base && base.length >= 4 && lower.includes(base)) { row = candidate; break }
    }
  }
  if (row) return { plugin: row.name, row_id: row.id, candidate_package: '', confidence: 'inferred' }
  return { plugin: '', row_id: '', candidate_package: '', confidence: 'unknown' }
}

function collectToolCalls(events, limit = 12) {
  const counts = new Map()
  const calls = []
  for (const event of events || []) {
    if (!event || event.type !== 'tool/call') continue
    const data = event.data || {}
    const name = asText(data.name) || 'unknown'
    counts.set(name, (counts.get(name) || 0) + 1)
    calls.push({
      seq: event.seq,
      time: event.time || null,
      turn: data.turn,
      step: data.step,
      call_id: asText(data.callId),
      name,
      arguments_preview: asText(typeof data.arguments === 'string' ? data.arguments : JSON.stringify(data.arguments || {})).slice(0, 160),
    })
  }
  return {
    total: calls.length,
    counts: Array.from(counts.entries()).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    recent: calls.slice(-limit).reverse(),
  }
}

function buildTurnInputMap(events, runtimeContext) {
  let startIndex = 0
  for (let index = (events || []).length - 1; index >= 0; index--) {
    if (events[index] && events[index].type === 'turn/start') { startIndex = index; break }
  }
  const components = []
  const systemEvent = lastEventOfType(events, 'system/message', (item) => {
    const message = item.data && item.data.message
    return message && message.role === 'system'
  })
  if (systemEvent) {
    components.push({
      kind: 'system',
      seq: systemEvent.seq,
      chars: messageContentText(systemEvent.data.message.content).length,
      label: '系统提示（system）',
    })
  }
  for (let index = startIndex; index < (events || []).length; index++) {
    const event = events[index]
    if (!event || event.type !== 'user/message') continue
    const source = (event.data && event.data.source) || {}
    const text = messageContentText(event.data && event.data.content)
    const isRuntime = source.kind === 'runtime-context'
    const hasInjection = isRuntime && Array.isArray(source.sections) && source.sections.some((section) => {
      const name = asText(section && section.name)
      return name === INJECT_SECTION_NAME && asText(section && (section.text !== undefined ? section.text : section.content)).length > 0
    })
    components.push({
      kind: asText(source.kind) || 'user',
      seq: event.seq,
      chars: text.length,
      label: source.kind === 'runtime-context'
        ? '运行时上下文' + (hasInjection ? '（含记忆注入）' : '')
        : (source.kind === 'agent-instructions' ? '工作区 / AGENTS 指令' : '用户消息'),
      contains_injection: hasInjection,
    })
  }
  const injectionIndex = components.findIndex((item) => item.contains_injection)
  return {
    turn_start_seq: startIndex > 0 ? events[startIndex].seq : null,
    components,
    injection_component_index: injectionIndex,
    injection_component: injectionIndex >= 0 ? components[injectionIndex] : null,
    note: '按会话事件顺序列出本回合进入模型输入的 system / user 消息；runtime-context 是独立 user 消息，注入段在其中。',
  }
}

export function inspectPluginBudget(home) {
  const file = home + '/.agent-presets/_memory-plugin/plugin-v3.js'
  let base = 2600
  let extra = 300
  let source = 'default'
  try {
    const text = fs.readFileSync(file, 'utf8')
    const baseMatch = text.match(/INJECT_BUDGET_CHARS\s*=\s*(\d+)/)
    if (baseMatch) { base = Number(baseMatch[1]); source = file }
    const extraMatch = text.match(/INJECT_BUDGET_CHARS\s*\+\s*(\d+)/)
    if (extraMatch) extra = Number(extraMatch[1])
  } catch (error) {}
  return { base_chars: base, extra_chars: extra, effective_chars: base + extra, source }
}

export function buildInjectionReport(events, options = {}) {
  const runtime = extractRuntimeContext(events)
  const headerInfo = extractRequestHeader(events)
  const system = extractSystemPrompt(events, headerInfo && headerInfo.header)
  const budget = options.budget || inspectPluginBudget(options.home || '/www/dsh/home')
  const meta = sessionMeta(events)
  const section = runtime ? (runtime.sections.find((item) => item.name === INJECT_SECTION_NAME) || null) : null
  const injectionText = section ? section.text : ''
  const segments = splitInjectionSegments(injectionText)
  const truncation = injectionText.match(TRUNCATION_MARKER_RE)
  let charOffset = -1
  if (runtime && injectionText) charOffset = runtime.messageText.indexOf(injectionText)
  const sectionIndex = runtime ? runtime.sections.findIndex((item) => item.name === INJECT_SECTION_NAME) : -1
  const inputMap = buildTurnInputMap(events, runtime)
  return {
    session: meta,
    source: options.source || { mode: 'unknown' },
    request: headerInfo ? {
      seq: headerInfo.event.seq,
      time: headerInfo.event.time || null,
      reason: headerInfo.reason,
      provider: asText(headerInfo.header.config && headerInfo.header.config.provider),
      model: asText(headerInfo.header.config && headerInfo.header.config.model),
      reasoning_effort: asText(headerInfo.header.config && headerInfo.header.config.reasoningEffort),
      max_tokens: headerInfo.header.config ? headerInfo.header.config.maxTokens : null,
      tool_count: headerInfo.tools.length,
    } : null,
    injection: {
      found: !!section,
      observed: !!section && injectionText.length > 0,
      event_seq: runtime ? runtime.seq : null,
      time: runtime ? runtime.time : null,
      form: runtime ? runtime.form : '',
      total_chars: injectionText.length,
      text: injectionText,
      segments,
      budget,
      truncated: !!truncation,
      removed_chars: truncation ? Number(truncation[1]) || 0 : 0,
      position: {
        runtime_context: runtime ? {
          event_seq: runtime.seq,
          section_index: sectionIndex,
          section_count: runtime.sections.length,
          section_name: INJECT_SECTION_NAME,
          section_chars: injectionText.length,
          char_offset_in_message: charOffset,
          runtime_context_chars: runtime.messageText.length,
          sections: runtime.sections.map((item, index) => ({
            index,
            name: item.name,
            chars: item.text.length,
            is_injection: item.name === INJECT_SECTION_NAME,
          })),
          previous_section: sectionIndex > 0 ? runtime.sections[sectionIndex - 1].name : null,
          next_section: sectionIndex >= 0 && sectionIndex + 1 < runtime.sections.length ? runtime.sections[sectionIndex + 1].name : null,
        } : null,
        input_map: inputMap,
        system_prompt: {
          present: system.present,
          chars: system.chars,
          source: system.source,
          seq: system.seq,
          segments: system.segments.map((item) => ({ label: item.label, chars: item.chars })),
          note: system.note,
        },
      },
    },
    system_prompt: {
      present: system.present,
      chars: system.chars,
      source: system.source,
      seq: system.seq,
      segments: system.segments,
      text: system.text,
    },
    cache_note: '注入文本取自会话事件中实际下发的 runtime-context（deepmemory 段），包含记忆使用指引；preset 插件的 memoryCache 是模块内私有变量，Host 侧不可读取。memory-server 的 injection-log 只保留最近一次（全局、脱敏、截断 6000 字符），不能替代按会话读取。',
  }
}

export function buildToolsReport(events, options = {}) {
  const meta = sessionMeta(events)
  const headerInfo = extractRequestHeader(events)
  const filter = extractToolFilter(events)
  const calls = collectToolCalls(events)
  const pluginRows = options.pluginRows || { rows: [], files: [] }
  const tools = headerInfo ? headerInfo.tools.map((tool) => {
    const name = asText(tool && tool.name)
    const description = asText(tool && tool.description)
    const parameters = tool && tool.parameters !== undefined ? JSON.stringify(tool.parameters) : ''
    return {
      name,
      source: resolveToolSource(name, pluginRows.rows),
      description_chars: description.length,
      parameters_chars: parameters.length,
      description_preview: description.slice(0, 120),
    }
  }) : []
  const names = tools.map((tool) => tool.name)
  const deniedPresent = filter.deny.filter((name) => names.includes(name))
  const deniedAbsent = filter.deny.filter((name) => !names.includes(name))
  const sources = []
  for (const tool of tools) {
    const key = tool.source.plugin + '|' + tool.source.row_id
    let entry = sources.find((item) => item.key === key)
    if (!entry) {
      entry = {
        key,
        plugin: tool.source.plugin,
        row_id: tool.source.row_id,
        confidence: tool.source.confidence,
        tools: [],
      }
      sources.push(entry)
    }
    entry.tools.push(tool.name)
  }
  sources.sort((a, b) => a.plugin.localeCompare(b.plugin) || a.row_id.localeCompare(b.row_id))
  return {
    session: meta,
    source: options.source || { mode: 'unknown' },
    request: headerInfo ? {
      seq: headerInfo.event.seq,
      time: headerInfo.event.time || null,
      reason: headerInfo.reason,
      provider: asText(headerInfo.header.config && headerInfo.header.config.provider),
      model: asText(headerInfo.header.config && headerInfo.header.config.model),
      reasoning_effort: asText(headerInfo.header.config && headerInfo.header.config.reasoningEffort),
      max_tokens: headerInfo.header.config ? headerInfo.header.config.maxTokens : null,
    } : null,
    tools: {
      total: tools.length,
      names,
      items: tools,
      sources,
    },
    filter: {
      deny: filter.deny,
      allow: filter.allow,
      source: filter.source,
      denied_present: deniedPresent,
      denied_absent: deniedAbsent,
      note: filter.source
        ? '子 agent 的 toolFilter 来自 subagent/descriptor；日志只记录被裁掉的名单，未出现的工具即未注册或已被裁。'
        : '主会话没有 subagent/descriptor.toolFilter；preset 里的 toolFilter 配置作用于它派生的子 agent，不作用于本会话。',
    },
    calls,
    preset_files: pluginRows.files,
    notes: [
      'request/header.tools 只有 name/description/parameters，没有注册者元数据；来源按 preset 插件行 + 内置映射推断，confidence 标明 exact/inferred/mapped-no-row/unknown。',
      '工具数量是本回合实际下发给模型的工具数，已含 preset 的插件裁剪结果。',
    ],
  }
}

// 只解第一帧（v4/v3 的 frame0 恰好是那一行 session header）——避免为拿 preset 解压整份日志。
export function readSessionLogHeader(file) {
  if (!file) return null
  try {
    if (!zlib || typeof zlib.zstdDecompressSync !== 'function') return null
    const input = fs.readFileSync(file)
    const end = zstdFrameEnd(input, 0)
    const raw = zlib.zstdDecompressSync(input.subarray(0, end))
    for (const line of raw.toString('utf8').split(/\r?\n/)) {
      if (!line.trim()) continue
      try {
        const event = JSON.parse(line)
        if (event && event.type === 'session') return event
      } catch (error) {}
    }
  } catch (error) {}
  return null
}

export function findSessionLogFile(home, sessionId) {
  const sid = asText(sessionId).trim()
  if (!sid) return null
  const wanted = new Set([sid, 'session-' + sid, sid.replace(/^session-/, '')])
  const root = home + '/sessions'
  let projects = []
  try { projects = fs.readdirSync(root) } catch (error) { return null }
  for (const project of projects) {
    let dirs = []
    try { dirs = fs.readdirSync(root + '/' + project) } catch (error) { continue }
    for (const dir of dirs) {
      if (!wanted.has(dir)) continue
      const folder = root + '/' + project + '/' + dir
      let files = []
      try { files = fs.readdirSync(folder) } catch (error) { continue }
      const logs = files
        .filter((file) => /^session\.v\d+\.jsonl\.zstd$/.test(file))
        .sort()
        .reverse()
      if (logs.length) return folder + '/' + logs[0]
    }
  }
  return null
}

// v4/v3 会话日志是「一帧一帧追加」的 zstd 文件：Node 的 zstdDecompressSync 只解
// 第一帧，会静默丢掉后续事件。这里按 zstd 帧格式切分后逐帧解压（含 skippable frame）。
function zstdFrameEnd(buffer, start) {
  const magic = buffer.readUInt32LE(start)
  if ((magic & 0xFFFFFFF0) === 0x184D2A50) {
    const size = buffer.readUInt32LE(start + 4)
    return start + 8 + size
  }
  if (magic !== 0xFD2FB528) throw new Error('zstd frame magic mismatch at ' + start)
  let offset = start + 4
  const descriptor = buffer[offset++]
  const fcsFlag = (descriptor >> 6) & 3
  const singleSegment = (descriptor >> 5) & 1
  const checksum = (descriptor >> 2) & 1
  const dictionaryFlag = descriptor & 3
  if (!singleSegment) offset += 1
  offset += [0, 1, 2, 4][dictionaryFlag]
  offset += fcsFlag === 0 ? (singleSegment ? 1 : 0) : (1 << fcsFlag)
  for (;;) {
    if (offset + 3 > buffer.length) throw new Error('zstd frame truncated (header)')
    const blockHeader = buffer[offset] | (buffer[offset + 1] << 8) | (buffer[offset + 2] << 16)
    offset += 3
    const last = blockHeader & 1
    const type = (blockHeader >> 1) & 3
    const size = blockHeader >>> 3
    if (type === 1) offset += 1
    else offset += size
    if (offset > buffer.length) throw new Error('zstd frame truncated (block)')
    if (last) break
  }
  if (checksum) offset += 4
  if (offset > buffer.length) throw new Error('zstd frame truncated (checksum)')
  return offset
}

function decompressZstdFrames(input) {
  const parts = []
  let offset = 0
  while (offset < input.length) {
    if (input.length - offset < 4) break
    let end
    try {
      end = zstdFrameEnd(input, offset)
      parts.push(zlib.zstdDecompressSync(input.subarray(offset, end)))
    } catch (error) {
      // 文件可能正在追加（尾部半帧）：保留已解出的帧，不退回“只解第一帧”。
      if (parts.length) break
      throw error
    }
    offset = end
  }
  if (!parts.length) return Buffer.alloc(0)
  return Buffer.concat(parts)
}

export function readSessionLogEvents(file) {
  if (!file) return null
  if (!zlib || typeof zlib.zstdDecompressSync !== 'function') {
    throw new Error('当前 Node 运行时不支持 zstd 解压（需要 Node >= 22.15），无法读取会话日志退路')
  }
  const input = fs.readFileSync(file)
  if (input.length > 64 * 1024 * 1024) throw new Error('会话日志压缩体积超过 64MB，拒绝解压')
  let raw
  try {
    raw = decompressZstdFrames(input)
  } catch (error) {
    // 帧解析失败时退回单帧解压（至少拿到开头事件，而不是整份失败）
    raw = zlib.zstdDecompressSync(input)
  }
  if (raw.length > 256 * 1024 * 1024) throw new Error('会话日志解压后超过 256MB，拒绝解析')
  const events = []
  for (const line of raw.toString('utf8').split(/\r?\n/)) {
    if (!line.trim()) continue
    try {
      const event = JSON.parse(line)
      if (event && typeof event.type === 'string') events.push(event)
    } catch (error) {}
  }
  return events
}

async function initializeStateCard(ctx, sessionId, kind) {
  const existing = await memoryRequest('GET', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sessionId)).catch((error) => {
    if (String(error.message).includes('state card not found')) return { card: null }
    throw error
  })
  if (existing.card) return { status: 'exists', card: existing.card }
  const events = await sessionEvents(ctx, sessionId)
  if (!events) throw new Error('session is not available')
  const messages = []
  for (const event of events) {
    const text = eventText(event).trim()
    if (!text) continue
    messages.push({ role: event.type === 'user/message' ? '用户' : '助手', text: text.slice(0, 1600) })
  }
  const recent = messages.slice(-30)
  if (!recent.length) return { status: 'no_messages' }
  const llm = ctx.get('llm')
  if (!llm) throw new Error('llm service unavailable')
  const dialog = recent.map((item) => item.role + ': ' + item.text).join('\n')
  const card = await extractSessionCard(llm, dialog, null)
  if (!card) return { status: 'no_state' }
  const result = await memoryRequest('PUT', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sessionId), {
    expected_version: 0,
    payload: {
      goal: String(card.goal || ''),
      current_plan: String(card.current_plan || ''),
      key_decisions: Array.isArray(card.key_decisions) ? card.key_decisions.slice(0, 4) : [],
      in_progress: Array.isArray(card.in_progress) ? card.in_progress.slice(0, 4) : [],
      next_steps: Array.isArray(card.next_steps) ? card.next_steps.slice(0, 4) : [],
    },
    actor: 'user',
    reason: 'manual session card initialization',
  })
  return { status: 'created', card: result.card }
}

async function performConsolidation(ctx, options = {}) {
  if (consolidationRun) {
    if (!options.force || consolidationRunForce) return consolidationRun
    if (!queuedForceRun) {
      queuedForceRun = consolidationRun
        .catch(() => undefined)
        .then(() => performConsolidation(ctx, options))
        .finally(() => { queuedForceRun = null })
    }
    return queuedForceRun
  }
  consolidationRunForce = options.force === true
  consolidationRun = (async () => {
    const configResponse = await memoryRequest('GET', '/v1/config')
    const config = configResponse.config || {}
    const force = options.force === true
    const enabled = boolValue(config['memory_consolidation.enabled'], false)
    const trigger = String(config['memory_consolidation.trigger'] || 'daily')
    if (!force && (!enabled || trigger !== 'daily')) {
      return { status: 'disabled', enabled, trigger }
    }
    const lastResponse = await memoryRequest('GET', '/v1/settings/last_consolidation_at')
    const last = Number(lastResponse.value || 0)
    const now = Date.now() / 1000
    if (!force && last > 0 && now - last < 86400) {
      return { status: 'skipped', next_run_in_seconds: Math.ceil(86400 - (now - last)) }
    }
    const similarity = Number.isFinite(Number(options.similarity))
      ? Number(options.similarity)
      : Number(config['memory_consolidation.semantic_similarity_threshold'] || 0.92)
    const limitGroups = Number.isFinite(Number(options.limitGroups))
      ? Number(options.limitGroups)
      : Number(config['memory_consolidation.max_groups_per_run'] || 5)
    const candidates = await memoryRequest('POST', '/v1/maintenance/consolidate/candidates', {
      similarity,
      limit_groups: limitGroups,
    })
    if (candidates.error) throw new Error(String(candidates.error))
    if (!candidates.groups) {
      await memoryRequest('POST', '/v1/settings/set', { key: 'last_consolidation_at', value: now })
      return {
        status: 'no_candidates', merged: 0, groups: 0, threshold: candidates.threshold,
        min_age_days: Number(config['memory_consolidation.min_age_days'] || 1),
        max_importance: Number(config['memory_consolidation.max_importance'] || 0.5),
      }
    }
    const useLlm = boolValue(config['memory_consolidation.llm_summarize'], false)
    const llm = ctx.get('llm')
    let route = null
    const groups = []
    if (useLlm && llm) {
      const configuredProvider = String(config['memory_consolidation.llm_provider'] || '').trim()
      const configuredModel = String(config['memory_consolidation.llm_model'] || '').trim()
      route = await resolveModelRoute(llm, { provider: configuredProvider, model: configuredModel })
      for (const group of candidates.candidates || []) {
        groups.push({
          primary_id: group.primary_id,
          archived_ids: group.archived_ids || [],
          canonical_summary: await summarizeGroup(llm, route, group),
        })
      }
    } else {
      for (const group of candidates.candidates || []) {
        groups.push({ primary_id: group.primary_id, archived_ids: group.archived_ids || [] })
      }
    }
    const applied = await memoryRequest('POST', '/v1/maintenance/consolidate/apply', { groups })
    await memoryRequest('POST', '/v1/settings/set', { key: 'last_consolidation_at', value: now })
    return {
      status: 'completed', merged: applied.merged || 0, groups: applied.groups || groups.length,
      threshold: candidates.threshold, mode: useLlm && llm ? 'llm' : 'deterministic', route,
    }
  })().finally(() => {
    consolidationRun = null
    consolidationRunForce = false
  })
  return consolidationRun
}

function readRequestJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > 64 * 1024) {
        reject(new Error('request body too large'))
        req.destroy()
      } else chunks.push(chunk)
    })
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}) }
      catch (error) { reject(error) }
    })
    req.on('error', reject)
  })
}

function writeJson(res, status, payload) {
  if (res.headersSent) {
    try { res.end() } catch (error) {}
    return
  }
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  res.end(JSON.stringify(payload))
}

function queryParam(req, name) {
  try {
    return new URL(req.url || '', 'http://localhost').searchParams.get(name) || ''
  } catch (error) {
    return ''
  }
}

function pickFirst(object, keys) {
  if (!object) return undefined
  for (const key of keys) {
    if (object[key] !== undefined && object[key] !== null) return object[key]
  }
  return undefined
}

function boolSetting(value, fallback) {
  if (value === undefined || value === null) return fallback
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['false', '0', 'no', 'off', ''].includes(normalized)) return false
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true
  }
  return Boolean(value)
}

export function apply(ctx) {
  // 0.1.7 起设置页的表单从 Loader entry 的 Config schema 派生（按 profile entry
  // id 索引），插件不再自行 register 命名空间——ctx.settings 已无 register()。
  // deepmemory 的实际配置存在 memory-server，配置卡片由浏览器半通过
  // `plugins.item` 槽位自绘注册，Host 侧无需任何注册动作。

  const cardBuckets = new Map()
  const cardRuns = new Set()
  const cadenceRuns = new Map()
  const hostMemoryCache = new Map()
  const hostMemoryInitialized = new Set()
  const hostMemoryRefreshes = new Map()

  // N13：与 preset 的 resolveWorkspace 同一套解析——按 sessionId 在
  // storages/workspace.json 的 tables.workspaces[*].sessionIds 中找真实归属。
  // 原实现直接拿 config.workspace 或兜底字符串，会让备用召回在其他工作区里检索为空
  // 或串到别的工作区。解析失败返回空串，由调用方决定回退。
  const hostWsCache = new Map()
  function resolveHostWorkspace(sessionId) {
    const sid = String(sessionId || '').trim()
    if (!sid) return ''
    if (hostWsCache.has(sid)) return hostWsCache.get(sid)
    let found = ''
    try {
      const home = String(process.env.DSH_HOME || '/www/dsh/home')
      const d = JSON.parse(fs.readFileSync(home + '/storages/workspace.json', 'utf-8'))
      const wss = (d.tables && d.tables.workspaces) || {}
      for (const k of Object.keys(wss)) {
        if ((wss[k].sessionIds || []).includes(sid)) { found = String(k); break }
      }
    } catch (e) { /* 解析失败 → 空串，调用方回退 */ }
    hostWsCache.set(sid, found)
    return found
  }

  async function refreshHostMemory(agent, config) {
    const sessionId = String(agent.session.id)
    if (hostMemoryRefreshes.has(sessionId)) return hostMemoryRefreshes.get(sessionId)
    const refresh = (async () => {
      const limit = Math.max(1, Math.min(20, Number(config['context_automation.memory_completion_k'] || 5)))
      // N13：优先按会话真实归属解析（与 preset 一致）；解析为空才回退配置/兜底字符串
      const workspaceId = resolveHostWorkspace(sessionId) || String(config.workspace || 'deepseek-harness')
      const search = await memoryRequest('POST', '/v1/memories/search', {
        query: latestMemoryQuery(agent),
        k: limit,
        session_id: sessionId,
        workspace_id: workspaceId,
      })
      const kind = stateCardKind(agent)
      const card = await memoryRequest('GET', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sessionId)).catch((error) => {
        if (String(error.message).includes('state card not found')) return { card: null }
        throw error
      })
      const text = memoryCompletionText(card.card, search.results || [], limit)
      if ((hostMemoryCache.get(sessionId) || '') !== text) hostMemoryCache.set(sessionId, text)
      hostMemoryInitialized.add(sessionId)
      return text
    })().finally(() => hostMemoryRefreshes.delete(sessionId))
    hostMemoryRefreshes.set(sessionId, refresh)
    return refresh
  }

  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt) {
    ctx.effect(() => systemPrompt.section({
      name: 'deepmemory:host-memory-completion',
      order: 50,
      text: (context) => {
        const agent = context && context.agent
        if (!agent || presetHasMemoryCompletion(agent)) return ''
        return hostMemoryCache.get(String(agent.session.id)) || ''
      },
    }))
    ctx.on('system-prompt/assemble', async (assembly, context, next) => {
      const assembled = await next()
      const agent = context && context.agent
      if (!agent || presetHasMemoryCompletion(agent)) return assembled
      const sessionId = String(agent.session.id)
      try {
        const response = await memoryRequest('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId))
        const config = response.config || {}
        if (!boolValue(config['context_automation.enabled'], true)) {
          hostMemoryCache.delete(sessionId)
          hostMemoryInitialized.delete(sessionId)
        } else if (!hostMemoryInitialized.has(sessionId)) {
          await refreshHostMemory(agent, config)
        }
      } catch (error) {
        ctx.logger.warn('memory completion failed for session %s: %s', sessionId, String((error && error.message) || error))
      }
      const text = hostMemoryCache.get(sessionId) || ''
      return {
        ...assembled,
        sections: assembled.sections.map((section) => section.name === 'deepmemory:host-memory-completion' ? { ...section, text } : section),
      }
    })
  }

  ctx.on('session/event', (session, event) => {
    const text = eventText(event).trim()
    if (!text || !session || !session.id) return
    const sessionId = String(session.id)
    let bucket = cardBuckets.get(sessionId)
    if (!bucket) {
      bucket = []
      cardBuckets.set(sessionId, bucket)
    }
    bucket.push({ role: event.type === 'user/message' ? '用户' : '助手', text: text.slice(0, 1200) })
    if (bucket.length > 40) bucket.shift()
  })

  ctx.on('agent/turn-stopping', async ({ agent, turn, signal }) => {
    if (!agent) return
    const sessionId = String(agent.session.id)
    if (cardRuns.has(sessionId)) return
    const bucket = cardBuckets.get(sessionId) || []
    let configResponse
    try {
      configResponse = await memoryRequest('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId))
    } catch (error) {
      ctx.logger.warn('automatic state card config failed for session %s: %s', sessionId, String((error && error.message) || error))
      return
    }
    const config = configResponse.config || {}
    const syncTurns = Math.max(1, Math.min(100, Number(config['state_card.sync_turns'] || 5)))
    const contextAutomation = boolValue(config['context_automation.enabled'], true)
    let cadence = cadenceRuns.get(sessionId)
    if (!cadence) {
      if (!turn || turn % syncTurns !== 0) return
      cadence = { turn, cardDone: false }
      cadenceRuns.set(sessionId, cadence)
    }
    const enabled = boolValue(config['state_card.auto_generate'], true)
    const consumed = bucket.length ? bucket.slice() : []
    cardRuns.add(sessionId)
    try {
      if (!cadence.cardDone) {
        if (enabled) {
          const llm = ctx.get('llm')
          if (!llm) throw new Error('llm service unavailable')
          const kind = stateCardKind(agent)
          const existing = await memoryRequest('GET', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sessionId)).catch((error) => {
            if (String(error.message).includes('state card not found')) return { card: null }
            throw error
          })
          let input = consumed
          if (!input.length) {
            input = []
            for (const event of sessionEventList(agent && agent.session)) {
              const text = eventText(event).trim()
              if (text) input.push({ role: event.type === 'user/message' ? '用户' : '助手', text: text.slice(0, 1200) })
            }
            input = input.slice(-20)
          }
          const dialog = input.map((item) => item.role + ': ' + item.text).join('\n')
          const card = dialog ? await extractSessionCard(llm, dialog, existing.card && existing.card.payload) : null
          if (card) {
            const result = await memoryRequest('PUT', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sessionId), {
              expected_version: existing.card ? Number(existing.card.version || 0) : 0,
              payload: {
                goal: String(card.goal || ''),
                current_plan: String(card.current_plan || ''),
                key_decisions: Array.isArray(card.key_decisions) ? card.key_decisions.slice(0, 4) : [],
                in_progress: Array.isArray(card.in_progress) ? card.in_progress.slice(0, 4) : [],
                next_steps: Array.isArray(card.next_steps) ? card.next_steps.slice(0, 4) : [],
              },
              actor: 'main_agent',
              reason: 'five-turn state card and compaction cadence',
            })
            ctx.logger.info('five-turn state card updated session %s v%s', sessionId, result.card && result.card.version)
          } else {
            ctx.logger.info('five-turn state card: no update for session %s', sessionId)
          }
        }
        if (consumed.length) cardBuckets.set(sessionId, (cardBuckets.get(sessionId) || []).slice(consumed.length))
        cadence.cardDone = true
      }
      const range = cadenceCompactionRange(agent, cadence.turn)
      if (contextAutomation) {
        const compaction = agent.ctx.get('compaction')
        if (range && compaction) {
          const result = await compaction.compactRegion(range.start, range.end, agent, signal)
          ctx.logger.info('five-turn compaction completed session %s turn %s id %s', sessionId, cadence.turn, result && result.compactionId)
        } else if (range && !compaction) {
          ctx.logger.warn('five-turn compaction unavailable for session %s', sessionId)
        } else {
          ctx.logger.info('five-turn compaction had no eligible history for session %s turn %s', sessionId, cadence.turn)
        }
        if (!presetHasMemoryCompletion(agent)) hostMemoryInitialized.delete(sessionId)
      } else {
        ctx.logger.info('five-turn compaction and memory completion disabled for session %s', sessionId)
      }
      cadenceRuns.delete(sessionId)
    } catch (error) {
      ctx.logger.warn('five-turn state card/compaction failed for session %s: %s', sessionId, String((error && error.message) || error))
    } finally {
      cardRuns.delete(sessionId)
    }
  })

  // ── 上下文注入管理 API（2026-10-18）────────────────────────────────────
  const homeDir = process.env.DSH_HOME || '/www/dsh/home'

  async function loadSessionEventsDetailed(sessionId) {
    const sid = String(sessionId || '').trim()
    if (!sid) throw new Error('session_id 必填')
    const variants = Array.from(new Set([sid, 'session-' + sid, sid.replace(/^session-/, '')]))
    let agents = null
    try { agents = ctx.get('agents') } catch (error) { agents = null }
    if (agents && typeof agents.get === 'function') {
      for (const id of variants) {
        let agent
        try { agent = agents.get(id) } catch (error) { agent = null }
        if (!agent || !agent.session) continue
        const events = sessionEventList(agent.session)
        if (events && events.length) {
          return {
            events,
            source: {
              mode: 'live',
              detail: '宿主进程内 agent.session.snapshotEvents()（实时，不依赖磁盘落盘）',
              event_count: events.length,
            },
          }
        }
      }
    }
    const persistence = ctx.get('sessionPersistence')
    let persistError = ''
    if (persistence && typeof persistence.open === 'function') {
      for (const id of variants) {
        let handle
        try {
          handle = await persistence.open(id, 'read')
          const slice = await handle.read(0)
          const events = (slice && slice.events) || []
          if (events.length) {
            return {
              events,
              source: {
                mode: 'persistence',
                detail: 'sessionPersistence.open/read（官方解析器，已处理 v0/v3/v4 迁移）',
                event_count: events.length,
                session_key: id,
              },
            }
          }
        } catch (error) {
          persistError = String((error && error.message) || error)
        } finally {
          if (handle) {
            try { await handle.close() } catch (error) {}
          }
        }
      }
    }
    let logError = ''
    for (const id of variants) {
      const file = findSessionLogFile(homeDir, id)
      if (!file) continue
      try {
        const events = readSessionLogEvents(file)
        if (events && events.length) {
          return {
            events,
            source: {
              mode: 'log',
              detail: 'zstd 会话日志退路（行 JSON；v4/v3/v0 均按行解析）',
              event_count: events.length,
              path: file,
            },
          }
        }
      } catch (error) {
        logError = String((error && error.message) || error)
      }
    }
    throw new Error('无法读取会话事件：' + (persistError || logError || 'live / persistence / log 均不可用'))
  }

  function listStoredSessions(limit) {
    const titles = {}
    try {
      const cache = JSON.parse(fs.readFileSync(homeDir + '/storages/session_projcache.json', 'utf8'))
      const table = (cache.tables && cache.tables.sessions) || {}
      for (const id of Object.keys(table)) {
        const title = table[id] && table[id].rows && table[id].rows.title && table[id].rows.title.val
        if (title) titles[String(id)] = String(title)
      }
    } catch (error) {}
    const root = homeDir + '/sessions'
    const out = []
    let projects = []
    try { projects = fs.readdirSync(root) } catch (error) { return out }
    for (const project of projects) {
      let dirs = []
      try { dirs = fs.readdirSync(root + '/' + project) } catch (error) { continue }
      for (const dir of dirs) {
        const folder = root + '/' + project + '/' + dir
        let files = []
        try { files = fs.readdirSync(folder) } catch (error) { continue }
        const logs = files
          .filter((file) => /^session\.v\d+\.jsonl\.zstd$/.test(file))
          .sort()
          .reverse()
        if (!logs.length) continue
        const file = folder + '/' + logs[0]
        let stat
        try { stat = fs.statSync(file) } catch (error) { continue }
        const bare = dir.replace(/^session-/, '')
        out.push({
          session_id: dir,
          canonical_id: bare,
          title: titles[dir] || titles[bare] || '',
          mtime: stat.mtimeMs,
          size: stat.size,
          log_format: logs[0].replace(/^session\.(v\d+)\.jsonl\.zstd$/, '$1'),
          project,
        })
      }
    }
    out.sort((a, b) => b.mtime - a.mtime)
    return out.slice(0, Math.max(1, Math.min(100, Number(limit) || 30)))
  }

  async function readInjectionFlags(sessionId, events) {
    const meta = sessionMeta(events)
    // live/persistence 事件流里**不含** v4 的 frame-0 header 行，所以 sessionMeta 的
    // agentPreset/origin/parentSession 会是 null。用活着的 agent.session.header 补齐——
    // 否则 'agentPreset 为空' 会被当成"白名单通过"，让 standard 会话也显示已挂载。
    try {
      const agents = ctx.get('agents')
      const bare = String(sessionId).replace(/^session-/, '')
      const variants = [sessionId, 'session-' + bare, bare]
      let live = null
      for (const id of variants) {
        const agent = agents && typeof agents.get === 'function' ? agents.get(id) : null
        if (agent && agent.session && agent.session.header) { live = agent.session.header; break }
      }
      if (!live) {
        // 会话没有活着的 agent 时（刚重启/未 resume），退回读日志 frame0 的 header
        try { live = readSessionLogHeader(findSessionLogFile(homeDir, sessionId)) } catch (error) {}
      }
      if (live) {
        if (!meta.agentPreset && live.agentPreset) meta.agentPreset = String(live.agentPreset)
        if (!meta.origin && live.origin) meta.origin = String(live.origin)
        if (!meta.parentSession && live.parentSession) meta.parentSession = String(live.parentSession)
        if (meta.delegationDepth === null && typeof live.delegationDepth === 'number') meta.delegationDepth = live.delegationDepth
      }
    } catch (error) {}
    const headerInfo = extractRequestHeader(events)
    let globalCfg = {}
    let sessionCfg = {}
    let globalError = ''
    let sessionError = ''
    try {
      const result = await memoryRequest('GET', '/v1/config')
      globalCfg = (result && result.config) || {}
    } catch (error) {
      globalError = String((error && error.message) || error)
    }
    try {
      const result = await memoryRequest('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId))
      sessionCfg = (result && result.config) || {}
    } catch (error) {
      sessionError = String((error && error.message) || error)
    }
    let sessionToggle = null
    try {
      sessionToggle = await memoryRequest('GET', '/v1/settings/session_enabled:' + encodeURIComponent(sessionId))
    } catch (error) {}
    const sessionEnabled = sessionToggle && typeof sessionToggle.value === 'boolean' ? sessionToggle.value : true
    const sessionEnabledSource = sessionToggle && typeof sessionToggle.value === 'boolean'
      ? 'memory-server setting session_enabled:' + sessionId
      : '缺省 true（未设置会话覆盖）'
    const globalInject = boolSetting(pickFirst(globalCfg, ['injection.inject_enabled', 'inject_enabled']), true)
    const globalCard = boolSetting(pickFirst(globalCfg, ['injection.inject_card', 'inject_card']), true)
    const globalSummary = boolSetting(pickFirst(globalCfg, ['compression.inject_summary', 'inject_summary']), true)
    const injectOrder = Number(pickFirst(globalCfg, ['injection.inject_order', 'inject_order'])) || 50
    const recallK = Number(pickFirst(globalCfg, ['recall_engine.top_k', 'recall_k'])) || 5
    const automationRaw = pickFirst(sessionCfg, ['context_automation.enabled'])
    const automation = automationRaw === undefined ? true : boolSetting(automationRaw, true)
    const isChild = meta.origin === 'subagent' || (typeof meta.delegationDepth === 'number' && meta.delegationDepth > 0)
    let childMemory = null
    let parentEnabled = null
    if (isChild && meta.parentSession) {
      try {
        const parentToggle = await memoryRequest('GET', '/v1/settings/session_enabled:' + encodeURIComponent(meta.parentSession))
        parentEnabled = parentToggle && typeof parentToggle.value === 'boolean' ? parentToggle.value : true
      } catch (error) { parentEnabled = null }
      try {
        const childToggle = await memoryRequest('GET', '/v1/settings/child_memory:' + encodeURIComponent(meta.parentSession))
        childMemory = childToggle && childToggle.value === true
      } catch (error) { childMemory = null }
    }
    const provider = asText(headerInfo && headerInfo.header && headerInfo.header.config && headerInfo.header.config.provider) || asText(meta.descriptor && meta.descriptor.agentProvider)
    const model = asText(headerInfo && headerInfo.header && headerInfo.header.config && headerInfo.header.config.model) || asText(meta.descriptor && meta.descriptor.agentModel)
    const astra = provider === 'uuapi-astra' || model === 'gpt-6-astra'
    const presetInfo = loadPresetOnlyRows(homeDir, meta.agentPreset)
    const presetPluginRow = findMemoryPluginRow(presetInfo.rows)
    const hostPlane = loadHostPlaneRows(homeDir)
    const hostPluginRow = findMemoryPluginRow(hostPlane.rows)
    const presetWhitelisted = !meta.agentPreset || MEMORY_PRESET_IDS.has(String(meta.agentPreset))
    const hostPlaneActive = !!hostPluginRow && presetWhitelisted
    const memoryPluginRow = presetPluginRow || (hostPlaneActive ? hostPluginRow : null)
    const memoryPlane = presetPluginRow ? 'preset' : (hostPlaneActive ? 'profile' : 'none')
    let effective = true
    const blockers = []
    if (!memoryPluginRow && meta.agentPreset) {
      effective = false
      blockers.push(hostPluginRow
        ? 'preset 不在 deepmemory 白名单（' + meta.agentPreset + '）'
        : 'preset 未挂载 deepmemory 插件（' + meta.agentPreset + '）')
    }
    if (!globalInject) { effective = false; blockers.push('全局 inject_enabled=false') }
    if (!automation) { effective = false; blockers.push('会话 context_automation.enabled=false') }
    if (astra) { effective = false; blockers.push('astra（审核/规划子 agent）一律不注入') }
    if (isChild) {
      if (parentEnabled === false) { effective = false; blockers.push('父会话 session_enabled=false') }
      if (childMemory !== true) { effective = false; blockers.push('父会话 child_memory 未开启（缺省 false）') }
    } else if (!sessionEnabled) {
      effective = false
      blockers.push('会话 session_enabled=false')
    }
    const reason = [
      '全局 inject_enabled=' + globalInject,
      '会话 session_enabled=' + sessionEnabled + '（' + sessionEnabledSource + '）',
      'context_automation.enabled=' + automation,
      isChild ? '子 agent：parent=' + (meta.parentSession || '?') + '，parent_enabled=' + String(parentEnabled) + '，child_memory=' + String(childMemory) : '主会话',
      astra ? 'astra provider/model 命中排除规则' : '非 astra',
      memoryPluginRow
        ? (memoryPlane === 'profile'
            ? 'profile(root) 级已挂载 deepmemory 插件（' + memoryPluginRow.id + '，preset 在白名单）'
            : 'preset 已挂载 deepmemory 插件（' + memoryPluginRow.id + '）')
        : (hostPluginRow ? 'profile 级有 deepmemory 行但本 preset 不在白名单（' + meta.agentPreset + '）' : 'preset 未挂载 deepmemory 插件'),
      'effective=' + effective,
    ].join('；')
    return {
      global: {
        inject_enabled: globalInject,
        inject_card: globalCard,
        inject_summary: globalSummary,
        recall_k: recallK,
        inject_order: injectOrder,
        config_error: globalError || null,
      },
      session: {
        enabled: sessionEnabled,
        source: sessionEnabledSource,
        config_error: sessionError || null,
      },
      child: {
        is_child: isChild,
        parent_session_id: meta.parentSession || null,
        parent_enabled: parentEnabled,
        child_memory: childMemory,
      },
      automation: { enabled: automation },
      astra: { excluded: astra, provider, model },
      preset: {
        agent_preset: meta.agentPreset || null,
        has_memory_plugin: !!memoryPluginRow,
        memory_plugin_row: memoryPluginRow ? memoryPluginRow.id : null,
        memory_plane: memoryPlane,
        preset_row: presetPluginRow ? presetPluginRow.id : null,
        host_plane_row: hostPluginRow ? hostPluginRow.id : null,
        preset_whitelisted: presetWhitelisted,
        file: presetInfo.file || null,
      },
      effective,
      blockers,
      reason,
    }
  }

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/context/sessions',
    handler: (req, res) => {
      if (req.method !== 'GET') return writeJson(res, 405, { error: 'method not allowed' })
      try {
        const limit = Number(queryParam(req, 'limit')) || 30
        writeJson(res, 200, { sessions: listStoredSessions(limit), home: homeDir })
      } catch (error) {
        writeJson(res, 500, { error: String((error && error.message) || error) })
      }
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/context/injection',
    handler: async (req, res) => {
      if (req.method !== 'GET') return writeJson(res, 405, { error: 'method not allowed' })
      try {
        const sessionId = queryParam(req, 'session_id') || queryParam(req, 'sessionId')
        const loaded = await loadSessionEventsDetailed(sessionId)
        const flags = await readInjectionFlags(sessionId, loaded.events)
        const report = buildInjectionReport(loaded.events, {
          source: loaded.source,
          budget: inspectPluginBudget(homeDir),
          home: homeDir,
        })
        report.flags = flags
        report.injection.consistent = flags.effective === report.injection.observed
        if (!report.injection.consistent) {
          report.injection.consistency_note = flags.effective
            ? '开关判定为“应注入”，但本条 runtime-context 里没有 deepmemory 段：可能是本轮尚未刷新（新会话首轮）、注入缓存为空、或日志/快照落后。'
            : '开关判定为“不应注入”，但日志里仍有 deepmemory 段：可能是开关刚改、缓存尚未失效（插件有 TTL），或该段来自更早的回合。'
        }
        writeJson(res, 200, report)
      } catch (error) {
        const message = String((error && error.message) || error)
        writeJson(res, message.includes('无法读取会话事件') || message.includes('session_id 必填') ? 404 : 500, { error: message })
      }
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/context/tools',
    handler: async (req, res) => {
      if (req.method !== 'GET') return writeJson(res, 405, { error: 'method not allowed' })
      try {
        const sessionId = queryParam(req, 'session_id') || queryParam(req, 'sessionId')
        const loaded = await loadSessionEventsDetailed(sessionId)
        const meta = sessionMeta(loaded.events)
        const pluginRows = loadPluginRows(homeDir, meta.agentPreset)
        const report = buildToolsReport(loaded.events, { source: loaded.source, pluginRows })
        writeJson(res, 200, report)
      } catch (error) {
        const message = String((error && error.message) || error)
        writeJson(res, message.includes('无法读取会话事件') || message.includes('session_id 必填') ? 404 : 500, { error: message })
      }
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/context/config',
    handler: (req, res) => {
      if (req.method !== 'POST') return writeJson(res, 405, { error: 'method not allowed' })
      readRequestJson(req)
        .then(async (body) => {
          const patch = {}
          if (body && body.inject_enabled !== undefined) {
            if (typeof body.inject_enabled !== 'boolean') throw new Error('inject_enabled 必须是 boolean')
            patch['injection.inject_enabled'] = body.inject_enabled
            patch.inject_enabled = body.inject_enabled
          }
          if (body && body.inject_card !== undefined) {
            if (typeof body.inject_card !== 'boolean') throw new Error('inject_card 必须是 boolean')
            patch['injection.inject_card'] = body.inject_card
            patch.inject_card = body.inject_card
          }
          if (body && body.inject_summary !== undefined) {
            if (typeof body.inject_summary !== 'boolean') throw new Error('inject_summary 必须是 boolean')
            patch['compression.inject_summary'] = body.inject_summary
            patch.inject_summary = body.inject_summary
          }
          if (body && body.recall_k !== undefined) {
            const value = Number(body.recall_k)
            if (!Number.isInteger(value) || value < 1 || value > 50) throw new Error('recall_k 必须是 1-50 的整数')
            patch['recall_engine.top_k'] = value
            patch.recall_k = value
          }
          if (!Object.keys(patch).length) throw new Error('没有可写入的键（支持 inject_enabled / inject_card / inject_summary / recall_k）')
          const saved = await memoryRequest('POST', '/v1/config', patch)
          const after = await memoryRequest('GET', '/v1/config').catch(() => ({ config: {} }))
          return { saved: (saved && saved.saved) || Object.keys(patch).length, patch, config: (after && after.config) || {} }
        })
        .then((result) => writeJson(res, 200, result))
        .catch((error) => writeJson(res, 400, { error: String((error && error.message) || error) }))
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/context/session-flag',
    handler: (req, res) => {
      if (req.method !== 'POST') return writeJson(res, 405, { error: 'method not allowed' })
      readRequestJson(req)
        .then(async (body) => {
          const sessionId = String((body && body.session_id) || '').trim()
          if (!sessionId) throw new Error('session_id 必填')
          if (typeof body.value !== 'boolean') throw new Error('value 必须是 boolean（false 才能回退，禁止 null）')
          const key = body.key === 'child_memory' ? 'child_memory:' + String(body.parent_session_id || sessionId) : 'session_enabled:' + sessionId
          await memoryRequest('POST', '/v1/settings/set', { key, value: body.value })
          return { saved: true, key, value: body.value }
        })
        .then((result) => writeJson(res, 200, result))
        .catch((error) => writeJson(res, 400, { error: String((error && error.message) || error) }))
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/models',
    handler: async (req, res) => {
      if (req.method !== 'GET') {
        res.writeHead(405)
        res.end()
        return
      }
      try {
        const llm = ctx.get('llm')
        const catalog = []
        if (llm && typeof llm.listProviders === 'function') {
          const rawProviders = await llm.listProviders()
          const providerNames = (rawProviders || []).map((p) => p.id || p.provider || p.name).filter(Boolean)
          const uniq = new Set(providerNames)
          for (const name of uniq) {
            const entry = { id: name, models: [] }
            try {
              const models = typeof llm.listModels === 'function' ? await llm.listModels(name) : []
              entry.models = (models || []).map((m) => ({ id: m.id, name: m.name }))
            } catch {}
            catalog.push(entry)
          }
        }
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ providers: catalog }))
      } catch (error) {
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ error: String((error && error.message) || error) }))
      }
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/maintenance/consolidate',
    handler: (req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405)
        res.end()
        return
      }
      readRequestJson(req)
        .then((body) => performConsolidation(ctx, {
          force: true,
          similarity: body.similarity,
          limitGroups: body.limit_groups,
        }))
        .then((result) => {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify(result))
        })
        .catch((error) => {
          res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        })
    },
  })

  ctx.webServer.register({
    kind: 'exact',
    path: PREFIX + '/v1/cards/initialize',
    handler: (req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405)
        res.end()
        return
      }
      readRequestJson(req)
        .then((body) => {
          const sessionId = String(body.session_id || '').trim()
          const kind = body.kind === 'task' ? 'task' : 'daily'
          if (!sessionId) throw new Error('session_id is required')
          return initializeStateCard(ctx, sessionId, kind)
        })
        .then((result) => {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify(result))
        })
        .catch((error) => {
          const message = String((error && error.message) || error)
          // 抽取/LLM 失败属于暂时不可用（可重试），不是客户端/服务器错误 → 503。
          // HTTP 500 只保留给真正的内部异常。
          const status = message.includes('state card extraction') || message.includes('llm_stream') ? 503 : 500
          res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ status: status === 503 ? 'extract_failed' : 'error', error: message }))
        })
    },
  })

  ctx.webServer.register({
    kind: 'prefix',
    path: PREFIX,
    handler: (req, res) => {
      let rel = req.url ?? ''
      if (rel.startsWith(PREFIX)) rel = rel.slice(PREFIX.length)
      if (!rel.startsWith('/')) rel = '/' + rel
      const upstreamPath = rel || '/v1/health'
      const headers = { ...req.headers }
      delete headers.origin
      delete headers.authorization
      const token = readToken()
      if (token) headers.authorization = `Bearer ${token}`
      headers.host = `${TARGET_HOST}:${TARGET_PORT}`
      const upstream = http.request(
        {
          host: TARGET_HOST,
          port: TARGET_PORT,
          path: upstreamPath,
          method: req.method ?? 'GET',
          headers,
          timeout: 30000,
        },
        (upRes) => {
          res.writeHead(upRes.statusCode ?? 502, upRes.headers)
          // N12：上游中断/出错时终止下游，避免响应流悬置与未处理流错误
          upRes.on('error', () => { try { res.destroy() } catch (e) {} })
          upRes.on('aborted', () => { try { res.destroy() } catch (e) {} })
          res.on('close', () => { if (!res.writableEnded) { try { upRes.destroy() } catch (e) {} } })
          upRes.pipe(res)
        },
      )
      upstream.on('timeout', () => upstream.destroy(new Error('memory-server request timeout')))
      upstream.on('error', (error) => {
        try {
          // N12：已发头时不能再次 writeHead —— 直接终止连接
          if (res.headersSent) { res.destroy(); return }
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        } catch {}
      })
      // N12：客户端中途断开时联动终止上游，避免上游继续空跑
      req.on('aborted', () => { try { upstream.destroy() } catch (e) {} })
      req.pipe(upstream)
    },
  })

  const scheduled = async () => {
    try {
      const result = await performConsolidation(ctx)
      if (result.status !== 'disabled' && result.status !== 'skipped') {
        ctx.logger.info('scheduled consolidation: %o', result)
      }
    } catch (error) {
      ctx.logger.warn('scheduled consolidation failed: %s', String((error && error.message) || error))
    }
  }
  ctx.timeout(scheduled, 15000)
  ctx.interval(scheduled, 3600 * 1000)
}
