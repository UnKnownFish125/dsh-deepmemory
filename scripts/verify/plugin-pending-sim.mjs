#!/usr/bin/env node
// P0-3 本地模拟验证（不连真实 memory-server、不碰生产 pending 目录）
//
// 做法：mock globalThis.fetch（只实现插件实际调用的 3 类接口）+ mock ctx/llm，
// 直接 import 并 apply 仓库里的 plugin-v3.js，然后触发真实的 session/event 与
// agent/turn-stopping 钩子。
//
// 覆盖：
//   S1 落库 HTTP 失败 → 来源消息留在 pending（未丢），恢复后定时重放落库并 ack；
//   S2 落库成功 → pending ack（文件删除）、消息被消费，再次 turn-stopping 不重复抽取；
//   S3 server 已提交但响应丢失 → 用同一 uuid 重放 → 命中 UNIQUE 约束，视为已落库，不产生重复行；
//   S4 >8000 字符输入 → 按 ≤8000 分块、逐块抽取落库，尾部消息参与抽取且全量覆盖；
//   S5 部分条目失败 → 按条重试（失败项保留在 pending，不静默丢）。
//
// 用法：node scripts/verify/plugin-pending-sim.mjs
// 退出码 0 = 全部 PASS。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUGIN = path.resolve(HERE, '../../agent-preset/memory-plugin/plugin-v3.js')
const TMP_ROOT = path.resolve(process.env.VERIFY_TMP_ROOT || '/www/verify-tmp', 'plugin-pending-sim')

if (!fs.existsSync(PLUGIN)) {
  console.error('plugin not found: ' + PLUGIN)
  process.exit(2)
}

fs.rmSync(TMP_ROOT, { recursive: true, force: true })
fs.mkdirSync(TMP_ROOT, { recursive: true })

// ── mock server state ───────────────────────────────────────────────────────
const server = {
  stored: new Map(),          // uuid -> item（模拟 documents.uuid UNIQUE）
  addBatchCalls: [],          // 每次 add_batch 的 items 快照
  llmCalls: [],               // 每次抽取模型的输入（文本 + 长度）
  extractResults: [],
  pendingSeenDuringExtract: [], // 抽取时 pending 文件是否已落盘
  batchMode: 'ok',            // ok | http500 | dropThenDup | itemFailAlways | itemFailFirstRound
  memoriesPerChunk: 1,
  llmFail: false,             // true = 抽取模型不可用（模拟抽取阶段崩溃）
  dupCount: 0,
  failedOnce: false,
  droppedOnce: false,
  itemFailedOnce: false,
  reset() {
    this.stored.clear()
    this.addBatchCalls = []
    this.llmCalls = []
    this.extractResults = []
    this.pendingSeenDuringExtract = []
    this.batchMode = 'ok'
    this.memoriesPerChunk = 1
    this.llmFail = false
    this.dupCount = 0
    this.failedOnce = false
    this.droppedOnce = false
    this.itemFailedOnce = false
  },
}

function jsonResponse(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return data },
  }
}

globalThis.fetch = async (input, init = {}) => {
  const url = new URL(String(input))
  const p = url.pathname
  const method = String(init.method || 'GET').toUpperCase()
  // 插件读配置：本模拟统一 404 → 用默认值（EXTRACT_THRESHOLD=4 等）
  if (p.startsWith('/v1/settings/session_enabled:')) return jsonResponse(200, { value: true })
  if (p.startsWith('/v1/settings/')) return jsonResponse(404, { error: 'no setting in sim' })
  // 状态卡 / 任务板：本模拟的抽取结果不含 card/tasks，正常不会走到这里
  if (p.startsWith('/v1/v2/tasks')) return jsonResponse(200, { tasks: [] })
  if (p === '/v1/memories/add_batch' && method === 'POST') {
    const body = JSON.parse(init.body || '{}')
    const items = Array.isArray(body.items) ? body.items : []
    server.addBatchCalls.push(items)

    if (server.batchMode === 'http500') return jsonResponse(503, { error: 'simulated backend restart' })

    // server 已提交、响应丢失：本批实际入库，但返回空响应体（没有 added）
    if (server.batchMode === 'dropThenDup' && !server.droppedOnce) {
      for (const item of items) server.stored.set(String(item.uuid), item)
      server.droppedOnce = true
      return jsonResponse(200, {})
    }

    const added = []
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const uuid = String(item.uuid)
      // 部分条目失败：全部条目报普通错误（模拟后端逐条拒绝），条目应保留在 pending 重试
      if (server.batchMode === 'itemFailAlways') {
        added.push({ error: 'simulated per-item write failure' })
        continue
      }
      // 部分条目失败：第一轮第 0 条失败、第 1 条成功；第二轮失败条按条重试
      if (server.batchMode === 'itemFailFirstRound' && !server.itemFailedOnce && i === 0) {
        added.push({ error: 'simulated transient per-item failure' })
        continue
      }
      if (server.stored.has(uuid)) {
        server.dupCount += 1
        added.push({ error: 'UNIQUE constraint failed: documents.uuid', content: String(item.content || '').slice(0, 80) })
        continue
      }
      server.stored.set(uuid, item)
      added.push({ id: server.stored.size, uuid })
    }
    if (server.batchMode === 'itemFailFirstRound') server.itemFailedOnce = true
    return jsonResponse(200, { added })
  }
  return jsonResponse(404, { error: 'sim not implemented: ' + method + ' ' + p })
}

// ── mock llm：抽取输入里出现 M01-/M02-… 标记 → 每个分块返回 N 条记忆 ───────────
function makeFakeLlm() {
  return {
    async listProviders() { return [{ id: 'deepseek-official' }] },
    async listModels() { return [{ id: 'deepseek-flash', reasoning: { efforts: [{ id: 'low' }] } }] },
    stream(opts) {
      const text = String((((opts || {}).messages || [])[0] || {}).content?.[0]?.text || '')
      const markers = Array.from(new Set(text.match(/M\d\d-/g) || []))
      server.llmCalls.push({ len: text.length, markers, text, ok: !server.llmFail })
      if (server.llmFail) {
        return (async function* () { throw new Error('simulated llm outage') })()
      }
      // 抽取时 pending 文件必须已经在磁盘上（证明「抽取前落盘」）
      let pendingCount = 0
      try { pendingCount = fs.readdirSync(process.env.DEEPMEMORY_PENDING_DIR).filter((n) => n.endsWith('.json')).length } catch (e) {}
      server.pendingSeenDuringExtract.push(pendingCount > 0)
      const memories = []
      for (let i = 0; i < server.memoriesPerChunk; i++) {
        memories.push({ content: 'chunk' + i + ': ' + markers.join(',') + ' len=' + text.length, key_facts: 'sim', type: 'fact' })
      }
      server.extractResults.push({ memories, card: null, tasks: [] })
      const out = JSON.stringify({ memories, card: null, tasks: [] })
      return (async function* () {
        yield { type: 'text-delta', text: out }
      })()
    },
  }
}

// ── mock ctx ────────────────────────────────────────────────────────────────
function makeContext() {
  const ctx = {
    handlers: new Map(),
    disposers: [],
    llm: makeFakeLlm(),
    on(name, fn) { ctx.handlers.set(name, fn) },
    get(name) {
      if (name === 'llm') return ctx.llm
      if (name === 'commands') return null
      return undefined
    },
    effect(fn) {
      const dispose = fn()
      if (typeof dispose === 'function') ctx.disposers.push(dispose)
      return dispose
    },
    tools: { register() {}, restrict() {} },
  }
  return ctx
}

const plugin = await import(pathToFileURL(PLUGIN).href)
const tick = (ms) => new Promise((r) => setTimeout(r, ms))

async function newRig(label, opts = {}) {
  const options = opts || {}
  const dir = path.join(TMP_ROOT, label, 'pending')
  if (!options.keepDir) {
    fs.rmSync(path.dirname(dir), { recursive: true, force: true })
    fs.mkdirSync(dir, { recursive: true })
  } else {
    fs.mkdirSync(dir, { recursive: true })
  }
  process.env.DEEPMEMORY_PENDING_DIR = dir
  process.env.DEEPMEMORY_PENDING_REPLAY_MS = '150'
  process.env.DEEPMEMORY_PENDING_STARTUP_MS = '150'
  if (!options.keepServer) server.reset()
  const ctx = makeContext()
  plugin.apply(ctx, { preset_mode: 'task' })
  await tick(30) // 让 apply 里的 loadConfig() 落定（本模拟全部 404）
  return {
    ctx,
    dir,
    dispose() {
      for (const d of ctx.disposers) { try { d() } catch (e) {} }
    },
    pendingFiles() {
      try { return fs.readdirSync(dir).filter((n) => n.endsWith('.json')).map((n) => path.join(dir, n)) } catch (e) { return [] }
    },
    readPending() {
      const files = this.pendingFiles()
      if (!files.length) return null
      return JSON.parse(fs.readFileSync(files[0], 'utf8'))
    },
  }
}

const SID = 'sim-session-0001'
const agentOf = (sid) => ({ id: sid, session: { header: { id: sid } } })
const userEvent = (text) => ({ type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text }] } })

async function feed(ctx, texts) {
  const on = ctx.handlers.get('session/event')
  for (const text of texts) await on({ header: { id: SID } }, userEvent(text))
}

async function turnStopping(ctx, sid = SID) {
  const on = ctx.handlers.get('agent/turn-stopping')
  return await on({ agent: agentOf(sid) })
}

function waitFor(fn, timeoutMs = 5000) {
  const t0 = Date.now()
  return new Promise((resolve) => {
    const poll = () => {
      let v = false
      try { v = fn() } catch (e) {}
      if (v) return resolve(true)
      if (Date.now() - t0 > timeoutMs) return resolve(false)
      setTimeout(poll, 50)
    }
    poll()
  })
}

// ── 断言工具 ────────────────────────────────────────────────────────────────
let failures = 0
let current = ''
function scenario(name) { current = name; console.log('\n=== ' + name + ' ===') }
function assert(cond, msg, detail) {
  if (cond) {
    console.log('  PASS  ' + msg)
  } else {
    failures += 1
    console.log('  FAIL  ' + msg + (detail !== undefined ? '  << ' + detail : ''))
  }
}
const messages = (n, width = 40) => Array.from({ length: n }, (_, i) =>
  'M' + String(i + 1).padStart(2, '0') + '-' + 'x'.repeat(Math.max(1, width - 5)))

// ═══════════════════════════════════════════════════════════════════════════
// S1：落库 HTTP 失败 → 消息留在 pending，恢复后重放落库并 ack
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S1 落库 HTTP 失败 → 未丢消息 + 恢复后重放')
  const rig = await newRig('s1')
  try {
    const texts = messages(5)
    await feed(rig.ctx, texts)
    server.batchMode = 'http500'
    await turnStopping(rig.ctx)

    const files = rig.pendingFiles()
    assert(files.length === 1, 'HTTP 失败后 pending 文件留存（1 个）', files.length)
    const rec = rig.readPending()
    assert(!!rec, 'pending 记录可解析')
    assert(rec && rec.sourceMessages.length === texts.length,
      'pending 内含全部 ' + texts.length + ' 条来源消息', rec && rec.sourceMessages.length)
    assert(rec && rec.chunks.length === 1 && rec.chunks[0].items.length === 1,
      '抽取结果条目已写入 pending（等待重试）', rec && rec.chunks.map((c) => c.items.length).join(','))
    assert(rec && rec.chunks[0].status === 'extracted',
      'chunk 状态 = extracted（已抽取未落库）', rec && rec.chunks[0].status)
    const failCalls = server.addBatchCalls.length
    assert(failCalls === 3, 'live 内按轮重试了 3 次 add_batch', failCalls)

    // 后端恢复 → 等待定时重放
    server.batchMode = 'ok'
    const cleared = await waitFor(() => rig.pendingFiles().length === 0, 5000)
    assert(cleared, '后端恢复后 pending 被 ack（文件删除）')
    assert(server.stored.size === 1, '重放只写入 1 条记忆', server.stored.size)
    const uuidsAcrossCalls = server.addBatchCalls.map((items) => items.map((i) => i.uuid).join('|'))
    assert(new Set(uuidsAcrossCalls).size === 1, '所有重试/重放使用同一批 uuid（幂等键稳定）', JSON.stringify(uuidsAcrossCalls))

    // 已消费：再次 turn-stopping 不应再抽/再写
    const callsBefore = server.addBatchCalls.length
    await turnStopping(rig.ctx)
    await tick(100)
    assert(server.addBatchCalls.length === callsBefore, '消息已消费：再次 turn-stopping 不再落库')
    const leftovers = fs.readdirSync(rig.dir).filter((n) => n.includes('.tmp-') || n.endsWith('.lock'))
    assert(leftovers.length === 0, '无 .tmp-* / .lock 残留', JSON.stringify(leftovers))
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S2：落库成功 → 立即 ack 并消费消息
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S2 落库成功 → 消费 + ack')
  const rig = await newRig('s2')
  try {
    await feed(rig.ctx, messages(4))
    await turnStopping(rig.ctx)
    assert(rig.pendingFiles().length === 0, '成功后 pending 已 ack（无遗留文件）', rig.pendingFiles().length)
    assert(server.addBatchCalls.length === 1, 'add_batch 只调用 1 次', server.addBatchCalls.length)
    assert(server.stored.size === 1, '记忆已写入', server.stored.size)
    const first = server.addBatchCalls[0][0]
    assert(first && typeof first.uuid === 'string' && first.uuid.length > 8, '落库条目带 uuid 幂等键', first && first.uuid)

    const before = server.addBatchCalls.length
    await turnStopping(rig.ctx)
    await tick(100)
    assert(server.addBatchCalls.length === before, '消息已消费：第二次 turn-stopping 不重复抽取')
    assert(server.llmCalls.length === 1, '抽取模型只调用 1 次', server.llmCalls.length)
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S3：server 已提交但响应丢失 → 同 uuid 重放不重复
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S3 已提交但响应丢失 → 重放命中 uuid 唯一约束，不重复')
  const rig = await newRig('s3')
  try {
    await feed(rig.ctx, messages(4))
    server.batchMode = 'dropThenDup'
    await turnStopping(rig.ctx)
    assert(rig.pendingFiles().length === 0, '重放确认后 pending 已 ack', rig.pendingFiles().length)
    assert(server.addBatchCalls.length === 2, '同一批被重试 1 轮（共 2 次 add_batch）', server.addBatchCalls.length)
    const firstUuids = server.addBatchCalls[0].map((i) => i.uuid).join('|')
    const secondUuids = server.addBatchCalls[1].map((i) => i.uuid).join('|')
    assert(firstUuids === secondUuids, '重放复用同一 uuid', firstUuids + ' vs ' + secondUuids)
    assert(server.dupCount === 1, '第二次被 server UNIQUE 约束识别为已存在（1 次 dup）', server.dupCount)
    assert(server.stored.size === 1, '最终只有 1 条记忆（无重复行）', server.stored.size)
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S4：>8000 字符输入 → 分块逐批抽取/落库，尾部消息完整覆盖
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S4 >8000 字符 → 分块抽取且尾部消息覆盖')
  const rig = await newRig('s4')
  try {
    const texts = messages(14, 800) // 每条约 800 字符 → 总对话 > 11000 字符
    const expectedDialog = texts.map((t) => '用户: ' + t).join('\n')
    await feed(rig.ctx, texts)
    await turnStopping(rig.ctx)

    assert(server.llmCalls.length === 2, '>8000 字符被切成 2 块（逐块抽取）', server.llmCalls.length)
    assert(server.llmCalls.every((c) => c.len <= 8000), '每块发给模型的内容 ≤8000 字符',
      JSON.stringify(server.llmCalls.map((c) => c.len)))
    assert(server.llmCalls.every((c) => c.len > 0), '无空块')
    assert(server.pendingSeenDuringExtract.every(Boolean), '每次抽取时 pending 文件均已存在（抽取前落盘）',
      JSON.stringify(server.pendingSeenDuringExtract))

    const coveredMarkers = new Set(server.storedMarkers || [])
    for (const item of server.stored.values()) {
      const ms = String(item.content || '').match(/M\d\d-/g) || []
      for (const m of ms) coveredMarkers.add(m)
    }
    const allMarkers = texts.map((t) => t.slice(0, 4))
    assert(allMarkers.every((m) => coveredMarkers.has(m)),
      '全部 ' + allMarkers.length + ' 条消息（含尾部 M14-）都参与了抽取并落库',
      'missing=' + allMarkers.filter((m) => !coveredMarkers.has(m)).join(','))
    const reconstructed = server.llmCalls.map((c) => c.text).join('\n')
    assert(reconstructed === expectedDialog, '分块拼接后与原始整段对话逐字节一致（无截断/漏消息）',
      'reconstructed=' + reconstructed.length + ' expected=' + expectedDialog.length)
    assert(rig.pendingFiles().length === 0, '全部分块落库后 pending 已 ack', rig.pendingFiles().length)
    assert(server.stored.size === 2, '两个分块各写 1 条记忆', server.stored.size)
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S5a：部分条目持续失败 → 失败条目保留在 pending，不静默丢
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S5a 部分条目失败 → 失败条目保留在 pending')
  const rig = await newRig('s5a')
  try {
    server.memoriesPerChunk = 2
    await feed(rig.ctx, messages(4))
    server.batchMode = 'itemFailAlways'
    await turnStopping(rig.ctx)
    const rec = rig.readPending()
    assert(!!rec, '逐条失败后 pending 仍留存')
    assert(rec && rec.chunks[0].items.length === 2, '2 条失败条目全部保留（未静默丢）', rec && rec.chunks[0].items.length)
    assert(rec && rec.chunks[0].status === 'extracted', 'chunk 未标记 done（等待重试）', rec && rec.chunks[0].status)
    assert(server.stored.size === 0, '此时确实一条都没写成功', server.stored.size)

    // 恢复：逐条重试应把保留的条目全部写入
    server.batchMode = 'ok'
    const cleared = await waitFor(() => rig.pendingFiles().length === 0, 5000)
    assert(cleared, '后端恢复后 pending 被重放 ack')
    assert(server.stored.size === 2, '保留的失败条目全部补写成功', server.stored.size)
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S5b：单条瞬时失败 → 按条重试，成功条不重复、失败条补齐
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S5b 部分条目瞬时失败 → 按条重试且不重复成功项')
  const rig = await newRig('s5b')
  try {
    server.memoriesPerChunk = 2
    await feed(rig.ctx, messages(4))
    server.batchMode = 'itemFailFirstRound'
    await turnStopping(rig.ctx)
    assert(server.addBatchCalls.length === 2, '失败后触发第二轮 add_batch（按条重试）', server.addBatchCalls.length)
    const first = server.addBatchCalls[0]
    const second = server.addBatchCalls[1]
    assert(second.length === 1, '第二轮只重发失败的那 1 条', JSON.stringify(second.map((i) => i.uuid)))
    assert(first.find((i) => i.uuid === second[0].uuid), '第二轮 uuid 与第一轮失败条目一致（同一条重试）')
    assert(server.stored.size === 2, '两条记忆最终都写入（成功项未重复写）', server.stored.size)
    assert(server.dupCount === 0, '第一轮已成功的条目没有被重发（dup=0，避免无谓重写）', server.dupCount)
    assert(rig.pendingFiles().length === 0, '全部落库后 pending 已 ack', rig.pendingFiles().length)
  } finally {
    rig.dispose()
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// S6：插件重启（新 apply 实例）→ 启动时自动重放上一进程留下的未 ack 批次
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S6 插件重启 → 启动时重放未 ack 批次')
  // 第一次 apply：后端故障，留下未 ack 批次后「崩溃」
  const rigA = await newRig('s6')
  let dir = ''
  try {
    dir = rigA.dir
    await feed(rigA.ctx, messages(4))
    server.batchMode = 'http500'
    await turnStopping(rigA.ctx)
    assert(rigA.pendingFiles().length === 1, '重启前遗留 1 个未 ack pending 文件', rigA.pendingFiles().length)
    assert(rigA.readPending().chunks[0].items.length === 1, 'pending 内含已抽取、未落库的条目')
  } finally {
    rigA.dispose() // 模拟进程结束（定时器停止）
  }
  // 第二次 apply：同目录、不触发任何会话事件，只靠启动重放
  server.batchMode = 'ok'
  const callsAtRestart = server.addBatchCalls.length
  const rigB = await newRig('s6', { keepDir: true, keepServer: true })
  try {
    assert(rigB.dir === dir, '新实例使用同一 pending 目录')
    const cleared = await waitFor(() => rigB.pendingFiles().length === 0, 5000)
    assert(cleared, '启动重放后 pending 被 ack（无需任何新消息/turn-stopping）')
    assert(server.addBatchCalls.length > callsAtRestart, '启动重放确实发起了 add_batch')
    assert(server.stored.size === 1, '重启后补写 1 条记忆', server.stored.size)
  } finally {
    rigB.dispose()
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// S7：抽取阶段崩溃（pending 里只有来源消息）→ 重启后从 pending 重新抽取落库
// ═══════════════════════════════════════════════════════════════════════════
{
  scenario('S7 抽取阶段崩溃 → 重启后从 pending 重新抽取')
  const rigA = await newRig('s7')
  const texts = messages(4)
  try {
    await feed(rigA.ctx, texts)
    server.llmFail = true
    await turnStopping(rigA.ctx)
    const rec = rigA.readPending()
    assert(!!rec, '抽取失败后 pending 文件留存')
    assert(rec && rec.chunks[0].status === 'pending_extract', 'chunk 状态回退为 pending_extract（可重抽）', rec && rec.chunks[0].status)
    assert(rec && rec.chunks[0].items.length === 0, '此时还没有条目落盘')
    assert(rec && rec.sourceMessages.length === texts.length, '来源消息完整保留在 pending', rec && rec.sourceMessages.length)
  } finally {
    rigA.dispose()
  }
  // 模拟重启：LLM 恢复；不触发任何会话事件，只靠启动重放重新抽取
  server.llmFail = false
  const callsAtRestart = server.addBatchCalls.length
  const rigB = await newRig('s7', { keepDir: true, keepServer: true })
  try {
    const cleared = await waitFor(() => rigB.pendingFiles().length === 0, 5000)
    assert(cleared, '重启后从 pending 重新抽取并 ack')
    assert(server.addBatchCalls.length > callsAtRestart, '重新抽取后确实落库')
    assert(server.stored.size === 1, '补写 1 条记忆', server.stored.size)
    const okCalls = server.llmCalls.filter((c) => c.ok)
    const expected = texts.map((t) => '用户: ' + t).join('\n')
    assert(okCalls.length === 1 && okCalls[0].text === expected,
      '重抽输入与原始来源消息逐字节一致（无丢消息）',
      'okCalls=' + okCalls.length + (okCalls[0] ? ' len=' + okCalls[0].text.length + '/' + expected.length : ''))
    const leftovers = fs.readdirSync(rigB.dir).filter((n) => n.includes('.tmp-') || n.endsWith('.lock'))
    assert(leftovers.length === 0, '无 .tmp-* / .lock 残留', JSON.stringify(leftovers))
  } finally {
    rigB.dispose()
  }
}

console.log('\n============================')
if (failures === 0) {
  console.log('RESULT: ALL PASS')
  process.exit(0)
} else {
  console.log('RESULT: ' + failures + ' FAILURE(S)')
  process.exit(1)
}
