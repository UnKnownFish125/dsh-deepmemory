/**
 * Host half of the memory UI surface plugin.
 * Registers a same-origin proxy route `/mem-api/*` that forwards to the
 * local memory-server (localhost:6230). The browser half only talks to this
 * same-origin route, so no CORS or private-network policy applies.
 */
import http from 'node:http'
import z from '@deepseek-ai/schemastery'
import { settingsNamespace } from '@deepseek-ai/dsh-settings'
import fs from 'node:fs'

export const name = 'deepmemory'

export const inject = ['webServer', 'settings', 'timer']

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
  if (providers.includes('uuapi') && !candidates.includes('uuapi')) candidates.push('uuapi')
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
  return { provider: preferredProvider || 'uuapi', model: preferredModel || 'deepseek-v4-flash', source: 'fallback' }
}

async function summarizeGroup(llm, route, group) {
  const source = [group.primary_content].concat(group.contents || []).filter(Boolean)
  let output = ''
  const stream = llm.stream({
    provider: route.provider,
    model: route.model,
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
  let lastError = ''
  for (let attempt = 0; attempt < 2; attempt++) {
    let output = ''
    try {
      const stream = llm.stream({
        provider: r.provider,
        model: r.model,
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

function latestMemoryQuery(agent) {
  const parts = []
  const events = agent && agent.session && agent.session.events ? agent.session.events : []
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
  for (const event of agent.session.events) {
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
  const agent = ctx.get('agents')?.get(sessionId)
  if (agent && agent.session) return agent.session.events
  const persistence = ctx.get('sessionPersistence')
  if (!persistence) return null
  const inspection = await persistence.inspect(sessionId)
  return inspection && inspection.events
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

export function apply(ctx) {
  // The official Plugins settings page discovers configurable cards from the
  // Host settings namespace list, then dispatches settings.plugin.item by key.
  // Deepmemory keeps its actual config in memory-server, so this empty section
  // is only the discovery contract for the browser-owned configuration card.
  ctx.settings.register(settingsNamespace('deepmemory'), z.object({}))

  const cardBuckets = new Map()
  const cardRuns = new Set()
  const cadenceRuns = new Map()
  const hostMemoryCache = new Map()
  const hostMemoryInitialized = new Set()
  const hostMemoryRefreshes = new Map()

  async function refreshHostMemory(agent, config) {
    const sessionId = String(agent.session.id)
    if (hostMemoryRefreshes.has(sessionId)) return hostMemoryRefreshes.get(sessionId)
    const refresh = (async () => {
      const limit = Math.max(1, Math.min(20, Number(config['context_automation.memory_completion_k'] || 5)))
      const workspaceId = String(config.workspace || 'deepseek-harness')
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
            for (const event of agent.session.events) {
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
          upRes.pipe(res)
        },
      )
      upstream.on('timeout', () => upstream.destroy(new Error('memory-server request timeout')))
      upstream.on('error', (error) => {
        try {
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ error: String((error && error.message) || error) }))
        } catch {}
      })
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
