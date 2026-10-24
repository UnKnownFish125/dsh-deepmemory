/**
 * Offline verification of compressor-core.mjs against REAL exported DSH blocks.
 *
 * Input: /tmp/bc-exp/blocks.jsonl produced by analyze_sessions.py from real
 * session logs (one JSON object per compaction block, with the raw payload of
 * the *newly* shadowed events and the exact seq list).
 *
 * Run: node verify_proto.mjs [/tmp/bc-exp/blocks.jsonl]
 */
import fs from 'node:fs'
import { createHash } from 'node:crypto'
import {
  createIncrementalCompressor, createMemoryStore, readCompactionEvent,
  planIncrement, estTokens,
} from './compressor-core.mjs'

const FILE = process.argv[2] || '/tmp/bc-exp/blocks.jsonl'
const sha256 = (s) => createHash('sha256').update(s, 'utf8').digest('hex')
const results = { file: FILE, sessions: [], summary: {}, checks: {} }
let failures = 0
const check = (name, cond, detail) => {
  results.checks[name] = results.checks[name] || { pass: 0, fail: 0, details: [] }
  if (cond) results.checks[name].pass += 1
  else { results.checks[name].fail += 1; failures += 1 }
  if (detail !== undefined && results.checks[name].details.length < 6) {
    results.checks[name].details.push(detail)
  }
}

// events shaped like DSH message events, rebuilt from the exported payload
const toEvents = (row) => (row.payload_events || []).map((e) => ({
  seq: e.seq,
  type: 'user/message', // the compressor only reads text through eventText()
  data: { message: { content: [{ type: 'text', text: '(原类型 ' + (e.kind || e.type) + ') ' + (e.text || '') }] } },
}))

// deterministic stand-in for the delta summarizer (volume tests only)
const proxySummary = (content, budget) => {
  const flat = content.replace(/\s+/g, ' ').trim()
  return flat.slice(0, Math.min(budget, flat.length))
}

const rows = fs.readFileSync(FILE, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l))
const bySession = new Map()
for (const r of rows) {
  if (!bySession.has(r.session_id)) bySession.set(r.session_id, [])
  bySession.get(r.session_id).push(r)
}

for (const [sessionId, blocks] of bySession) {
  const store = createMemoryStore()
  const compressor = createIncrementalCompressor({ store, logger: { warn() {} } })
  const rec = {
    session_id: sessionId, blocks: blocks.length,
    compressOk: 0, skipped: 0, expandCalls: 0,
    storedChars: 0, expandedChars: 0, roundtripOk: 0, roundtripFail: 0,
    legacyInjectionChars: 0, incInjectionChars: 0, incBlocksKept: 0, mapChars: 0, mapHasIds: false,
    prefixHolds: 0, prefixBreaks: 0, floorAdvances: 0,
  }
  let prevInjection = null
  for (const row of blocks) {
    const events = toEvents(row)
    const summary = proxySummary(row.payload_text, 240) // 240 chars ≈ legacy slice
    const r1 = await compressor.compress({
      sessionId, events, seqs: row.new_seqs, summary, topic: 'block-' + row.index,
    })
    if (r1.ok && !r1.skipped) rec.compressOk += 1
    else rec.skipped += 1
    if (!r1.ok || !r1.handle) continue

    // reversibility: read back from store only, paginate to the end
    const storedBlock = await store.getBlock(r1.handle)
    rec.storedChars += storedBlock.source.length
    let cursor = 0, joined = '', guard = 0
    while (cursor !== null && guard < 10000) {
      const page = await compressor.expand(r1.handle, { cursor, maxChars: 8192 })
      rec.expandCalls += 1
      if (!page.ok) break
      const body = page.text.replace(/^\[expand[^\n]*\]\n/, '').replace(/\n\[expand (结束|未完)[^\]]*\]$/, '')
      joined += body
      cursor = page.nextCursor
      guard += 1
    }
    rec.expandedChars += joined.length
    if (joined === storedBlock.source && sha256(joined) === storedBlock.sourceSha256) rec.roundtripOk += 1
    else rec.roundtripFail += 1
    check('reversible-roundtrip', joined === storedBlock.source, { sessionId, block: row.index })

    // idempotency + incrementality guard
    const r2 = await compressor.compress({ sessionId, events, seqs: row.new_seqs, summary })
    check('duplicate-compress-skipped', !!(r2.ok && r2.skipped), { sessionId, block: row.index, skipped: r2.skipped })
  }

  // injection rendering over the accumulated blocks, budget 2600 (legacy budget)
  const blocksStored = await store.listBlocks(sessionId)
  const legacy = '[历史脉络摘要]\n' + blocksStored.slice(-5)
    .map((b, i) => ('[摘要#' + b.blockId + '] ' + b.summary).slice(0, 240)).join('\n')
  rec.legacyInjectionChars = legacy.length
  // incremental injection text is append-only between floor advances
  for (let k = 1; k <= blocksStored.length; k += 1) {
    const sub = blocksStored.slice(0, k)
    const rendered = await compressor.renderInjection({ sessionId, blocks: sub, budgetChars: 1e9 })
    if (prevInjection !== null) {
      if (rendered.text.startsWith(prevInjection)) rec.prefixHolds += 1
      else rec.prefixBreaks += 1
      check('injection-append-only', rendered.text.startsWith(prevInjection), { sessionId, k })
    }
    prevInjection = rendered.text
  }
  const finalInjection = await compressor.renderInjection({ sessionId, blocks: blocksStored, budgetChars: 2600 })
  rec.incInjectionChars = finalInjection.chars
  rec.incBlocksKept = finalInjection.kept
  // sticky floor: after the tight-budget render, an oversized render must not
  // resurrect blocks below the floor
  const afterFloor = await compressor.renderInjection({ sessionId, blocks: blocksStored, budgetChars: 1e9 })
  rec.floorAfter = await store.getFloorSeq(sessionId)
  check('sticky-floor', !afterFloor.text.includes('[摘要#1 ') || rec.floorAfter === 0, { sessionId, floor: rec.floorAfter })
  const mapRender = await compressor.renderInjection({ sessionId, blocks: blocksStored, mode: 'map', budgetChars: 2600, checkpointCoveredThrough: blocksStored[0] ? blocksStored[0].seqEnd : 0 })
  rec.mapChars = mapRender.chars
  rec.mapHasIds = /Active blocks \(/.test(mapRender.text)
  const st = await compressor.status(sessionId)
  rec.coveredSeqs = st.coveredSeqs
  rec.distinctSeqs = blocks.reduce((n, b) => n + b.new_seqs.length, 0)
  const coveredSet = (store._covered.get(sessionId) || new Set())
  const blockSeqs = blocksStored.flatMap((b) => b.coveredSeqs || [])
  check('seq-ledger-disjoint', coveredSet.size === new Set(blockSeqs).size,
    { sessionId, covered: coveredSet.size, blockSeqs: new Set(blockSeqs).size, distinct: rec.distinctSeqs })
  results.sessions.push(rec)
}

// separate-session isolation: the same seq number in another session must fold
{
  const store = createMemoryStore()
  const c = createIncrementalCompressor({ store, logger: { warn() {} } })
  const ev = [{ seq: 1, type: 'user/message', data: { message: { content: [{ type: 'text', text: 'A' }] } } }]
  const a = await c.compress({ sessionId: 's-A', events: ev, seqs: [1], summary: 'a' })
  const b = await c.compress({ sessionId: 's-B', events: ev, seqs: [1], summary: 'b' })
  check('session-isolation', a.ok && b.ok && !a.skipped && !b.skipped, { a, b })
}

// fail-open: throwing store must never throw out of compress
{
  const throwing = {
    async listBlocks() { throw new Error('db down') },
    async claimSeqs() { throw new Error('db down') },
    async putBlock() { throw new Error('db down') },
    async getBlock() { throw new Error('db down') },
    async setFloorSeq() { throw new Error('db down') },
    async getFloorSeq() { throw new Error('db down') },
  }
  const c = createIncrementalCompressor({ store: throwing, logger: { warn() {} } })
  const r = await c.compress({
    sessionId: 's', seqs: [1], summary: 'x',
    events: [{ seq: 1, type: 'user/message', data: { message: { content: [{ type: 'text', text: 'x' }] } } }],
  })
  check('fail-open-compress', r.ok === false && r.reason === 'exception', r)
  const e = await c.expand('bh_missing')
  check('fail-open-expand-store-down', e.ok === false, e)
  const good = createIncrementalCompressor({ store: createMemoryStore(), logger: { warn() {} } })
  const e2 = await good.expand('bh_missing')
  check('fail-open-expand-missing', e2.ok === false && e2.unavailable === true, e2)
  const inj = await c.renderInjection({ sessionId: 's', budgetChars: 100 })
  check('fail-open-render', inj.ok === false, inj)
}

// summarizer error must be contained too
{
  const store = createMemoryStore()
  const c = createIncrementalCompressor({
    store, logger: { warn() {} }, config: { summarizeTimeoutMs: 50 },
  })
  const r = await c.compress({
    sessionId: 's2', seqs: [7],
    events: [{ seq: 7, type: 'user/message', data: { message: { content: [{ type: 'text', text: 'y' }] } } }],
    summarizer: { async summarize() { throw new Error('llm 502') } },
  })
  check('fail-open-summarizer', r.ok === false, r)
}

// compaction event parser on the shapes seen in real logs (0.1.7/0.2.1)
{
  const ev = { type: 'compaction/summary', data: { compactionId: 'c1', summary: [{ type: 'text', text: 'S' }], shadowedRange: { start: 16, end: 934 }, shadowedSeqs: [16, 20, 934], shadowedTokenCount: 1000 } }
  const parsed = readCompactionEvent(ev)
  check('parse-compaction-event', parsed.seqs.length === 3 && parsed.summaryText === 'S' && parsed.range.start === 16, parsed)
  const plan = planIncrement({ seqs: [1, 2, 3, 4], isCheckpoint: (s) => s === 2, covered: new Set([3]) })
  check('plan-increment', plan.raw.length === 2 && plan.checkpoint.length === 1 && plan.refolded.length === 1, plan)
}

results.summary = {
  sessions: results.sessions.length,
  blocks: results.sessions.reduce((n, s) => n + s.blocks, 0),
  compress_ok: results.sessions.reduce((n, s) => n + s.compressOk, 0),
  skipped: results.sessions.reduce((n, s) => n + s.skipped, 0),
  expand_calls: results.sessions.reduce((n, s) => n + s.expandCalls, 0),
  stored_chars: results.sessions.reduce((n, s) => n + s.storedChars, 0),
  roundtrip_ok: results.sessions.reduce((n, s) => n + s.roundtripOk, 0),
  roundtrip_fail: results.sessions.reduce((n, s) => n + s.roundtripFail, 0),
  legacy_injection_chars: results.sessions.map((s) => s.legacyInjectionChars),
  inc_injection_chars: results.sessions.map((s) => [s.incInjectionChars, s.incBlocksKept]),
  map_injection_chars: results.sessions.map((s) => [s.mapChars, s.mapHasIds]),
  prefix_holds: results.sessions.reduce((n, s) => n + s.prefixHolds, 0),
  prefix_breaks: results.sessions.reduce((n, s) => n + s.prefixBreaks, 0),
  checks_fail: failures,
}
fs.writeFileSync('/tmp/bc-exp/verify-proto.json', JSON.stringify(results, null, 1))
console.log(JSON.stringify(results.summary, null, 2))
console.log('checks:', Object.fromEntries(Object.entries(results.checks).map(([k, v]) => [k, v.pass + '/' + (v.pass + v.fail)])))
console.log(failures === 0 ? 'ALL CHECKS PASSED' : 'FAILURES: ' + failures)
process.exit(failures === 0 ? 0 : 1)
