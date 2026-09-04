// dsh-literature agent 工具载体 + 轨 B 前提注入（v1.1 第 4 步）
// 工具：kb_query / kb_browse / kb_constraints / kb_contracts / kb_graph
//        + kb_archive_library(library, reason?) / kb_browse(archived=true)
// 轨 B：system-prompt/assemble 时从 literature(6260) 拉 bias 知识 → [约束前提] 段；
//       source_memory_id 与 deepmemory 轨 A 同源行去重（抑制重复注入）。
// 经 /lit-api 代理：/v1/literature/kb/*（6262 kb-server）、kb-search/knowledge-count（6260）。

import { defineTool } from '/usr/local/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js'

export const name = 'dsh-literature'

export const inject = ['tools']

const API = '/lit-api/v1/literature'

async function api(path, opts = {}) {
  const { method = 'GET', body } = opts
  const res = await fetch(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`)
  return data
}

export function apply(ctx) {
  const outSchema = { type: 'object', additionalProperties: true }
  const textRender = (value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }]

  // 轨 B：bias 约束前提段注入 —— systemPrompt.context（尾部持久快照，D3 同源机制；
  // 注：assemble 的 assembly 不是消息数组，push 永远无效——改用 context 注册）
  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt) {
    let kbText = ''
    let kbAt = 0
    async function refreshKbConstraints() {
      try {
        const r = await api('/kb/constraints?workspace_id=deepseek-harness&k=8')
        const rows = (r && r.constraints) || []
        kbText = rows.length
          ? '[约束前提]（来自 literature bias 库，均须遵守）：\n' +
            rows.map((x) => '- ' + String(x.concept || x.summary || x.content || '')).join('\n')
          : ''
        kbAt = Date.now()
      } catch (e) { /* 拉取失败保持旧文本 */ }
    }
    refreshKbConstraints().catch(() => {})
    ctx.effect(() => systemPrompt.context({
      name: 'literature-kb',
      order: 45,
      text: () => {
        if (Date.now() - kbAt > 300000) refreshKbConstraints().catch(() => {})
        return kbText || ''
      },
    }))
  }

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_query',
    description: 'Query knowledge hybrid: literature knowledge vectors + deepmemory memories (RRF fused). mode: hybrid|knowledge-only|deepmemory-only|auto.',
    parameters: {
      query: { type: 'string', required: true, description: 'Concise search keywords.' },
      library: { type: 'string', description: 'bias | core | eco | project | runtime. Empty = all.' },
      k: { type: 'integer', description: 'Max results.', default: 5 },
      workspace_id: { type: 'string', description: 'Workspace id.', default: 'deepseek-harness' },
      mode: { type: 'string', description: 'hybrid|knowledge-only|deepmemory-only|auto', default: 'auto' },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const body = { query: String(args.query || ''), k: args.k || 5, mode: args.mode || 'auto' }
      if (args.library) body.library = args.library
      if (args.workspace_id) body.workspace_id = args.workspace_id
      const data = await api('/kb/query', { method: 'POST', body })
      return { ok: true, count: data.count, mode: data.mode, knowledge_count: data.knowledge_count, results: data.results }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_browse',
    description: 'Browse library catalog. archived=true to include archived libraries.',
    parameters: {
      library: { type: 'string', description: 'Optional single library.', default: '' },
      archived: { type: 'boolean', description: 'Include archived.', default: false },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      if (args.archived) {
        // archived=true → 枚举本地 literature 归档知识（/knowledge-browse，本地权威）
        const q = []
        if (args.library) q.push(`library=${encodeURIComponent(args.library)}`)
        q.push('archived=true')
        const data = await api(`/knowledge-browse${q.length ? '?' + q.join('&') : ''}`)
        return { ok: true, items: data.items, archived: true }
      }
      const q = []
      if (args.library) q.push(`library=${encodeURIComponent(args.library)}`)
      const data = await api(`/kb/browse${q.length ? '?' + q.join('&') : ''}`)
      return { ok: true, libraries: data.libraries }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_constraints',
    description: 'Fetch total behavior constraints (bias library).',
    parameters: { k: { type: 'integer', description: 'Max constraints.', default: 12 } },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const data = await api(`/kb/constraints?k=${args.k || 12}`)
      return { ok: true, count: data.count, constraints: data.constraints, note: data.note }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_contracts',
    description: 'Query core design/contract knowledge.',
    parameters: {
      topic: { type: 'string', description: 'Optional topic.', default: '' },
      k: { type: 'integer', description: 'Max results.', default: 10 },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const q = args.topic ? `?topic=${encodeURIComponent(args.topic)}&k=${args.k || 10}` : `?k=${args.k || 10}`
      const data = await api(`/kb/contracts${q}`)
      return { ok: true, count: data.count, contracts: data.contracts }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_archive_library',
    description: 'Archive a whole library (move active items to archived). bias cannot be archived (server-side guard).',
    parameters: {
      library: { type: 'string', required: true, description: 'core | eco | project | runtime (bias rejected).' },
      reason: { type: 'string', description: 'Archive reason.', default: '' },
    },
    output: { schema: outSchema, render: textRender },
    async execute(args) {
      const body = { library: args.library, reason: args.reason || '' }
      const data = await api('/archive-library', { method: 'POST', body })
      return { ok: true, archived: data.archived, count: data.count }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_graph',
    description: 'Fetch the knowledge graph.',
    parameters: {},
    output: { schema: outSchema, render: textRender },
    async execute() {
      const data = await api('/kb/graph')
      return { ok: true, graph: data.graph }
    },
  })))
}
