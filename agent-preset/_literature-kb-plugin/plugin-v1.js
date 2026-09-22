// dsh-literature agent 工具载体 + 轨 B 前提注入（v1.2）
// 工具：kb_query / kb_browse / kb_constraints / kb_contracts / kb_graph
//        + kb_archive_library / kb_browse(archived=true)
// 轨 B：system-prompt/assemble 时从 literature 拉 bias 知识 → [约束前提] 段；
//       source_memory_id 与 deepmemory 轨 A 同源行去重（抑制重复注入）。
//
// 【v1.2 关键修复 2026-09-16】
// 1) 相对 URL 换绝对 URL：本插件运行在 dsh-web 的 Node 进程内，`fetch('/lit-api/...')`
//    会在联网前被 undici 拒绝（Failed to parse URL）。/lit-api 只服务浏览器同源请求。
// 2) 服务基址与 token 一律由 systemd unit 显式注入，**禁止跨环境 fallback**：
//    若像原方案那样把「生产 token 路径 + 测试 token 路径」都列进兜底列表，
//    测试实例在自身 token 缺失时会命中【生产 token】+ 默认端口 6260 → 直写生产库。
// 3) 所有工具补 workspace_id 参数：此前 constraints/contracts/graph 连参数都没有，
//    服务端只能落到 UP.DEFAULT_WORKSPACE（幻影值 'deepseek-harness'）→ 永远失明；
//    archive 不传 ws 则必然因 domain 守卫报错。
// 4) 绝不发送 Origin 头：服务端带 Origin 一律 403（浏览器路径才需要代理）。

import fs from 'node:fs'
import { defineTool } from '/usr/local/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js'

export const name = 'dsh-literature'

export const inject = ['tools']

// ── 显式绑定（无兜底）──────────────────────────────────────────────────────
// unit 必须注入：
//   生产 dsh-web   LITERATURE_SERVER_PORT=6260 LITERATURE_API_TOKEN_FILE=/www/dsh/home/.dsh-literature-api-token
//   测试 dsh-test  LITERATURE_SERVER_PORT=6263 LITERATURE_API_TOKEN_FILE=/www/dsh-test-literature-server/data/api-token
const PORT = String(process.env.LITERATURE_SERVER_PORT || '').trim()
const API = PORT ? `http://127.0.0.1:${PORT}/v1/literature` : ''
const TOKEN_FILE = String(process.env.LITERATURE_API_TOKEN_FILE || '').trim()

function readToken() {
  if (!TOKEN_FILE) return ''
  try {
    return fs.readFileSync(TOKEN_FILE, 'utf8').trim()
  } catch {
    return ''
  }
}

/** 配置自检：返回错误描述或空串。**不抛异常**——插件异常会炸掉所有会话的每个回合。 */
function configProblem() {
  if (!API) return 'LITERATURE_SERVER_PORT 未注入（拒绝隐式默认端口，防误连生产）'
  if (!TOKEN_FILE) return 'LITERATURE_API_TOKEN_FILE 未注入（拒绝跨环境 token fallback）'
  if (!readToken()) return `token 文件不可读: ${TOKEN_FILE}`
  return ''
}

async function api(path, opts = {}) {
  const problem = configProblem()
  if (problem) throw new Error(`[literature] 配置错误：${problem}`)
  const { method = 'GET', body } = opts
  const res = await fetch(API + path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${readToken()}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`)
  return data
}

/** 写操作/按区查询必须显式给出 workspace_id（禁止兜底）。 */
const WS_PARAM = {
  type: 'string',
  description: '宿主工作区 UUID（按 DSH_SESSION_ID→storages/workspace.json 解析，勿用兜底值）。',
}

export function apply(ctx) {
  const outSchema = { type: 'object', additionalProperties: true }
  const textRender = (value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }]

  // 轨 B：bias 约束前提段注入（bias 为全局库，服务端空 ws 即返回全部 bias，无需 ws）
  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    // 瀑布链礼仪: 必须把下游结果(上游 await next() 依赖)原样返回, 且不吞下游异常
    const downstream = next ? await next(assembly, context) : undefined
    try {
      const bias = await api('/kb-search', {
        method: 'POST',
        body: { query: '约束 必须 禁止 绝对路径', k: 6, library: 'bias' },
      }).catch(() => null)
      const rows = (bias && bias.results) || []
      if (!rows.length) return downstream
      const text = rows.map((r) => `- ${r.summary || r.concept || ''}`).join('\n')
      // 0.1.5: PromptAssembly = {sections, contexts, tools, variables}，没有 push（旧代码恒假，从未注入）。
      // [约束前提] 的“预算外”指独立于 deepmemory L2/L3 记忆注入预算（plan-literature-side.md §1.4），
      // 不是系统提示预算：它是持久系统级约束，应进 sections（系统提示本体，policy 语义、前缀缓存友好）；
      // contexts 是对话流尾部逐回合重发的运行时快照（"supersedes earlier"），语义不符。
      // 遵循核心 dsh-agent 的瀑布范式：next() 返回值为权威，追加到其 sections 尾部后返回。
      if (downstream && Array.isArray(downstream.sections)
        && !downstream.sections.some((section) => section && section.name === 'literature:bias-constraints')) {
        return {
          ...downstream,
          sections: [...downstream.sections, {
            name: 'literature:bias-constraints',
            // 防 renderPrompt 严格插值：把完整的 {{...}} 组拆成全角，避免 bias 文本里的
            // 模板残留抛 unknown prompt variable 炸掉整个 assembly。
            text: `[约束前提]（来自 literature bias 库，均须遵守）：\n${text.replaceAll('{{', '\uFF5B\uFF5B')}`,
          }],
        }
      }
    } catch (e) { /* 轨 B 注入失败不影响主流程 */ }
    return downstream
  })

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_query',
    description: 'Query knowledge hybrid: literature knowledge vectors + deepmemory memories (RRF fused). mode: hybrid|knowledge-only|deepmemory-only|auto.',
    parameters: {
      query: { type: 'string', required: true, description: 'Concise search keywords.' },
      library: { type: 'string', description: 'bias | core | eco | project | runtime. Empty = all.' },
      k: { type: 'integer', description: 'Max results.', default: 5 },
      // 修正原描述「留空=不限」：服务端留空会兜底 DEFAULT_WORKSPACE（幻影值）导致本地知识全部失明
      workspace_id: { ...WS_PARAM, description: 'Workspace UUID。**建议总是显式传**：留空时服务端会落到默认工作区，本地知识将查不到。' },
      mode: { type: 'string', description: 'hybrid|knowledge-only|deepmemory-only|auto', default: 'auto' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
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
    description: 'Browse library catalog. archived=true to include archived libraries (requires workspace_id).',
    parameters: {
      library: { type: 'string', description: 'Optional single library.', default: '' },
      archived: { type: 'boolean', description: 'Include archived.', default: false },
      workspace_id: WS_PARAM,
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (args.archived) {
        // archived=true → 枚举本地 literature 归档知识（/knowledge-browse，本地权威）
        // 该端点按区过滤，必须显式传 ws，否则枚举不到任何东西
        if (!args.workspace_id) {
          return { ok: false, error: 'archived=true 需要显式 workspace_id（本地知识按工作区隔离）' }
        }
        const q = [`workspace_id=${encodeURIComponent(args.workspace_id)}`]
        if (args.library) q.push(`library=${encodeURIComponent(args.library)}`)
        q.push('archived=true')
        const data = await api(`/knowledge-browse?${q.join('&')}`)
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
    description: 'Fetch total behavior constraints (bias library). bias is global across workspaces.',
    parameters: {
      k: { type: 'integer', description: 'Max constraints.', default: 12 },
      workspace_id: { ...WS_PARAM, description: 'Optional. bias 为全局约束库，不传也可读全部。' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      // 此前不传 ws → 服务端兜底幻影 DEFAULT_WORKSPACE → 失明
      const q = [`k=${args.k || 12}`]
      if (args.workspace_id) q.push(`workspace_id=${encodeURIComponent(args.workspace_id)}`)
      const data = await api(`/kb/constraints?${q.join('&')}`)
      return { ok: true, count: data.count, constraints: data.constraints, note: data.note }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_contracts',
    description: 'Query core design/contract knowledge.',
    parameters: {
      topic: { type: 'string', description: 'Optional topic.', default: '' },
      k: { type: 'integer', description: 'Max results.', default: 10 },
      workspace_id: WS_PARAM,
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!args.workspace_id) {
        return { ok: false, error: 'kb_contracts 需要显式 workspace_id（知识按工作区隔离，服务端默认值不可依赖）' }
      }
      const q = [`workspace_id=${encodeURIComponent(args.workspace_id)}`, `k=${args.k || 10}`]
      if (args.topic) q.push(`topic=${encodeURIComponent(args.topic)}`)
      const data = await api(`/kb/contracts?${q.join('&')}`)
      return { ok: true, count: data.count, contracts: data.contracts }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_archive_library',
    description: 'Archive a whole library (move active items to archived). Requires explicit workspace_id; bias cannot be archived (server-side guard).',
    parameters: {
      library: { type: 'string', required: true, description: 'core | eco | project | runtime (bias rejected).' },
      reason: { type: 'string', description: 'Archive reason.', default: '' },
      // 此前缺该参数 → 服务端 domain 守卫必然拒绝（workspace_id is required）
      workspace_id: { ...WS_PARAM, description: '目标工作区 UUID（**必填**，服务端拒绝空值以防跨区批量归档）。' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      if (!args.workspace_id) {
        return { ok: false, error: 'kb_archive_library 需要显式 workspace_id（服务端默认隔离兜底会拒绝空值）' }
      }
      const body = { library: args.library, reason: args.reason || '', workspace_id: args.workspace_id }
      const data = await api('/archive-library', { method: 'POST', body })
      return { ok: true, archived: data.archived, count: data.count }
    },
  })))

  ctx.effect(() => ctx.tools.register(defineTool({
    name: 'kb_graph',
    description: 'Fetch the knowledge graph.',
    parameters: {
      workspace_id: WS_PARAM,
      library: { type: 'string', description: 'Optional single library.', default: '' },
    },
    output: { schema: outSchema, render: (args, value) => textRender(value) },
    async execute(args) {
      // 此前完全不传 ws，上游行为不明确；显式传递以对齐按区隔离语义
      const q = []
      if (args.workspace_id) q.push(`workspace_id=${encodeURIComponent(args.workspace_id)}`)
      if (args.library) q.push(`library=${encodeURIComponent(args.library)}`)
      const data = await api(`/kb/graph${q.length ? '?' + q.join('&') : ''}`)
      return { ok: true, graph: data.graph }
    },
  })))
}
