// Preset plugin contract + HTTP transport verification.
import fs from 'node:fs'
import os from 'node:os'
import pathModule from 'node:path'

const path = process.env.PLUGIN_PATH
const tokenHome = fs.mkdtempSync(pathModule.join(os.tmpdir(), 'deepmemory-token-'))
const originalDshHome = process.env.DSH_HOME
const originalHome = process.env.HOME
delete process.env.DSH_HOME
process.env.HOME = tokenHome
fs.writeFileSync(pathModule.join(tokenHome, '.dsh-memory-api-token'), 'verify-memory-token\n', { mode: 0o600 })
const requests = []
const llmCalls = []
let promptSection
let contextAutomationEnabled = true
globalThis.fetch = async (url, options = {}) => {
  requests.push({ url: String(url), method: options.method || 'GET', headers: options.headers, body: options.body })
  const pathname = new URL(url).pathname
  let data = { value: null }
  if (pathname.endsWith('/memories/add')) data = { id: 1 }
  if (pathname.endsWith('/memories/add_batch')) data = { added: [1] }
  if (pathname.endsWith('/memories/search')) {
    const payload = options.body ? JSON.parse(options.body) : {}
    const sessionId = payload.session_id || 'tool'
    data = { count: 1, results: [{ content: 'memory-for-' + sessionId, type: 'fact', scope: 'session' }] }
  }
  if (pathname.endsWith('/briefing')) data = { count: 0, briefing: '' }
  if (pathname.endsWith('/config/session')) data = { config: { 'state_card.auto_generate': false, 'context_automation.enabled': contextAutomationEnabled, 'context_automation.memory_completion_k': 5 } }
  if (pathname.includes('/v2/cards/') && (options.method || 'GET') === 'GET') data = { card: null }
  if (pathname.includes('/v2/cards/') && options.method === 'PUT') data = { card: { version: 1 } }
  if (pathname.endsWith('/v2/tasks') && options.method === 'POST') data = { task: { id: 'task-1' } }
  return { ok: true, status: 200, json: async () => data }
}

const plugin = await import(path)
if (typeof plugin.apply !== 'function') throw new Error('apply 不是函数')
if (plugin.name !== 'deepmemory') throw new Error('插件 name 异常: ' + plugin.name)

const registered = new Map()
const eventHandlers = new Map()
const ctx = {
  get: (name) => {
    if (name === 'systemPrompt') return { section: (section) => { promptSection = section; return () => {} } }
    if (name === 'llm') return {
      stream: async function* (options) {
        llmCalls.push(options)
        const fakeToken = 'ghp_' + 'VERIFYPAT_0123456789abcdefghijklmnopqrstuvwx'
        yield { type: 'text-delta', text: JSON.stringify({ memories: [{ content: '用户本地 Gitea token ' + fakeToken, key_facts: 'token key', type: 'fact' }], card: { goal: 'target ' + fakeToken, current_plan: '', key_decisions: [], in_progress: [], next_steps: [] }, tasks: [] }) }
      },
    }
    return undefined
  },
  tools: { register: (tool) => { registered.set(tool.name, tool); return () => {} } },
  effect: (fn) => fn(),
  on: (name, handler) => { eventHandlers.set(name, handler); return () => {} },
  interval: () => {},
}
plugin.apply(ctx)

const expected = ['memory_recall', 'memory_save', 'memory_briefing']
for (const name of expected) {
  if (!registered.has(name)) throw new Error('工具未注册: ' + name)
}
const marker = 'transport-json-marker'
await registered.get('memory_save').execute({ content: marker })
const write = requests.find((item) => item.url.endsWith('/v1/memories/add'))
if (!write) throw new Error('memory_save 未发起 HTTP 请求')
if (write.method !== 'POST') throw new Error('memory_save 请求方法异常: ' + write.method)
const payload = JSON.parse(write.body)
if (payload.content !== marker) throw new Error('memory_save JSON body 损坏')
if (!write.headers || write.headers.Authorization !== 'Bearer verify-memory-token') {
  throw new Error('HOME token fallback 未写入 Authorization header')
}
if (requests.some((item) => !item.url.startsWith('http://localhost:6230/'))) {
  throw new Error('默认请求越出本地 memory-server 边界')
}

const eventHandler = eventHandlers.get('session/event')
const assembleHandler = eventHandlers.get('system-prompt/assemble')
const stoppingHandler = eventHandlers.get('agent/turn-stopping')
if (!eventHandler || !assembleHandler || !stoppingHandler) throw new Error('记忆事件处理器未注册')
const session = { header: { id: 'verify-session' } }
const agent = { id: 'verify-session' }
async function assembleFor(targetAgent) {
  const assembly = {
    sections: [{ name: 'deepmemory', text: promptSection.text({ agent: targetAgent, scope: targetAgent }) }],
    contexts: [], tools: [], variables: {},
  }
  return assembleHandler(assembly, { agent: targetAgent, scope: targetAgent }, async () => assembly)
}
const firstAssembly = await assembleFor(agent)
const firstSearchCount = requests.filter((item) => item.url.endsWith('/v1/memories/search')).length
await assembleFor(agent)
const secondSearchCount = requests.filter((item) => item.url.endsWith('/v1/memories/search')).length
if (firstSearchCount !== secondSearchCount) throw new Error('稳定缓存仍在每次 prompt 组装时重建')
if (!promptSection || typeof promptSection.text !== 'function') throw new Error('system prompt 记忆段未注册')
if (!firstAssembly.sections[0].text.includes('memory-for-verify-session')) throw new Error('首个请求未渲染当前会话缓存')
const otherAgent = { id: 'verify-session-2' }
const otherAssembly = await assembleFor(otherAgent)
if (!otherAssembly.sections[0].text.includes('memory-for-verify-session-2')) throw new Error('第二会话缓存渲染异常')
if (promptSection.text({ agent, scope: agent }).includes('memory-for-verify-session-2')) throw new Error('system prompt 记忆缓存发生跨会话污染')
contextAutomationEnabled = false
const disabledAgent = { id: 'verify-session-disabled' }
const disabledAssembly = await assembleFor(disabledAgent)
if (disabledAssembly.sections[0].text !== '') throw new Error('关闭 context_automation.enabled 后仍注入记忆补全')
contextAutomationEnabled = true
for (let i = 0; i < 4; i++) {
  await eventHandler(session, {
    type: 'user/message',
    data: { content: [{ type: 'text', text: '抽取路由验证 ' + i }] },
  })
}
await stoppingHandler({ agent: { id: 'verify-session' } })
// credentials must never escape to LLM, memory, card, or task payloads
  const rawToken = 'ghp_' + 'VERIFYPAT0123456789abcdefghijklmnopqrstuvwx'
  const allBodies = requests.map((r) => String(r.body || '')).join('\n') + '\n' + (llmCalls[0] && llmCalls[0].messages ? llmCalls[0].messages.map((m)=>JSON.stringify(m)).join('\n') : '')
  if (allBodies.includes(rawToken)) throw new Error('sensitive token leak: credential escaped into memory/tool/LLM call')
  const batchItems = JSON.parse((requests.find((r) => r.url.endsWith('/v1/memories/add_batch')) || { body: '{"items":[]}' }).body || '{"items":[]}')
  if ((batchItems.items || []).some((m) => JSON.stringify(m).includes(rawToken))) throw new Error('sensitive token leaked into add_batch')
if (llmCalls.length !== 1) throw new Error('记忆抽取未调用一次 LLM')
if (llmCalls[0].provider !== 'uuapi' || llmCalls[0].model !== 'deepseek-v4-flash') {
  throw new Error('记忆抽取模型路由异常: ' + JSON.stringify({ provider: llmCalls[0].provider, model: llmCalls[0].model }))
}
// AI 状态卡写回：抽取到 card 后应调用 PUT（§3.5 新契约，不再"丢弃 result.card"）
const cardWrites = requests.filter((item) => item.method === 'PUT' && item.url.includes('/v1/v2/cards/'))
if (cardWrites.length !== 1) throw new Error('AI 状态卡写回未按契约调用一次 PUT /v1/v2/cards/')
const cardBody = JSON.parse(cardWrites[0].body || '{}')
if (cardBody.actor !== 'main_agent') throw new Error('AI 状态卡写回 actor 异常: ' + cardBody.actor)
// AI 任务板写入：LLM 无明确任务时不应创建任务
const taskWrites = requests.filter((item) => item.method === 'POST' && item.url.endsWith('/v1/v2/tasks'))
if (taskWrites.length !== 0) throw new Error('无明确任务时不应创建 tasks')
fs.rmSync(tokenHome, { recursive: true, force: true })
if (originalDshHome === undefined) delete process.env.DSH_HOME
else process.env.DSH_HOME = originalDshHome
if (originalHome === undefined) delete process.env.HOME
else process.env.HOME = originalHome
console.log('plugin apply + native fetch ok, tools:', expected.join(', '))
