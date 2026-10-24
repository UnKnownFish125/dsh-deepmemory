/**
 * Incremental, reversible context compressor — prototype (no runtime side effects).
 *
 * This is the concrete shape of `interface Compressor` proposed in
 * docs/ARCHITECTURE.md §6.2, adapted to what DSH 0.1.7 / 0.2.1 actually offers:
 *
 *   - DSH owns the model + the session log. A plugin cannot append custom events
 *     (`session.append` rejects unknown types), so ALL compressor state lives
 *     outside the session log (memory-server DB or a local file).
 *   - The plugin observes `compaction/summary`, whose payload carries
 *     `shadowedSeqs` / `shadowedRange` / `summary` (verified on 0.1.7 and 0.2.1),
 *     so it can reconstruct a *handle* to the exact raw events.
 *   - Nothing here touches DSH's own compaction. This layer is additive: if it
 *     throws, the caller keeps the legacy injection path.
 *
 * The three properties from billion-context are mapped as follows:
 *   incremental   -> claim-ledger: each raw seq is folded into at most one block;
 *                    a block whose whole range is already covered is skipped.
 *   reversible    -> the raw event text is stored beside the block (zstd on the
 *                    server), keyed by an opaque handle; `expand()` windows it
 *                    back under an explicit char budget.
 *   cache-friendly-> renderInjection() is append-only: the rendered text for
 *                    blocks[0..k] is a prefix of blocks[0..k+1] until the
 *                    budget floor advances monotonically (sticky floor).
 *
 * No LLM call is made here; the caller supplies the summary or injects a
 * `Summarizer` (see BLOCK_SUMMARY_PROMPT).
 */

import { createHash, randomUUID } from 'node:crypto'

export const COMPRESSOR_VERSION = 'proto-0.1.0'
export const DEFAULT_CONFIG = {
  summaryBudgetChars: 900,      // per-block summary ceiling (BC median ≈185 tokens ≈ 740 chars)
  injectionBudgetChars: 2600,   // legacy INJECT_BUDGET_CHARS; can be raised via server config
  maxSourceChars: 512 * 1024,   // per-block raw payload cap (zstd-compressed in the server)
  expandChunkChars: 8000,       // default expand window
  expandMaxChars: 20000,        // hard cap per expand call
  summarizeTimeoutMs: 20000,
  maxBlocksPerSession: 400,
  blockMapMaxShown: 8,          // BC's BLOCK_MAP_MAX_SHOWN
  tier2TriggerBlocks: 12,       // BC tier2Trigger default 1000 *blocks*; production tier-2 is rare
}

const sha256 = (s) => createHash('sha256').update(s, 'utf8').digest('hex')

/* ------------------------------------------------------------------ helpers */

/** Defensive extraction of a compaction/summary payload (0.1.7 & 0.2.1). */
export function readCompactionEvent(event) {
  const data = (event && event.data) || {}
  const summaryBlocks = data.summary
  let summaryText = ''
  if (Array.isArray(summaryBlocks)) {
    summaryText = summaryBlocks
      .filter((b) => b && typeof b === 'object' && b.type === 'text')
      .map((b) => b.text || '')
      .join('')
  } else if (typeof summaryBlocks === 'string') {
    summaryText = summaryBlocks
  }
  const seqs = Array.isArray(data.shadowedSeqs) ? data.shadowedSeqs.filter(Number.isInteger) : []
  const range = data.shadowedRange || {}
  return {
    seqs,
    range: Number.isInteger(range.start) ? { start: range.start, end: range.end } : null,
    summaryText,
    shadowedTokens: Number.isFinite(data.shadowedTokenCount) ? data.shadowedTokenCount : 0,
    compactionId: data.compactionId || null,
    provider: data.provider || null,
    model: data.model || null,
  }
}

/** True when a message event is a DSH compaction checkpoint (not raw history). */
export function isCheckpointEvent(event) {
  const d = (event && event.data) || {}
  const m = d.message || d
  const src = m && m.source
  if (!src || typeof src !== 'object') return false
  // 0.2.x stamps kind=compact-checkpoint; 0.1.7 (v3 logs) stamps plugin/compact.
  return src.kind === 'compact-checkpoint' ||
    (src.kind === 'plugin' && src.plugin === 'compact') ||
    (src.kind === 'plugin' && src.plugin === 'compaction')
}

/** Text of a message-producing event, or '' for log-only events. */
export function eventText(event) {
  const t = event && event.type
  const d = (event && event.data) || {}
  const blocksToText = (content) => {
    if (typeof content === 'string') return content
    if (!Array.isArray(content)) return ''
    return content
      .filter((b) => b && typeof b === 'object' && b.type === 'text')
      .map((b) => b.text || '')
      .join('\n')
  }
  if (t === 'tool/result') return blocksToText((d.message || {}).content)
  if (t === 'user/message' || t === 'assistant/message' || t === 'system/message' || t === 'developer/message') {
    const m = d.message || d
    return blocksToText(m.content)
  }
  if (t === 'tool/call') return String(d.arguments || '')
  return ''
}

/**
 * Split `shadowedSeqs` into raw seqs vs checkpoint seqs and drop anything the
 * ledger already covered. Pure: no IO. This is the incrementality guarantee.
 */
export function planIncrement({ seqs, isCheckpoint, covered }) {
  const coveredSet = covered instanceof Set ? covered : new Set(covered || [])
  const raw = []
  const checkpoint = []
  const refolded = []
  for (const seq of seqs) {
    if (isCheckpoint(seq)) {
      checkpoint.push(seq)
      continue
    }
    if (coveredSet.has(seq)) refolded.push(seq)
    else raw.push(seq)
  }
  return { raw, checkpoint, refolded }
}

/* --------------------------------------------------------------- stores */

/**
 * In-memory store used by the offline test. The production adapter is
 * `createHttpStore` (server-side tables in memory-server); the interface is
 * intentionally tiny so either can back the compressor.
 *
 * Contract:
 *   claimSeqs(sessionId, seqs) -> { claimed: number[], already: number[] }
 *        must be atomic with putBlock (unique index on (session_id, seq))
 *   putBlock(block) -> { inserted: boolean, block }
 *   getBlock(handle) -> block | null
 *   listBlocks(sessionId) -> block[]
 *   setFloorSeq(sessionId, seq) -> number   (monotonic max)
 *   getFloorSeq(sessionId) -> number
 *   stats(sessionId) -> { blocks, coveredSeqs, rawChars }
 */
export function createMemoryStore() {
  const blocks = new Map()
  const bySession = new Map()
  const covered = new Map()
  const floors = new Map()
  const coveredKey = (sid, seq) => sid + '\u0000' + seq
  return {
    async claimSeqs(sessionId, seqs) {
      const set = covered.get(sessionId) || new Set()
      const claimed = []
      const already = []
      for (const seq of seqs) {
        if (set.has(seq)) already.push(seq)
        else { set.add(seq); claimed.push(seq) }
      }
      covered.set(sessionId, set)
      return { claimed, already }
    },
    async putBlock(block) {
      for (const b of blocks.values()) {
        if (b.sessionId === block.sessionId && b.seqStart === block.seqStart &&
            b.seqEnd === block.seqEnd && b.sourceSha256 === block.sourceSha256) {
          return { inserted: false, block: b }
        }
      }
      blocks.set(block.handle, block)
      const list = bySession.get(block.sessionId) || []
      list.push(block)
      bySession.set(block.sessionId, list)
      return { inserted: true, block }
    },
    async getBlock(handle) { return blocks.get(handle) || null },
    async listBlocks(sessionId) { return (bySession.get(sessionId) || []).slice() },
    async setFloorSeq(sessionId, seq) {
      const next = Math.max(floors.get(sessionId) || 0, seq || 0)
      floors.set(sessionId, next)
      return next
    },
    async getFloorSeq(sessionId) { return floors.get(sessionId) || 0 },
    async stats(sessionId) {
      const list = bySession.get(sessionId) || []
      return {
        blocks: list.length,
        coveredSeqs: (covered.get(sessionId) || new Set()).size,
        rawChars: list.reduce((n, b) => n + (b.rawChars || 0), 0),
      }
    },
    _covered: covered,
  }
}

/**
 * Production adapter sketch (memory-server). Endpoints to add (all Bearer-token
 * protected, same auth as /v1/topic-summaries):
 *   POST /v1/compression/blocks        {session_id, workspace_id, seqs, ...block}
 *                                      -> {inserted, handle, claimed, already}
 *   GET  /v1/compression/blocks?session_id=&active=1
 *   GET  /v1/compression/blocks/:handle?cursor=&max_chars=   -> {text,next_cursor,total_chars}
 *   POST /v1/compression/blocks/:handle/deactivate
 *   GET  /v1/compression/floor?session_id=
 * The server zstd-compresses `source` and enforces size caps + GC; the client
 * never reads the session log again after `compress()`.
 */
export function createHttpStore({ baseUrl, token, fetchImpl = fetch, timeoutMs = 3000 }) {
  const call = async (method, path, body) => {
    const ac = new AbortController()
    const timer = setTimeout(() => ac.abort(), timeoutMs)
    try {
      const res = await fetchImpl(baseUrl.replace(/\/$/, '') + path, {
        method,
        headers: {
          'content-type': 'application/json',
          ...(token ? { authorization: 'Bearer ' + token } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ac.signal,
      })
      if (!res.ok) throw new Error('http ' + res.status)
      return await res.json()
    } finally {
      clearTimeout(timer)
    }
  }
  return {
    async claimSeqs(sessionId, seqs, payload) {
      const r = await call('POST', '/v1/compression/claim', { session_id: sessionId, seqs, payload })
      return { claimed: r.claimed || [], already: r.already || [] }
    },
    async putBlock(block) {
      const r = await call('POST', '/v1/compression/blocks', block)
      return { inserted: !!r.inserted, block: r.block || block }
    },
    async getBlock(handle) { return await call('GET', '/v1/compression/blocks/' + encodeURIComponent(handle)) },
    async listBlocks(sessionId) {
      const r = await call('GET', '/v1/compression/blocks?session_id=' + encodeURIComponent(sessionId))
      return r.blocks || []
    },
    async setFloorSeq(sessionId, seq) {
      const r = await call('POST', '/v1/compression/floor', { session_id: sessionId, seq })
      return r.seq || 0
    },
    async getFloorSeq(sessionId) {
      const r = await call('GET', '/v1/compression/floor?session_id=' + encodeURIComponent(sessionId))
      return r.seq || 0
    },
    async stats(sessionId) {
      const r = await call('GET', '/v1/compression/stats?session_id=' + encodeURIComponent(sessionId))
      return r
    },
  }
}

/* ----------------------------------------------------------- compressor */

export function createIncrementalCompressor({ store, config = {}, logger = console } = {}) {
  if (!store) throw new Error('compressor: store is required')
  const cfg = { ...DEFAULT_CONFIG, ...config }

  /**
   * compress() — fold one increment into one reversible block.
   *
   * @param {object} ctx
   * @param {string} ctx.sessionId   full session id (never the 12-char topic_id)
   * @param {string} [ctx.workspaceId]
   * @param {Array<object>} ctx.events  events whose seqs are in ctx.seqs, in order
   * @param {number[]} ctx.seqs      raw (non-checkpoint) seqs of this increment
   * @param {string} [ctx.summary]   caller-provided summary; otherwise summarizer
   * @param {object} [ctx.summarizer] { summarize({content,budgetChars,topic,timeoutMs}) }
   * @param {number} [ctx.budgetChars]
   * @param {string} [ctx.topic]
   * @returns {Promise<{ok:boolean, skipped?:string, handle?:string, summary?:string,
   *   reversible?:boolean, foldedTokensEst?:number, rawChars?:number, reason?:string}>}
   */
  async function compress(ctx) {
    try {
      if (!ctx || !ctx.sessionId) return { ok: false, reason: 'missing sessionId' }
      const seqs = Array.isArray(ctx.seqs) ? ctx.seqs.slice().sort((a, b) => a - b) : []
      if (seqs.length === 0) return { ok: true, skipped: 'no-new-content' }

      const before = await store.listBlocks(ctx.sessionId)
      const covered = new Set(before.flatMap((b) => b.coveredSeqs || []))
      const fresh = seqs.filter((s) => !covered.has(s))
      if (fresh.length === 0) {
        // Incrementality guard: all of this range was already folded.
        return { ok: true, skipped: 'already-covered', refolded: seqs.length }
      }

      const eventBySeq = new Map()
      for (const e of ctx.events || []) {
        if (Number.isInteger(e && e.seq)) eventBySeq.set(e.seq, e)
      }
      const parts = []
      for (const seq of fresh) {
        const ev = eventBySeq.get(seq)
        if (!ev) continue
        const text = eventText(ev)
        if (text) parts.push({ seq, type: ev.type, text })
      }
      if (parts.length === 0) return { ok: true, skipped: 'no-raw-content' }

      let content = parts.map((p) => '[seq=' + p.seq + ' ' + p.type + '] ' + p.text).join('\n\n')
      const truncated = content.length > cfg.maxSourceChars
      if (truncated) {
        content = content.slice(0, cfg.maxSourceChars) +
          '\n\n[compressor: source truncated at ' + cfg.maxSourceChars + ' chars]'
      }

      let summary = typeof ctx.summary === 'string' ? ctx.summary.trim() : ''
      if (!summary) {
        const summarizer = ctx.summarizer
        if (!summarizer || typeof summarizer.summarize !== 'function') {
          return { ok: false, reason: 'no-summary-and-no-summarizer' }
        }
        summary = String(await summarizer.summarize({
          content,
          budgetChars: ctx.budgetChars || cfg.summaryBudgetChars,
          topic: ctx.topic || '',
          timeoutMs: cfg.summarizeTimeoutMs,
        }) || '').trim()
      }
      if (!summary) return { ok: false, reason: 'empty-summary' }
      if (summary.length > (ctx.budgetChars || cfg.summaryBudgetChars)) {
        summary = summary.slice(0, ctx.budgetChars || cfg.summaryBudgetChars)
      }

      const seqStart = fresh[0]
      const seqEnd = fresh[fresh.length - 1]
      const handle = 'bh_' + sha256(ctx.sessionId + ':' + seqStart + ':' + seqEnd).slice(0, 20)
      const prev = before[before.length - 1]
      const block = {
        handle,
        blockId: before.length + 1,
        sessionId: ctx.sessionId,
        workspaceId: ctx.workspaceId || '',
        seqStart,
        seqEnd,
        coveredSeqs: fresh,
        topic: ctx.topic || '',
        summary,
        summaryTokens: estTokens(summary),
        rawChars: content.length,
        rawTokens: estTokens(content),
        sourceSha256: sha256(content),
        source: content,
        tier: 1,
        generation: 'young',
        active: true,
        createdAt: Date.now(),
        truncated,
        prevHandle: prev ? prev.handle : null,
      }

      // Claim first, then insert: a crash between the two can under-report but
      // never double-fold. Server-side both happen in one transaction.
      const claim = await store.claimSeqs(ctx.sessionId, fresh)
      if (claim.claimed.length !== fresh.length) {
        return { ok: true, skipped: 'race-lost', claimed: claim.claimed.length }
      }
      const put = await store.putBlock(block)
      if (!put.inserted) return { ok: true, skipped: 'duplicate-block' }
      if (before.length + 1 >= cfg.maxBlocksPerSession) {
        logger.warn && logger.warn('[compressor] block count near cap for ' + ctx.sessionId)
      }
      return {
        ok: true, handle, summary, reversible: true,
        foldedTokensEst: block.rawTokens, rawChars: block.rawChars,
        seqStart, seqEnd, truncated, tier: 1,
      }
    } catch (error) {
      // FAIL-OPEN: the caller must keep the legacy path on any failure.
      logger.warn && logger.warn('[compressor] compress failed (fail-open): ' + String(error && error.message || error))
      return { ok: false, reason: 'exception', error: String(error && error.message || error) }
    }
  }

  /**
   * expand(handle, {cursor, maxChars}) — read the raw source back out of the
   * store (never out of the session log). Paginated; safe on UTF-16 boundaries.
   */
  async function expand(handle, opts = {}) {
    try {
      const maxChars = Math.min(Number(opts.maxChars) || cfg.expandChunkChars, cfg.expandMaxChars)
      const start = Math.max(0, Number(opts.cursor) || 0)
      const block = await store.getBlock(handle)
      if (!block || typeof block.source !== 'string') {
        return {
          ok: false, unavailable: true,
          text: '[expand：句柄 ' + handle + ' 不存在或原文已过期；无法恢复该块原文。压缩摘要仍在注入中，可继续工作。]',
        }
      }
      const total = block.source.length
      let end = Math.min(total, start + maxChars)
      if (end < total && end > start) {
        const code = block.source.charCodeAt(end - 1)
        if (code >= 0xd800 && code <= 0xdbff) end -= 1 // do not split a surrogate pair
      }
      const text = block.source.slice(start, end)
      const nextCursor = end < total ? end : null
      const head = '[expand 句柄=' + handle + ' seq=' + block.seqStart + '–' + block.seqEnd +
        ' 原文 ' + start + '–' + end + ' / ' + total + ' 字符' +
        (block.truncated ? '（压缩时已截断，非完整原文）' : '') + ']\n'
      const tail = nextCursor === null
        ? '\n[expand 结束]'
        : '\n[expand 未完，续读 cursor=' + nextCursor + ']'
      return { ok: true, text: head + text + tail, nextCursor, totalChars: total, seqStart: block.seqStart, seqEnd: block.seqEnd }
    } catch (error) {
      logger.warn && logger.warn('[compressor] expand failed (fail-open): ' + String(error && error.message || error))
      return { ok: false, reason: 'exception', text: '[expand 暂时不可用：' + String(error && error.message || error) + ']' }
    }
  }

  /**
   * renderInjection() — append-only digest block for the memory injection.
   * Sticky floor: once a block is pushed out of the budget it never comes back,
   * so text(k) stays a prefix of text(k+1) between floor advances.
   */
  async function renderInjection(ctx = {}) {
    try {
      const sessionId = ctx.sessionId
      if (!sessionId) return { ok: false, text: '', reason: 'missing sessionId' }
      const budget = Number(ctx.budgetChars) || cfg.injectionBudgetChars
      const all = (ctx.blocks || await store.listBlocks(sessionId))
        .filter((b) => b && b.active !== false)
        .sort((a, b) => a.seqStart - b.seqStart)
      // mode='map': cheap block directory (BC's formatBlockMap / acp_status
      // pattern). Summaries only for blocks the current DSH checkpoint does NOT
      // already represent; older blocks stay reachable through expand().
      if (ctx.mode === 'map') {
        const coveredThrough = Number(ctx.checkpointCoveredThrough) || 0
        const shown = all.slice(-cfg.blockMapMaxShown)
        const hidden = all.length - shown.length
        const map = shown.map((b) => 'b' + b.blockId + '=' + b.seqStart + '–' + b.seqEnd).join(' · ')
        const fresh = all.filter((b) => b.seqEnd > coveredThrough)
        const freshLines = fresh.map((b) => '[摘要#b' + b.blockId + ' seq ' + b.seqStart + '–' + b.seqEnd + '] ' + b.summary)
        const header = '[历史脉络摘要·块目录]（checkpoint 已覆盖的只给目录；需要细节用 memory_call layer="expand" handle="bh_…"）\n'
        const mapLine = 'Active blocks (' + all.length + '): ' + (hidden > 0 ? '…+' + hidden + ' older · ' : '') + map
        // The directory is the load-bearing part: never let summaries evict it.
        let used = header.length + mapLine.length + 1
        const keptFresh = []
        for (let i = freshLines.length - 1; i >= 0; i -= 1) {
          if (used + freshLines[i].length + 1 > budget) break
          keptFresh.unshift(freshLines[i]); used += freshLines[i].length + 1
        }
        const text = header + (keptFresh.length ? keptFresh.join('\n') + '\n' : '') + mapLine + '\n'
        return { ok: true, text, kept: shown.length, omitted: hidden, freshShown: keptFresh.length,
          freshOmitted: freshLines.length - keptFresh.length,
          floor: await store.getFloorSeq(sessionId), chars: text.length, mode: 'map' }
      }
      const prevFloor = await store.getFloorSeq(sessionId)
      const kept = []
      let used = 0
      // Newest-first budget allocation, then render oldest-first.
      for (let i = all.length - 1; i >= 0; i -= 1) {
        const line = '[摘要#' + all[i].blockId + ' seq ' + all[i].seqStart + '–' + all[i].seqEnd + '] ' + all[i].summary
        if (all[i].seqStart <= prevFloor) break // sticky floor
        if (used + line.length > budget && kept.length > 0) break
        kept.push({ block: all[i], line })
        used += line.length + 1
      }
      kept.reverse()
      const dropped = all.slice(0, Math.max(0, all.length - kept.length))
      const floor = dropped.length ? Math.max(prevFloor, dropped[dropped.length - 1].seqStart) : prevFloor
      if (floor !== prevFloor) await store.setFloorSeq(sessionId, floor)
      const header = '[历史脉络摘要·增量块]（每块只覆盖一次原文；需要细节时 memory_call layer="expand"）\n'
      const body = kept.map((k) => k.line).join('\n')
      const text = kept.length ? header + body + '\n' : ''
      return { ok: true, text, kept: kept.length, omitted: dropped.length, floor, chars: text.length }
    } catch (error) {
      logger.warn && logger.warn('[compressor] renderInjection failed (fail-open): ' + String(error && error.message || error))
      return { ok: false, text: '', reason: 'exception' }
    }
  }

  async function status(sessionId) {
    try {
      const blocks = await store.listBlocks(sessionId)
      return {
        ok: true, sessionId, blocks: blocks.length,
        coveredSeqs: blocks.reduce((n, b) => n + (b.coveredSeqs || []).length, 0),
        rawChars: blocks.reduce((n, b) => n + (b.rawChars || 0), 0),
        summaryTokens: blocks.reduce((n, b) => n + (b.summaryTokens || 0), 0),
        activeBlocks: blocks.filter((b) => b.active !== false).length,
        floor: await store.getFloorSeq(sessionId),
      }
    } catch (error) {
      return { ok: false, reason: String(error && error.message || error) }
    }
  }

  return { name: 'incremental-v1', version: COMPRESSOR_VERSION, config: cfg, compress, expand, renderInjection, status }
}

/* ------------------------------------------------------------ summarizer */

/**
 * Prompt for the delta summarizer (adapted from billion-context's doctrine,
 * §4 summary-craft rules; the model only ever sees the increment).
 * Caller keeps the prompt versioned next to the compressor version.
 */
export const BLOCK_SUMMARY_PROMPT = `You are folding ONE consumed increment of a long coding conversation into a durable block summary.
Input: only the messages of this increment (each prefixed [seq=N type]). You cannot see the rest of the session; do not invent it.

Criterion: keep what a future reader needs to continue the task; drop what only mattered while producing it.
KEEP VERBATIM: full file paths with line numbers; signatures and load-bearing code lines; exact error strings; decisions WITH their rationale; exact values/versions/thresholds; user intent (short quotes).
DROP: verbose logs once the result is extracted; duplicate reads; the journey to a conclusion; dead ends (keep the lesson only).
Label recorded state as history: "TASK AS OF THIS BLOCK: ...". Never phrase it as a live instruction.
No percentages, no token counts, no commentary about the compression itself. Preserve the source language.
Budget: at most {budget} characters. Output plain text markdown, no JSON wrapper.`

export function estTokens(text) {
  if (!text) return 0
  const cjk = (String(text).match(/[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/g) || []).length
  const rest = String(text).length - cjk
  return cjk + Math.ceil(rest / 4)
}

export function newHandleSeed() { return randomUUID() }
