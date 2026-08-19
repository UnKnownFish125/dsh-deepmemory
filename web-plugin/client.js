/**
 * deepmemory browser plugin — memory panel + schema-driven config page.
 * ESM source; installed via install.sh which converts it to __ModuleLoader__ format.
 */
import * as React from 'react'
const name = 'deepmemory'

const WORKSPACE_DEFAULT = 'deepseek-hardness'

const TYPE_ZH = { fact: '事实', preference: '偏好', decision: '决定', plan: '计划', episode: '事件' }
const TYPE_EN = { fact: 'fact', preference: 'preference', decision: 'decision', plan: 'plan', episode: 'episode' }

const I18N = {
  zh: {
    panelTitle: 'deepmemory 记忆控制', config: '配置', graph: '图谱', archive: '归档', maintain: '维护',
    back: '返回', refresh: '刷新', loading: '加载中…', search: '搜索', save: '保存', saved: '已保存',
    on: '已开启', off: '已关闭', memories: '记忆', stateCard: '工作区状态卡', noCard: '（无状态卡）',
    manual: '手动写入', newPlaceholder: '新的记忆内容…', retrieval: '检索', searchPlaceholder: '语义检索记忆…',
    globalSection: '公用记忆（全局）', localSection: '工作区 / 会话记忆', empty: '（暂无）', count: '条',
    importance: '重要性', delete: '删除', scopeTitle: '调整作用域', domainTitle: '调整域',
    session: '会话', workspace: '工作区', global: '全局', work: '工作', life: '生活',
    graphTitle: '实体图谱', graphHint: '节点为抽取实体，连线为关系；空心无连线时说明尚无关系边。',
    nodeCount: '节点', edgeCount: '关系', archiveTitle: '归档管理', archiveHint: '归档记忆不参与检索，可恢复或彻底删除。',
    restore: '恢复', noArchive: '（无归档记忆）', maintainTitle: '维护与备份',
    backup: '创建备份', backups: '备份列表', backupDocs: '记忆数', restoreBackup: '恢复', delBackup: '删除',
    rebuild: '重建索引', consolidate: '记忆整合', similarity: '相似度阈值', decayRun: '执行衰减',
    stats: '统计', atoms: '原子', nodes: '节点', edges: '关系', archived: '归档',
    enabled: '已开启', disabled: '已关闭', result: '结果', cfgTitle: 'deepmemory 配置（对齐 livingmemory 全量参数）',
    cfgSave: '保存全部', cfgSaving: '保存中…', cfgSaved: '已保存 N 项。行为类配置在下一轮对话生效。', cfgFail: '保存失败: ',
    lang: 'EN', loadFail: '加载失败: ', searchFail: '搜索失败: ', updateFail: '调整失败: ', adjustDone: '已调整',
    memOn: '记忆已开启', memOff: '记忆已关闭', stateV: '状态卡 v',
    source: '原文', noSource: '（无原文切片）来源会话: ',
    graphDragHint: '拖拽节点布局 · 滚轮缩放 · 空白处拖拽平移 · 悬停高亮关联', graphReset: '重置视图',
    cardEdit: '编辑', cardSave: '保存', cardCancel: '取消',
    sessionConfig: '会话配置', defaultConfig: '默认配置', override: '覆盖', reset: '重置', overridden: '已覆盖',
    sensitiveWarning: '此内容含敏感信息，默认脱敏', requestApproval: '请求授权', approving: '授权中…',
    expand: '展开原文', approvalGranted: '已授权（剩余N次）', approvalExpired: '授权已过期',
    tasks: '任务看板', taskTitle: '任务标题', taskDesc: '任务描述', taskStatus: '状态',
    taskCreate: '创建任务', taskEdit: '编辑', taskBlocked: '阻塞', taskUnblock: '解除阻塞',
    planned: '待规划', todo: '待办', inProgress: '进行中', completed: '已完成', failed: '已失败',
    blockReason: '阻塞原因', noTasks: '（无任务）', taskTransition: '流转', cardRevisions: '修订历史',
  },
  en: {
    panelTitle: 'deepmemory Memory', config: 'Config', graph: 'Graph', archive: 'Archive', maintain: 'Maintain',
    back: 'Back', refresh: 'Refresh', loading: 'Loading…', search: 'Search', save: 'Save', saved: 'Saved',
    on: 'Enabled', off: 'Disabled', memories: 'memories', stateCard: 'Workspace Card', noCard: '(no card)',
    manual: 'Manual Write', newPlaceholder: 'New memory…', retrieval: 'Retrieval', searchPlaceholder: 'Semantic search…',
    globalSection: 'Shared (global)', localSection: 'Workspace / Session', empty: '(empty)', count: 'items',
    importance: 'imp', delete: 'Delete', scopeTitle: 'Adjust scope', domainTitle: 'Adjust domain',
    session: 'session', workspace: 'workspace', global: 'global', work: 'work', life: 'life',
    graphTitle: 'Entity Graph', graphHint: 'Nodes are extracted entities, lines are relations.',
    nodeCount: 'nodes', edgeCount: 'edges', archiveTitle: 'Archive', archiveHint: 'Archived memories are excluded from retrieval; restore or delete permanently.',
    restore: 'Restore', noArchive: '(no archived memories)', maintainTitle: 'Maintenance & Backup',
    backup: 'Create backup', backups: 'Backups', backupDocs: 'docs', restoreBackup: 'Restore', delBackup: 'Delete',
    rebuild: 'Rebuild index', consolidate: 'Consolidate', similarity: 'Similarity', decayRun: 'Run decay',
    stats: 'Stats', atoms: 'atoms', nodes: 'nodes', edges: 'edges', archived: 'archived',
    enabled: 'Enabled', disabled: 'Disabled', result: 'Result', cfgTitle: 'deepmemory Config (livingmemory-aligned)',
    cfgSave: 'Save all', cfgSaving: 'Saving…', cfgSaved: 'Saved N items. Behavior config applies next turn.', cfgFail: 'Save failed: ',
    lang: '中文', loadFail: 'Load failed: ', searchFail: 'Search failed: ', updateFail: 'Update failed: ', adjustDone: 'Adjusted',
    memOn: 'Memory enabled', memOff: 'Memory disabled', stateV: 'card v',
    source: 'Source', noSource: '(no source slice) origin session: ',
    graphDragHint: 'Drag nodes · wheel zoom · drag canvas to pan · hover highlights links', graphReset: 'Reset view',
    cardEdit: 'Edit', cardSave: 'Save', cardCancel: 'Cancel',
    sessionConfig: 'Session Config', defaultConfig: 'Default Config', override: 'Override', reset: 'Reset', overridden: 'Overridden',
    sensitiveWarning: 'This content contains sensitive info, redacted by default', requestApproval: 'Request Authorization', approving: 'Authorizing…',
    expand: 'Expand Source', approvalGranted: 'Authorized (N remaining)', approvalExpired: 'Authorization expired',
    tasks: 'Task Board', taskTitle: 'Task Title', taskDesc: 'Description', taskStatus: 'Status',
    taskCreate: 'Create Task', taskEdit: 'Edit', taskBlocked: 'Blocked', taskUnblock: 'Unblock',
    planned: 'Planned', todo: 'To Do', inProgress: 'In Progress', completed: 'Completed', failed: 'Failed',
    blockReason: 'Block Reason', noTasks: '(no tasks)', taskTransition: 'Transition', cardRevisions: 'Revisions',
  },
}

const PANEL_CSS = `
.dsh-mem-panel { padding:16px 20px; display:flex; flex-direction:column; gap:12px; font-size:13px; max-width:1120px; }
.dsh-mem-panel .dsh-mem-title { margin:0; font-size:12px; opacity:.7; letter-spacing:.04em; }
.dsh-mem-box { border:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.35)); border-radius:8px; padding:10px; }
.dsh-mem-row { display:flex; gap:8px; align-items:flex-start; padding:7px 0; border-top:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.15)); }
.dsh-mem-row:first-child { border-top:none; }
.dsh-mem-content { flex:1; line-height:1.45; word-break:break-word; }
.dsh-mem-badge { font-size:11px; padding:1px 6px; border-radius:10px; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.2)); white-space:nowrap; }
.dsh-mem-imp { opacity:.65; font-size:11px; white-space:nowrap; }
.dsh-mem-input { flex:1; background:var(--dsw-alias-bg-layer-1, transparent); border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.4)); border-radius:6px; padding:6px 8px; color:var(--dsw-alias-label-primary, inherit); font:inherit; min-width:0; }
.dsh-mem-select { background:var(--dsw-alias-bg-layer-1, transparent); border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.4)); border-radius:6px; padding:5px 6px; color:var(--dsw-alias-label-primary, inherit); font:inherit; }
.dsh-mem-select option { background:var(--dsw-alias-bg-overlay, #1e1e1e); color:var(--dsw-alias-label-primary, #e8e8e8); }
.dsh-mem-mini { background:var(--dsw-alias-bg-layer-1, transparent); border:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.35)); border-radius:5px; padding:1px 4px; color:var(--dsw-alias-label-secondary, inherit); font-size:11px; }
.dsh-mem-mini option { background:var(--dsw-alias-bg-overlay, #1e1e1e); color:var(--dsw-alias-label-primary, #e8e8e8); }
.dsh-mem-btn { border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.45)); background:var(--dsw-alias-bg-layer-1, transparent); color:var(--dsw-alias-label-primary, inherit); border-radius:6px; padding:4px 12px; cursor:pointer; font:inherit; white-space:nowrap; }
.dsh-mem-btn:hover { background:var(--dsw-alias-bg-layer-2, rgba(128,128,128,.16)); }
.dsh-mem-btn-primary { border-color:var(--dsw-alias-brand-primary, #4c8dff); color:var(--dsw-alias-brand-primary, #4c8dff); }
.dsh-mem-del { color:#e05858; border:none; background:transparent; cursor:pointer; font-size:13px; padding:2px 4px; }
.dsh-mem-del:hover { text-decoration:underline; }
.dsh-mem-srcpre { margin:6px 0 0; padding:8px; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.08)); border:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.2)); border-radius:6px; font-size:11px; line-height:1.5; white-space:pre-wrap; word-break:break-word; max-height:240px; overflow:auto; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; opacity:.85; }
.dsh-mem-stats { display:flex; gap:14px; opacity:.8; flex-wrap:wrap; }
.dsh-mem-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.dsh-mem-meta { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.dsh-mem-form { display:flex; gap:8px; flex-wrap:wrap; }
.dsh-mem-section-head { display:flex; align-items:center; gap:8px; margin:0 0 4px; }
.dsh-mem-cfg-item { display:flex; flex-direction:column; gap:3px; padding:8px 0; border-top:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.12)); }
.dsh-mem-cfg-item:first-child { border-top:none; }
.dsh-mem-cfg-label { font-weight:500; }
.dsh-mem-cfg-hint { opacity:.65; font-size:11px; line-height:1.4; }
.dsh-mem-graph-svg { width:100%; min-height:520px; height:auto; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.06)); border-radius:8px; touch-action:none; }
.dsh-mem-graph-node { transition:opacity .14s, stroke-width .14s; }
.dsh-mem-graph-label { fill:var(--dsw-alias-label-primary, #e8e8e8); font-size:11px; paint-order:stroke; stroke:var(--dsw-alias-bg-layer-1, #17191c); stroke-width:3px; stroke-linejoin:round; pointer-events:none; }
.dsh-mem-graph-edge { stroke:var(--dsw-alias-border-l2, rgba(128,128,128,.5)); }
.dsh-mem-graph-legend { display:flex; align-items:center; gap:12px; flex-wrap:wrap; font-size:11px; opacity:.78; }
.dsh-mem-graph-key { display:inline-flex; align-items:center; gap:5px; white-space:nowrap; }
.dsh-mem-graph-dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
.dsh-mem-pcard { border:1px solid var(--dsw-alias-border-l2); background:var(--dsw-alias-bg-layer-3); border-radius:12px; list-style:none; transition:border-color .16s, background .16s; }
.dsh-mem-pcard:hover { border-color:var(--dsw-alias-label-dimmed); }
.dsh-mem-pcard-open { background:var(--dsw-alias-bg-layer-2); border-color:var(--dsw-alias-label-dimmed); }
.dsh-mem-pcard-header { appearance:none; width:100%; font:inherit; color:inherit; text-align:left; cursor:pointer; background:0 0; border:0; border-radius:12px; align-items:center; gap:12px; padding:14px 16px; display:flex; }
.dsh-mem-pcard-header:focus-visible { outline:2px solid var(--dsw-alias-brand-primary); outline-offset:-2px; }
.dsh-mem-pcard-headtext { flex-direction:column; flex:1; gap:4px; min-width:0; display:flex; }
.dsh-mem-pcard-name { color:var(--dsw-alias-label-primary); font-size:15px; font-weight:600; line-height:1.4; }
.dsh-mem-pcard-desc { color:var(--dsw-alias-label-tertiary); font-size:13px; line-height:1.5; }
.dsh-mem-pcard-chevron { color:var(--dsw-alias-label-tertiary); flex:none; font-size:12px; transition:transform .16s; }
.dsh-mem-pcard-chevron-open { transform:rotate(180deg); }
.dsh-mem-pcard-body { border-top:1px solid var(--dsw-alias-border-l2); margin:0 16px; padding-bottom:8px; }
.dsh-mem-pcard-group { margin-top:14px; }
.dsh-mem-pcard-grouptitle { color:var(--dsw-alias-label-secondary); font-size:12px; font-weight:600; letter-spacing:.04em; margin-bottom:2px; }
.dsh-mem-pcard-footer { border-top:1px solid var(--dsw-alias-border-l2); justify-content:flex-end; align-items:center; gap:8px; padding:12px 0 4px; margin-top:14px; display:flex; }
.dsh-mem-pcard-failed { min-width:0; color:var(--dsw-alias-label-secondary); flex:1; margin:0; font-size:12px; line-height:1.5; }
.dsh-mem-pcard-save { appearance:none; font:inherit; cursor:pointer; border:1px solid #0000; border-radius:8px; padding:5px 14px; font-size:13px; line-height:1.5; background:var(--dsw-alias-label-primary); color:var(--dsw-alias-bg-layer-3); }
.dsh-mem-pcard-save:disabled { opacity:.4; cursor:default; }
.dsh-mem-pcard-save:focus-visible { outline:2px solid var(--dsw-alias-brand-primary); outline-offset:1px; }
.dsh-mem-cfg-override { display:inline-flex; align-items:center; gap:4px; font-size:11px; opacity:.7; margin-left:8px; }
.dsh-mem-sensitive-box { background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.08)); border:1px solid rgba(255,140,0,.4); border-radius:6px; padding:8px; margin:6px 0; }
.dsh-mem-task-panel { min-width:0; }
.dsh-mem-task-toolbar { display:flex; align-items:center; gap:8px; padding-bottom:14px; border-bottom:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.14)); margin-bottom:12px; }
.dsh-mem-task-toolbar .dsh-mem-title { font-size:15px; }
.dsh-mem-task-toolbar-meta { display:flex; gap:10px; color:var(--dsw-alias-label-tertiary, #777b82); font-size:11px; margin-left:4px; }
.dsh-mem-task-board { display:grid; grid-template-columns:repeat(5, minmax(210px, 1fr)); gap:8px; overflow-x:auto; align-items:stretch; padding:2px 0 10px; scrollbar-width:thin; }
.dsh-mem-task-column { min-width:210px; min-height:360px; padding:7px; border:0; border-radius:8px; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.035)); }
.dsh-mem-task-column-title { min-height:38px; display:flex; justify-content:space-between; align-items:center; padding:4px 7px 10px; color:var(--dsw-alias-label-secondary, #a3a6ac); font-size:12px; font-weight:500; }
.dsh-mem-task-column-title .dsh-mem-badge { border:0; background:transparent; color:var(--dsw-alias-label-tertiary, #6f737b); padding:0; }
.dsh-mem-task-card { position:relative; overflow:hidden; border:1px solid var(--dsw-alias-border-l2, rgba(128,128,128,.18)); border-radius:7px; padding:0; margin-bottom:7px; background:var(--dsw-alias-bg-layer-2, rgba(30,31,35,.72)); transition:border-color .15s, background .15s; }
.dsh-mem-task-card:hover, .dsh-mem-task-card[open] { border-color:var(--dsw-alias-label-dimmed, rgba(128,128,128,.45)); background:var(--dsw-alias-bg-layer-3, rgba(38,39,44,.9)); }
.dsh-mem-task-card summary { position:relative; cursor:pointer; list-style:none; min-height:44px; padding:11px 12px 11px 16px; color:var(--dsw-alias-label-primary, #e5e5e5); font-size:13px; line-height:1.45; }
.dsh-mem-task-card summary::-webkit-details-marker { display:none; }
.dsh-mem-task-priority { position:absolute; z-index:1; inset:10px auto 10px 0; width:3px; border-radius:0 2px 2px 0; }
.dsh-mem-task-detail { border-top:1px solid var(--dsw-alias-border-l1, rgba(128,128,128,.12)); padding:10px 12px 12px; font-size:12px; }
.dsh-mem-task-description { white-space:pre-wrap; color:var(--dsw-alias-label-secondary, #a3a6ac); line-height:1.55; margin-bottom:10px; }
.dsh-mem-task-controls { display:flex; flex-wrap:wrap; gap:5px; margin-top:9px; }
.dsh-mem-task-color-label { display:flex; align-items:center; gap:6px; color:var(--dsw-alias-label-tertiary, #777b82); }
.dsh-mem-task-color-label select { max-width:90px; }
.dsh-mem-task-blocked { display:inline-flex; color:#d16a63; border:1px solid rgba(209,106,99,.35); border-radius:4px; padding:2px 6px; margin-bottom:7px; font-size:11px; }
.dsh-mem-task-empty { color:var(--dsw-alias-label-tertiary, #696d74); font-size:12px; padding:9px 7px; }
.dsh-mem-memory-editor { width:100%; min-height:72px; resize:vertical; }
.dsh-mem-memory-edit-actions { display:flex; gap:6px; justify-content:flex-end; }
@media (max-width:760px) { .dsh-mem-task-board { grid-template-columns:repeat(5, minmax(82vw, 82vw)); } .dsh-mem-task-column { min-width:82vw; } }

`

async function api(method, path, body) {
  try {
    const opts = { method, headers: { 'Content-Type': 'application/json' } }
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body)
    const res = await fetch('/mem-api' + path, opts)
    if (!res.ok) return { error: 'HTTP ' + res.status }
    return await res.json()
  } catch (e) {
    return { error: String((e && e.message) || e) }
  }
}

export function apply(ctx) {
  const slots = ctx.get('slots')
  if (slots === undefined) return

  const styleEl = document.createElement('style')
  styleEl.dataset.plugin = 'deepmemory'
  styleEl.textContent = PANEL_CSS
  document.head.appendChild(styleEl)

  function graphImportanceColor(value) {
    const importance = Number(value == null ? 0.5 : value)
    if (importance >= 0.85) return '#d85b4b'
    if (importance >= 0.65) return '#d28b36'
    if (importance >= 0.45) return '#4f9870'
    return '#7b838d'
  }

  function buildGraphLayout(nodes, edges, width, height) {
    const count = nodes.length
    const cx = width / 2, cy = height / 2
    const byId = new Map()
    const positions = []
    const degree = {}
    edges.forEach(function (edge) {
      degree[edge.source_id] = (degree[edge.source_id] || 0) + 1
      degree[edge.target_id] = (degree[edge.target_id] || 0) + 1
    })
    nodes.forEach(function (node, index) {
      const angle = index * 2.399963229728653
      const spread = Math.min(width, height) * 0.42 * Math.sqrt((index + 1) / Math.max(1, count))
      const item = {
        id: node.id,
        x: cx + Math.cos(angle) * spread,
        y: cy + Math.sin(angle) * spread,
        vx: 0,
        vy: 0,
        radius: 5 + Math.min(8, Math.sqrt(degree[node.id] || 0) * 2) + Number(node.importance || 0.5) * 5,
      }
      positions.push(item)
      byId.set(String(node.id), item)
    })
    const iterations = count > 320 ? 70 : count > 160 ? 100 : 140
    for (let tick = 0; tick < iterations; tick += 1) {
      const cooling = 1 - tick / iterations
      for (let i = 0; i < count; i += 1) {
        const a = positions[i]
        for (let j = i + 1; j < count; j += 1) {
          const b = positions[j]
          let dx = b.x - a.x, dy = b.y - a.y
          let distance2 = dx * dx + dy * dy
          if (distance2 < 1) { dx = ((i + 1) * 17 % 11) - 5; dy = ((j + 1) * 13 % 11) - 5; distance2 = dx * dx + dy * dy || 1 }
          const distance = Math.sqrt(distance2)
          const minimum = a.radius + b.radius + 13
          const repel = Math.min(2.8, 820 / distance2) + (distance < minimum ? (minimum - distance) * 0.08 : 0)
          const fx = dx / distance * repel, fy = dy / distance * repel
          a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy
        }
      }
      edges.forEach(function (edge) {
        const a = byId.get(String(edge.source_id)), b = byId.get(String(edge.target_id))
        if (!a || !b) return
        const dx = b.x - a.x, dy = b.y - a.y
        const distance = Math.sqrt(dx * dx + dy * dy) || 1
        const target = 78 + Math.min(30, (a.radius + b.radius) * 1.2)
        const pull = (distance - target) * 0.008
        const fx = dx / distance * pull, fy = dy / distance * pull
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
      })
      positions.forEach(function (item) {
        item.vx += (cx - item.x) * 0.0009
        item.vy += (cy - item.y) * 0.0009
        item.vx *= 0.72; item.vy *= 0.72
        item.x += item.vx * (0.7 + cooling * 0.6)
        item.y += item.vy * (0.7 + cooling * 0.6)
        const margin = item.radius + 24
        item.x = Math.max(margin, Math.min(width - margin, item.x))
        item.y = Math.max(margin, Math.min(height - margin, item.y))
      })
    }
    const result = {}
    positions.forEach(function (item) { result[item.id] = [item.x, item.y] })
    return result
  }

  function GraphView(props) {
    const t = props.t
    const lang = props.lang
    const [data, setData] = React.useState(null)
    const [selected, setSelected] = React.useState(null)
    const [linked, setLinked] = React.useState(null)
    const [hover, setHover] = React.useState(null)
    const [pos, setPos] = React.useState({})
    const [layoutVersion, setLayoutVersion] = React.useState(0)
    const [zoom, setZoom] = React.useState(1)
    const [pan, setPan] = React.useState({ x: 0, y: 0 })
    const drag = React.useRef(null)
    async function load() {
      const g = await api('GET', '/v1/graph')
      if (g && Array.isArray(g.nodes)) { setData(g); setPos({}); setLayoutVersion(function (v) { return v + 1 }) }
    }
    React.useEffect(function () { load() }, [])
    async function selectNode(n) {
      if (selected && selected.id === n.id) { setSelected(null); setLinked(null); return }
      setSelected(n)
      setLinked(null)
      const res = await api('GET', '/v1/graph/memories?entity=' + encodeURIComponent(n.name))
      if (res && Array.isArray(res.memories)) setLinked(res.memories)
    }
    const nodes = data && Array.isArray(data.nodes) ? data.nodes : []
    const edges = data && Array.isArray(data.edges) ? data.edges : []
    const W = 920, H = 560
    const deg = {}
    edges.forEach(function (e) {
      deg[e.source_id] = (deg[e.source_id] || 0) + 1
      deg[e.target_id] = (deg[e.target_id] || 0) + 1
    })
    const base = React.useMemo(function () { return buildGraphLayout(nodes, edges, W, H) }, [data, layoutVersion])
    if (!data) return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { style: { opacity: .6 } }, t('loading')))
    const getPos = function (nid) { return pos[nid] || base[nid] }
    const radiusOf = function (n) {
      return 5 + Math.min(8, Math.sqrt(deg[n.id] || Number(n.edge_count) || 0) * 2) + Number(n.importance || 0.5) * 5
    }
    let neighbors = null
    if (hover !== null) {
      neighbors = new Set([hover])
      edges.forEach(function (e) {
        if (e.source_id === hover) neighbors.add(e.target_id)
        if (e.target_id === hover) neighbors.add(e.source_id)
      })
    }
    const id2name = {}
    nodes.forEach(function (n) { id2name[n.id] = n.name })
    function onNodeDown(e, n) {
      e.stopPropagation()
      drag.current = { kind: 'node', id: n.id, sx: e.clientX, sy: e.clientY, ox: getPos(n.id)[0], oy: getPos(n.id)[1], moved: false }
    }
    function onBgDown(e) {
      if (e.target && e.target.tagName && e.target.tagName.toLowerCase() === 'circle') return
      drag.current = { kind: 'pan', sx: e.clientX, sy: e.clientY, ox: pan.x, oy: pan.y, moved: false }
    }
    function onMove(e) {
      const d = drag.current
      if (!d) return
      const dx = (e.clientX - d.sx) / zoom
      const dy = (e.clientY - d.sy) / zoom
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
      if (!d.moved) return
      if (d.kind === 'node') {
        setPos(function (prev) {
          const next = Object.assign({}, prev)
          next[d.id] = [d.ox + dx, d.oy + dy]
          return next
        })
      } else if (d.kind === 'pan') {
        setPan({ x: d.ox + dx * zoom, y: d.oy + dy * zoom })
      }
    }
    function onUp() {
      const d = drag.current
      drag.current = null
      if (d && d.kind === 'node' && !d.moved) {
        const n = nodes.find(function (x) { return String(x.id) === String(d.id) })
        if (n) selectNode(n)
      }
    }
    function onWheel(e) {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.12 : 0.89
      setZoom(function (z) { return Math.min(4, Math.max(0.42, z * factor)) })
    }
    const edgeEls = edges.map(function (e, i) {
      const a = getPos(e.source_id), b = getPos(e.target_id)
      if (!a || !b) return null
      const hot = hover === null || hover === e.source_id || hover === e.target_id
      return React.createElement('line', {
        key: 'e' + i,
        className: 'dsh-mem-graph-edge',
        x1: a[0], y1: a[1], x2: b[0], y2: b[1],
        opacity: hot ? (hover === null ? 0.22 : 0.82) : 0.045,
        strokeWidth: hot && hover !== null ? 1.5 : 0.8,
      }, React.createElement('title', null, (id2name[e.source_id] || '?') + ' → ' + (e.relation || '') + ' → ' + (id2name[e.target_id] || '?')))
    }).filter(Boolean)
    const typeLabels = lang === 'en' ? TYPE_EN : TYPE_ZH
    const nodeEls = nodes.map(function (n) {
      const p = getPos(n.id)
      if (!p) return null
      const isSel = selected && selected.id === n.id
      const dim = neighbors !== null && !neighbors.has(n.id)
      const hot = hover === n.id
      const importance = Number(n.importance == null ? 0.5 : n.importance)
      const showLabel = hot || isSel || importance >= 0.65 || (deg[n.id] || 0) >= 5 || zoom >= 1.55
      const tooltip = n.name + ' · ' + t('importance') + ' ' + importance.toFixed(2) + ' · ' + (deg[n.id] || 0) + ' ' + t('edgeCount')
      return React.createElement('g', {
        key: 'n' + n.id,
        style: { cursor: 'grab' },
        onMouseDown: function (e) { onNodeDown(e, n) },
        onMouseEnter: function () { setHover(n.id) },
        onMouseLeave: function () { setHover(null) },
      },
        React.createElement('title', null, tooltip),
        React.createElement('circle', {
          className: 'dsh-mem-graph-node', cx: p[0], cy: p[1], r: radiusOf(n),
          fill: graphImportanceColor(importance),
          opacity: dim ? 0.12 : (importance < 0.45 ? 0.62 : 0.94),
          stroke: hot ? graphImportanceColor(importance) : (isSel ? 'var(--dsw-alias-label-primary, #fff)' : 'rgba(255,255,255,.28)'),
          strokeWidth: (hot || isSel) ? 2.6 : 0.7,
        }),
        showLabel ? React.createElement('text', {
          className: 'dsh-mem-graph-label', x: p[0] + radiusOf(n) + 5, y: p[1] + 4, textAnchor: 'start',
          fontWeight: (isSel || hot || importance >= 0.85) ? 700 : 500,
          opacity: dim ? 0.12 : (importance < 0.45 ? 0.72 : 1),
        }, String(n.name).slice(0, 18)) : null,
      )
    })
    const legend = lang === 'en'
      ? [['Low < .45', '#7b838d'], ['Medium .45–.64', '#4f9870'], ['High .65–.84', '#d28b36'], ['Critical ≥ .85', '#d85b4b']]
      : [['较低 < 0.45', '#7b838d'], ['一般 0.45–0.64', '#4f9870'], ['重要 0.65–0.84', '#d28b36'], ['核心 ≥ 0.85', '#d85b4b']]
    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('graphTitle')),
        React.createElement('span', { className: 'dsh-mem-imp' }, t('graphDragHint')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setPos({}); setPan({ x: 0, y: 0 }); setZoom(1); setLayoutVersion(function (v) { return v + 1 }) } }, t('graphReset')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: props.onBack }, t('back')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: load }, t('refresh')),
      ),
      React.createElement('div', { className: 'dsh-mem-stats' },
        React.createElement('span', null, t('nodeCount') + ' ' + nodes.length),
        React.createElement('span', null, t('edgeCount') + ' ' + edges.length),
        React.createElement('span', { className: 'dsh-mem-graph-legend' }, legend.map(function (item) {
          return React.createElement('span', { key: item[0], className: 'dsh-mem-graph-key' },
            React.createElement('i', { className: 'dsh-mem-graph-dot', style: { background: item[1] } }), item[0])
        })),
      ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('graphHint')),
        nodes.length
          ? React.createElement('svg', {
            className: 'dsh-mem-graph-svg', viewBox: '0 0 ' + W + ' ' + H,
            onMouseDown: onBgDown, onMouseMove: onMove, onMouseUp: onUp, onMouseLeave: onUp, onWheel: onWheel,
            style: { cursor: drag.current && drag.current.kind === 'pan' ? 'grabbing' : 'default' },
          },
            React.createElement('g', { transform: 'translate(' + pan.x + ' ' + pan.y + ') scale(' + zoom + ')' },
              edgeEls, nodeEls))
          : React.createElement('div', { style: { opacity: .6 } }, t('empty')),
      ),
      selected ? React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-section-head' },
          React.createElement('span', { className: 'dsh-mem-title' }, '「' + selected.name + '」关联记忆'),
          linked ? React.createElement('span', { className: 'dsh-mem-imp' }, linked.length + ' ' + t('count')) : null,
        ),
        linked === null
          ? React.createElement('div', { style: { opacity: .6 } }, t('loading'))
          : linked.length
            ? linked.map(function (m) {
              return React.createElement('div', { key: String(m.id), className: 'dsh-mem-row' },
                React.createElement('span', { className: 'dsh-mem-badge' }, typeLabels[m.type] || m.type),
                React.createElement('span', { className: 'dsh-mem-content' }, m.content),
                React.createElement('span', { className: 'dsh-mem-imp' }, Number(m.importance || 0.5).toFixed(2)),
              )
            })
            : React.createElement('div', { style: { opacity: .6 } }, t('empty')),
      ) : null,
    )
  }

  function ArchiveView(props) {
    const t = props.t
    const [items, setItems] = React.useState([])
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState('')
    async function load() {
      setBusy(true)
      const res = await api('GET', '/v1/memories/list?status=archived&limit=200')
      if (res && Array.isArray(res.memories)) setItems(res.memories)
      else if (res && res.error) setMsg(t('loadFail') + res.error)
      setBusy(false)
    }
    React.useEffect(function () { load() }, [])
    async function restore(id) {
      await api('POST', '/v1/memories/restore', { id: Number(id) })
      load()
    }
    async function remove(id) {
      await api('DELETE', '/v1/memories/' + encodeURIComponent(String(id)))
      load()
    }
    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('archiveTitle')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: props.onBack }, t('back')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: load, disabled: busy }, t('refresh')),
      ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('archiveHint')),
        items.length
          ? items.map(function (m) {
            return React.createElement('div', { key: String(m.id), className: 'dsh-mem-row' },
              React.createElement('span', { className: 'dsh-mem-content' }, m.content),
              React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { restore(m.id) } }, t('restore')),
              React.createElement('button', { className: 'dsh-mem-del', onClick: function () { remove(m.id) } }, '✕'),
            )
          })
          : React.createElement('div', { style: { opacity: .6 } }, t('noArchive')),
      ),
      msg ? React.createElement('div', { style: { opacity: .7 } }, msg) : null,
    )
  }

  function MaintenanceView(props) {
    const t = props.t
    const [backups, setBackups] = React.useState([])
    const [sim, setSim] = React.useState('0.92')
    const [msg, setMsg] = React.useState('')
    async function load() {
      const b = await api('GET', '/v1/backups/list')
      if (b && Array.isArray(b.backups)) setBackups(b.backups)
    }
    React.useEffect(function () { load() }, [])
    function result(res) {
      setMsg(t('result') + ': ' + JSON.stringify(res))
      load()
    }
    async function doBackup() { result(await api('POST', '/v1/backups/create', {})) }
    async function doRestore(name) { result(await api('POST', '/v1/backups/restore', { name: name })) }
    async function doDelBackup(name) { result(await api('DELETE', '/v1/backups/' + encodeURIComponent(name))) }
    async function doRebuild() { result(await api('POST', '/v1/maintenance/rebuild', {})) }
    async function doConsolidate() {
      const s = parseFloat(sim)
      result(await api('POST', '/v1/maintenance/consolidate', { similarity: Number.isFinite(s) ? s : 0.92, limit_groups: 5 }))
    }
    async function doDecay() { result(await api('POST', '/v1/maintenance/decay', { force: true })) }
    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('maintainTitle')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: props.onBack }, t('back')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: load }, t('refresh')),
      ),
      msg ? React.createElement('div', { style: { opacity: .75 } }, msg) : null,
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('backups')),
        React.createElement('div', { className: 'dsh-mem-form' },
          React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', onClick: doBackup }, t('backup')),
        ),
        backups.length
          ? backups.map(function (b) {
            return React.createElement('div', { key: b.name, className: 'dsh-mem-row' },
              React.createElement('span', { className: 'dsh-mem-content' }, b.name + ' · ' + t('backupDocs') + ' ' + b.documents),
              React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { doRestore(b.name) } }, t('restoreBackup')),
              React.createElement('button', { className: 'dsh-mem-del', onClick: function () { doDelBackup(b.name) } }, '✕'),
            )
          })
          : React.createElement('div', { style: { opacity: .6 } }, t('empty')),
      ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('maintainTitle')),
        React.createElement('div', { className: 'dsh-mem-form' },
          React.createElement('button', { className: 'dsh-mem-btn', onClick: doRebuild }, t('rebuild')),
          React.createElement('input', { className: 'dsh-mem-input', style: { maxWidth: 90 }, value: sim, title: t('similarity'), onChange: function (e) { setSim(e.target.value) } }),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: doConsolidate }, t('consolidate')),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: doDecay }, t('decayRun')),
        ),
      ),
    )
  }

  function ConfigView(props) {
    // 插件配置卡片：注册在 设置 → 插件 → 插件配置（web-ui.plugin.item）
    const t = props.t
    const sessionId = props.sessionId || ''
    const [schema, setSchema] = React.useState(null)
    const [values, setValues] = React.useState({})
    const [loaded, setLoaded] = React.useState(false)
    const [overrides, setOverrides] = React.useState([])
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState('')

    async function load() {
      const sres = await api('GET', '/v1/config-schema')
      const vres = sessionId ? await api('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId)) : await api('GET', '/v1/config')
      setOverrides(sessionId && vres.overrides ? vres.overrides : [])
      if (sres && sres.schema) setSchema(sres.schema)
      if (vres && vres.config) setValues(vres.config)
      setLoaded(true)
    }

    React.useEffect(function () { load() }, [])

    async function save() {
      setBusy(true)
      const res = sessionId
        ? await Promise.all(Object.keys(values).map(function (key) { return api('POST', '/v1/config/session/set', { session_id: sessionId, key: key, value: values[key] }) })).then(function (items) { const failed = items.find(function (item) { return item && item.error }); return failed ? { error: failed.error } : { saved: items.length } })
        : await api('POST', '/v1/config', values)
      setBusy(false)
      if (res && res.error) { setMsg(t('cfgFail') + res.error); return }
      setMsg(t('cfgSaved').replace('N', String(res ? res.saved : 0)))
    }

    function setValue(key, v) {
      setValues(function (prev) { const next = Object.assign({}, prev); next[key] = v; return next })
    }

    async function resetValue(key) {
      const res = await api('POST', '/v1/config/session/reset', { session_id: sessionId, key: key })
      if (res && res.error) { setMsg(t('cfgFail') + res.error); return }
      const fresh = await api('GET', '/v1/config/session?session_id=' + encodeURIComponent(sessionId))
      if (fresh && fresh.config) setValues(fresh.config)
      setOverrides(fresh && fresh.overrides ? fresh.overrides : [])
    }

    function fieldDefault(spec) {
      if (spec && spec.default !== undefined) return spec.default
      if (spec && spec.type === 'bool') return false
      if (spec && (spec.type === 'int' || spec.type === 'float')) return 0
      return ''
    }

    function renderField(key, spec) {
      const val = values[key] !== undefined ? values[key] : fieldDefault(spec)
      if (spec.options && spec.options.length) {
        return React.createElement('select', {
          className: 'dsh-mem-select', style: { width: '100%' },
          value: String(val),
          onChange: function (e) { setValue(key, e.target.value) },
        }, spec.options.map(function (o) { return React.createElement('option', { key: o, value: o }, o) }))
      }
      if (spec.type === 'bool') {
        return React.createElement('select', {
          className: 'dsh-mem-select', style: { width: '100%' },
          value: val ? 'true' : 'false',
          onChange: function (e) { setValue(key, e.target.value === 'true') },
        },
          React.createElement('option', { value: 'true' }, t('on')),
          React.createElement('option', { value: 'false' }, t('off')),
        )
      }
      if (spec.type === 'text') {
        return React.createElement('textarea', {
          className: 'dsh-mem-input', style: { width: '100%', minHeight: 60, resize: 'vertical' },
          value: String(val),
          onChange: function (e) { setValue(key, e.target.value) },
        })
      }
      return React.createElement('input', {
        className: 'dsh-mem-input', style: { width: '100%' },
        type: (spec.type === 'int' || spec.type === 'float') ? 'number' : 'text',
        step: spec.type === 'float' ? 'any' : undefined,
        value: String(val),
        onChange: function (e) { setValue(key, (spec.type === 'int' || spec.type === 'float') ? Number(e.target.value) : e.target.value) },
      })
    }

    const groups = []
    if (schema) {
      for (const gname of Object.keys(schema)) {
        const g = schema[gname]
        const items = (g && g.items) || {}
        const fields = []
        for (const fname of Object.keys(items)) {
          fields.push({ key: gname + '.' + fname, spec: items[fname] })
        }
        if (fields.length) groups.push({ name: gname, title: (g && g.description) || gname, fields: fields })
      }
    }

    const [open, setOpen] = React.useState(!props.embedded)
    const collapsible = !!props.embedded

    // 官方 PluginCard 风格（设置 → 插件卡片）：header 点击展开，chevron 旋转，
    // 字段分组小标题平铺，底部保存按钮。
    if (collapsible) {
      return React.createElement('li', {
        className: 'dsh-mem-pcard' + (open ? ' dsh-mem-pcard-open' : ''),
        style: { listStyle: 'none' },
      },
        React.createElement('button', {
          type: 'button',
          className: 'dsh-mem-pcard-header',
          'aria-expanded': open,
          onClick: function () { setOpen(!open) },
        },
          React.createElement('span', { className: 'dsh-mem-pcard-headtext' },
            React.createElement('span', { className: 'dsh-mem-pcard-name' }, 'deepmemory'),
            React.createElement('span', { className: 'dsh-mem-pcard-desc' }, t('cfgTitle')),
          ),
          React.createElement('span', { className: 'dsh-mem-pcard-chevron' + (open ? ' dsh-mem-pcard-chevron-open' : '') }, '▾'),
        ),
        open
          ? React.createElement('div', { className: 'dsh-mem-pcard-body' },
            loaded
              ? groups.map(function (g) {
                return React.createElement('div', { key: g.name, className: 'dsh-mem-pcard-group' },
                  React.createElement('div', { className: 'dsh-mem-pcard-grouptitle' }, g.title),
                  g.fields.map(function (f) {
                    return React.createElement('div', { key: f.key, className: 'dsh-mem-cfg-item' },
                      React.createElement('span', { className: 'dsh-mem-cfg-label' }, f.spec.description || f.key,
                        sessionId && overrides.indexOf(f.key) >= 0 ? React.createElement('button', { className: 'dsh-mem-mini', onClick: function () { resetValue(f.key) } }, t('reset')) : null),
                      f.spec.hint ? React.createElement('span', { className: 'dsh-mem-cfg-hint' }, f.spec.hint) : null,
                      renderField(f.key, f.spec),
                    )
                  }),
                )
              })
              : React.createElement('div', { style: { opacity: .6 } }, t('loading')),
            React.createElement('div', { className: 'dsh-mem-pcard-footer' },
              msg ? React.createElement('span', { className: 'dsh-mem-pcard-failed', style: { opacity: .8 } }, msg) : null,
              React.createElement('button', { className: 'dsh-mem-pcard-save', onClick: save, disabled: busy || !loaded }, busy ? t('cfgSaving') : t('cfgSave')),
            ),
          )
          : null,
      )
    }

    return React.createElement('div', {
      className: 'dsh-mem-panel',
      style: { padding: '4px 0' },
    },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('cfgTitle')),
        React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', onClick: save, disabled: busy || !loaded }, busy ? t('cfgSaving') : t('cfgSave')),
      ),
      msg ? React.createElement('div', { style: { opacity: .75 } }, msg) : null,
      loaded
        ? groups.map(function (g) {
          return React.createElement('div', { key: g.name, className: 'dsh-mem-box' },
            React.createElement('div', { className: 'dsh-mem-title' }, g.title),
            g.fields.map(function (f) {
              return React.createElement('div', { key: f.key, className: 'dsh-mem-cfg-item' },
                React.createElement('span', { className: 'dsh-mem-cfg-label' }, f.spec.description || f.key),
                f.spec.hint ? React.createElement('span', { className: 'dsh-mem-cfg-hint' }, f.spec.hint) : null,
                renderField(f.key, f.spec),
              )
            }),
          )
        })
        : React.createElement('div', { style: { opacity: .6 } }, t('loading')),
    )
  }

  function MemoryRow(props) {
    const m = props.m
    const t = props.t
    const onDelete = props.onDelete
    const onUpdate = props.onUpdate
    const onSource = props.onSource
    const onRequestApproval = props.onRequestApproval
    const src = props.src
    const srcOpen = src && String(src.id) === String(m.id)
    const typeLabels = props.lang === 'en' ? TYPE_EN : TYPE_ZH
    const [editing, setEditing] = React.useState(false)
    const [draft, setDraft] = React.useState(m.content || '')
    async function saveContent() {
      const content = draft.trim()
      if (!content) return
      const ok = await onUpdate(m.id, { content: content })
      if (ok) setEditing(false)
    }
    return React.createElement('div', { className: 'dsh-mem-row' },
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 3, flex: 1 } },
        editing
          ? React.createElement(React.Fragment, null,
            React.createElement('textarea', { className: 'dsh-mem-input dsh-mem-memory-editor', value: draft, onChange: function (e) { setDraft(e.target.value) } }),
            React.createElement('div', { className: 'dsh-mem-memory-edit-actions' },
              React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setDraft(m.content || ''); setEditing(false) } }, t('cardCancel')),
              React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', disabled: !draft.trim(), onClick: saveContent }, t('cardSave')),
            ),
          )
          : React.createElement('span', { className: 'dsh-mem-content' }, m.content),
        React.createElement('span', { className: 'dsh-mem-meta' },
          React.createElement('span', { className: 'dsh-mem-badge' }, typeLabels[m.type] || m.type),
          React.createElement('select', {
            className: 'dsh-mem-mini', title: t('scopeTitle'), value: m.scope || 'workspace',
            onChange: function (e) { onUpdate(m.id, { scope: e.target.value }) },
          },
            React.createElement('option', { value: 'session' }, t('session')),
            React.createElement('option', { value: 'workspace' }, t('workspace')),
            React.createElement('option', { value: 'global' }, t('global')),
          ),
          React.createElement('select', {
            className: 'dsh-mem-mini', title: t('domainTitle'), value: m.domain || 'work',
            onChange: function (e) { onUpdate(m.id, { domain: e.target.value }) },
          },
            React.createElement('option', { value: 'work' }, t('work')),
            React.createElement('option', { value: 'life' }, t('life')),
          ),
          m.importance !== undefined ? React.createElement('span', { className: 'dsh-mem-imp' }, t('importance') + ' ' + Number(m.importance).toFixed(2)) : null,
          React.createElement('button', { className: 'dsh-mem-btn dsh-mem-memory-edit', style: { padding: '0 8px', fontSize: 11 }, onClick: function () { setDraft(m.content || ''); setEditing(true) } }, t('cardEdit')),
          React.createElement('button', { className: 'dsh-mem-btn', style: { padding: '0 8px', fontSize: 11 }, onClick: function () { onSource(m.id) } }, t('source')),
        ),
          m.has_sensitive && srcOpen && src.needsAuth
            ? React.createElement('div', { className: 'dsh-mem-sensitive-box' },
              React.createElement('div', { style: { marginBottom: 6, opacity: .9 } }, t('sensitiveWarning')),
              React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', onClick: onRequestApproval, disabled: src.requesting }, src.requesting ? t('approving') : t('requestApproval')),
            )
            : srcOpen && src.token && !src.loading
            ? React.createElement('div', { style: { marginTop: 6 } },
              React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { onSource(m.id, true) } }, t('expand')),
            )
            : null,
          srcOpen
          ? src.loading
            ? React.createElement('div', { style: { opacity: .6 } }, t('loading'))
            : src.text
              ? React.createElement('div', { className: 'dsh-mem-srcpre' }, src.text)
              : React.createElement('div', { style: { opacity: .6 } }, t('noSource') + (m.session_id || '—'))
          : null,
      ),
      React.createElement('button', { className: 'dsh-mem-del', title: t('delete'), onClick: function () { onDelete(m.id) } }, '✕'),
    )
  }

  function TaskBoardView(props) {
    const t = props.t
    const lang = props.lang
    const [tasks, setTasks] = React.useState([])
    const [creating, setCreating] = React.useState(false)
    const [newTitle, setNewTitle] = React.useState('')
    const [newDesc, setNewDesc] = React.useState('')
    const [newColor, setNewColor] = React.useState('neutral')
    const [msg, setMsg] = React.useState('')
    const colors = { neutral: '#8a8f98', red: '#d94f4f', orange: '#e58a32', yellow: '#c6a52b', green: '#3e9b68', blue: '#4c83c3' }
    const colorNames = { neutral: '默认', red: '红', orange: '橙', yellow: '黄', green: '绿', blue: '蓝' }
    async function load() {
      const res = await api('GET', '/v1/v2/tasks?limit=100')
      if (res && Array.isArray(res.tasks)) setTasks(res.tasks)
    }
    React.useEffect(function () { load() }, [])
    async function createTask() {
      if (!newTitle.trim()) return
      const res = await api('POST', '/v1/v2/tasks', { title: newTitle.trim(), description: newDesc.trim(), task_color: newColor, status: 'planned' })
      if (res && res.task) { setNewTitle(''); setNewDesc(''); setNewColor('neutral'); setCreating(false); load(); setMsg(t('saved')) }
      else setMsg(t('cfgFail') + (res.error || ''))
    }
    async function transition(task, toStatus) {
      const res = await api('POST', '/v1/v2/tasks/' + task.id + '/transition', { to_status: toStatus, expected_version: task.version, reason: 'UI transition' })
      if (res && res.task) { load(); setMsg(t('saved')) } else setMsg(t('cfgFail') + (res.error || ''))
    }
    async function toggleBlocked(task) {
      const res = await api('POST', '/v1/v2/tasks/' + task.id + '/blocked', { blocked: !task.blocked, expected_version: task.version, reason: task.blocked ? 'unblock' : 'block', missing_conditions: [] })
      if (res && res.task) { load(); setMsg(t('saved')) } else setMsg(t('cfgFail') + (res.error || ''))
    }
    async function setColor(task, color) {
      if (color === (task.task_color || 'neutral')) return
      const res = await api('POST', '/v1/v2/tasks/' + task.id + '/color', { task_color: color, expected_version: task.version })
      if (res && res.task) { load(); setMsg(t('saved')) } else setMsg(t('cfgFail') + (res.error || ''))
    }
    const statusMap = { planned: t('planned'), todo: t('todo'), in_progress: t('inProgress'), completed: t('completed'), failed: t('failed') }
    const canTransition = { planned: ['todo'], todo: ['in_progress'], in_progress: ['completed', 'failed'], completed: [], failed: ['todo', 'in_progress'] }
    const activeCount = tasks.filter(function (task) { return task.status === 'todo' || task.status === 'in_progress' }).length
    return React.createElement('div', { className: 'dsh-mem-panel dsh-mem-task-panel' },
      React.createElement('div', { className: 'dsh-mem-task-toolbar' },
        React.createElement('button', { className: 'dsh-mem-btn', onClick: props.onBack }, '‹ ' + t('back')),
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('tasks')),
        React.createElement('div', { className: 'dsh-mem-task-toolbar-meta' },
          React.createElement('span', null, tasks.length + ' ' + (lang === 'en' ? 'total' : '总计')),
          React.createElement('span', null, activeCount + ' ' + (lang === 'en' ? 'active' : '进行中')),
        ),
        React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', onClick: function () { setCreating(!creating) } }, creating ? t('cardCancel') : '+ ' + t('taskCreate')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: load }, t('refresh')),
      ),
      msg ? React.createElement('div', { style: { opacity: .7 } }, msg) : null,
      creating ? React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } }, React.createElement('span', { className: 'dsh-mem-cfg-label' }, t('taskTitle')), React.createElement('input', { className: 'dsh-mem-input', value: newTitle, onChange: function (e) { setNewTitle(e.target.value) } })),
        React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0 } }, React.createElement('span', { className: 'dsh-mem-cfg-label' }, t('taskDesc')), React.createElement('textarea', { className: 'dsh-mem-input', style: { minHeight: 60 }, value: newDesc, onChange: function (e) { setNewDesc(e.target.value) } })),
        React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0 } }, React.createElement('span', { className: 'dsh-mem-cfg-label' }, '颜色'), React.createElement('select', { className: 'dsh-mem-input', value: newColor, onChange: function (e) { setNewColor(e.target.value) } }, Object.keys(colors).map(function (c) { return React.createElement('option', { key: c, value: c }, colorNames[c]) }))),
        React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-primary', onClick: createTask }, t('save')),
      ) : null,
      React.createElement('div', { className: 'dsh-mem-task-board' },
        ['planned', 'todo', 'in_progress', 'completed', 'failed'].map(function (status) {
          const items = tasks.filter(function (task) { return task.status === status })
          return React.createElement('section', { key: status, className: 'dsh-mem-task-column', 'data-status': status },
            React.createElement('div', { className: 'dsh-mem-task-column-title' }, React.createElement('span', null, statusMap[status]), React.createElement('span', { className: 'dsh-mem-badge' }, String(items.length))),
            items.length ? items.map(function (task) {
              const color = task.task_color || 'neutral'
              return React.createElement('details', { key: task.id, className: 'dsh-mem-task-card' },
                React.createElement('span', { className: 'dsh-mem-task-priority', style: { background: colors[color] || colors.neutral }, title: colorNames[color] || color }),
                React.createElement('summary', null, task.title),
                React.createElement('div', { className: 'dsh-mem-task-detail' },
                  task.description ? React.createElement('div', { className: 'dsh-mem-task-description' }, task.description) : null,
                  task.blocked ? React.createElement('div', { className: 'dsh-mem-task-blocked' }, t('taskBlocked')) : null,
                  React.createElement('label', { className: 'dsh-mem-task-color-label' }, '颜色 ', React.createElement('select', { value: color, onChange: function (e) { setColor(task, e.target.value) } }, Object.keys(colors).map(function (c) { return React.createElement('option', { key: c, value: c }, colorNames[c]) }))),
                  React.createElement('div', { className: 'dsh-mem-task-controls' },
                    status === 'in_progress' ? React.createElement('button', { className: 'dsh-mem-btn dsh-mem-btn-warn', onClick: function () { toggleBlocked(task) } }, task.blocked ? t('taskUnblock') : t('taskBlocked')) : null,
                    (canTransition[status] || []).map(function (st) { return React.createElement('button', { key: st, className: 'dsh-mem-btn', onClick: function () { transition(task, st) } }, statusMap[st]) }),
                  ),
                ),
              )
            }) : React.createElement('div', { className: 'dsh-mem-task-empty' }, t('noTasks')),
          )
        }),
      ),
    )
  }

  function MemoryPanel(props) {
    const [view, setView] = React.useState('main')
    const [lang, setLang] = React.useState('zh')
    const [memories, setMemories] = React.useState([])
    const [card, setCard] = React.useState(null)
    const [revisions, setRevisions] = React.useState([])
    const [stats, setStats] = React.useState(null)
    const [enabled, setEnabled] = React.useState(true)
    const [query, setQuery] = React.useState('')
    const [newContent, setNewContent] = React.useState('')
    const [newType, setNewType] = React.useState('fact')
    const [newScope, setNewScope] = React.useState('workspace')
    const [newDomain, setNewDomain] = React.useState('work')
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState('')
    const [src, setSrc] = React.useState(null)
    const [cardEdit, setCardEdit] = React.useState(false)
    const [cardDraft, setCardDraft] = React.useState(null)

    const sid = props && props.sessionId ? String(props.sessionId) : ''
    const dict = I18N[lang] || I18N.zh
    function t(key) { return dict[key] !== undefined ? dict[key] : key }

    async function openSource(id, expand) {
      if (src && String(src.id) === String(id) && !expand) { setSrc(null); return }
      if (expand && src && src.token) {
        const expanded = await api('POST', '/v1/sensitive/expand', { source_id: src.sourceId, approval_token: src.token, user_id: 'dsh-user', session_id: sid })
        setSrc(Object.assign({}, src, { loading: false, text: expanded.source?.content || null, needsAuth: false }))
        return
      }
      setSrc({ id: id, loading: true })
      const res = await api('GET', '/v1/memories/' + encodeURIComponent(String(id)) + '/source')
      const list = res && Array.isArray(res.sources) ? res.sources : []
      const first = list[0]
      setSrc({ id: id, loading: false, sourceId: first?.protected_source_id || first?.id, text: first?.content || null, needsAuth: !!first?.needs_auth })
    }

    async function requestApproval() {
      if (!src || !sid) return
      setSrc(Object.assign({}, src, { requesting: true }))
      const res = await api('POST', '/v1/sensitive/approve', { user_id: 'dsh-user', session_id: sid, confirmed: true })
      if (res && res.approval_token) setSrc(Object.assign({}, src, { token: res.approval_token, requesting: false, needsAuth: false }))
      else setSrc(Object.assign({}, src, { requesting: false, error: res?.error || 'approval failed' }))
    }

    async function refresh() {
      setBusy(true)
      const res = await api('GET', '/v1/overview?workspace_id=' + encodeURIComponent(WORKSPACE_DEFAULT) + '&session_id=' + encodeURIComponent(sid))
      // Fetch session state card from v2 API
      if (sid) {
        const kind = props && props.cardKind === 'task' ? 'task' : 'daily'
        const cardRes = await api('GET', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sid))
        if (cardRes && cardRes.card) setCard(cardRes.card.payload)
        const revRes = await api('GET', '/v1/v2/cards/' + kind + '/' + encodeURIComponent(sid) + '/revisions')
        if (revRes && Array.isArray(revRes.revisions)) setRevisions(revRes.revisions)
      }
      if (res && Array.isArray(res.memories)) setMemories(res.memories)
      else if (res && res.error) setMsg(t('loadFail') + res.error)
      if (res && res.card) setCard(res.card)
      if (res && typeof res.documents === 'number') setStats({ documents: res.documents, archived: res.archived, atoms: res.atoms })
      if (res && typeof res.session_enabled === 'boolean') setEnabled(res.session_enabled)
      setBusy(false)
    }

    React.useEffect(function () { refresh() }, [])

    async function doSearch() {
      if (!query.trim()) { refresh(); return }
      setBusy(true)
      const res = await api('POST', '/v1/memories/search', { query: query.trim(), k: 15, workspace_id: WORKSPACE_DEFAULT })
      if (res && Array.isArray(res.results)) setMemories(res.results)
      else if (res && res.error) setMsg(t('searchFail') + res.error)
      setBusy(false)
    }

    async function doAdd() {
      if (!newContent.trim()) return
      const res = await api('POST', '/v1/memories/add', {
        content: newContent.trim(), type: newType, scope: newScope, domain: newDomain, workspace_id: WORKSPACE_DEFAULT, session_id: sid,
      })
      if (res && res.error) { setMsg(t('cfgFail') + res.error) } else { setNewContent(''); setMsg(t('saved')); refresh() }
    }

    async function doDelete(id) {
      await api('DELETE', '/v1/memories/' + encodeURIComponent(String(id)))
      refresh()
    }

    async function doUpdate(id, patch) {
      const res = await api('PUT', '/v1/memories/' + encodeURIComponent(String(id)), patch)
      if (res && res.error) { setMsg(t('updateFail') + res.error); return false }
      setMemories(function (prev) {
        return prev.map(function (m) { return String(m.id) === String(id) ? Object.assign({}, m, patch) : m })
      })
      setMsg(t('adjustDone'))
      return true
    }

    async function toggleEnabled() {
      const res = await api('POST', '/v1/settings/set', { key: 'session_enabled:' + sid, value: !enabled })
      if (res && res.key) { setEnabled(!enabled); setMsg(!enabled ? t('memOn') : t('memOff')) }
    }

    function subProps() {
      return { t: t, lang: lang, onBack: function () { setView('main'); refresh() } }
    }
    if (view === 'graph') return React.createElement(GraphView, subProps())
    if (view === 'archive') return React.createElement(ArchiveView, subProps())
    if (view === 'maintain') return React.createElement(MaintenanceView, subProps())
    if (view === 'tasks') return React.createElement(TaskBoardView, subProps())
    if (view === 'config') return React.createElement(ConfigView, { t: t, lang: lang, sessionId: sid, onBack: function () { setView('main') } })

    const cardLines = []
    if (card && card.goal) cardLines.push({ k: '目标', v: card.goal })
    if (card && card.current_plan) cardLines.push({ k: '当前方案', v: card.current_plan })
    if (card && card.next_steps && card.next_steps.length) cardLines.push({ k: '下一步', v: card.next_steps.join('；') })
    if (card && card.in_progress && card.in_progress.length) cardLines.push({ k: '进行中', v: card.in_progress.join('；') })

    function ListEditor(props) {
      const items = props.items
      const setItems = props.setItems
      const addLabel = props.addLabel
      return React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
        items.map(function (item, i) {
          return React.createElement('div', { key: String(i), style: { display: 'flex', gap: 6, alignItems: 'center' } },
            React.createElement('input', {
              className: 'dsh-mem-input',
              value: item,
              onChange: function (e) {
                setItems(items.map(function (x, j) { return j === i ? e.target.value : x }))
              },
            }),
            React.createElement('button', {
              className: 'dsh-mem-del', title: t('delete'),
              onClick: function () { setItems(items.filter(function (_, j) { return j !== i })) },
            }, '✕'),
          )
        }),
        React.createElement('button', {
          className: 'dsh-mem-btn', style: { alignSelf: 'flex-start' },
          onClick: function () { setItems(items.concat([''])) },
        }, '+ ' + addLabel),
      )
    }

    function renderCardEditor() {
      const d = cardDraft
      return React.createElement('div', { className: 'dsh-mem-box', style: { borderColor: 'var(--dsw-alias-brand-primary, #4c8dff)' } },
        React.createElement('div', { className: 'dsh-mem-actions' },
          React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('stateCard') + ' · ' + t('cardEdit')),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: saveCard, disabled: busy }, t('cardSave')),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setCardEdit(false) } }, t('cardCancel')),
        ),
        React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
          React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } },
            React.createElement('span', { className: 'dsh-mem-cfg-label' }, '目标'),
            React.createElement('input', {
              className: 'dsh-mem-input', value: d.goal,
              onChange: function (e) { setCardDraft(Object.assign({}, d, { goal: e.target.value })) },
            }),
          ),
          React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } },
            React.createElement('span', { className: 'dsh-mem-cfg-label' }, '当前方案'),
            React.createElement('input', {
              className: 'dsh-mem-input', value: d.current_plan,
              onChange: function (e) { setCardDraft(Object.assign({}, d, { current_plan: e.target.value })) },
            }),
          ),
          React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } },
            React.createElement('span', { className: 'dsh-mem-cfg-label' }, '关键决定'),
            React.createElement(ListEditor, { items: d.key_decisions, addLabel: '决定', setItems: function (v) { setCardDraft(Object.assign({}, d, { key_decisions: v })) } }),
          ),
          React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } },
            React.createElement('span', { className: 'dsh-mem-cfg-label' }, '进行中'),
            React.createElement(ListEditor, { items: d.in_progress, addLabel: '事项', setItems: function (v) { setCardDraft(Object.assign({}, d, { in_progress: v })) } }),
          ),
          React.createElement('div', { className: 'dsh-mem-cfg-item', style: { padding: 0, borderTop: 'none' } },
            React.createElement('span', { className: 'dsh-mem-cfg-label' }, '下一步'),
            React.createElement(ListEditor, { items: d.next_steps, addLabel: '步骤', setItems: function (v) { setCardDraft(Object.assign({}, d, { next_steps: v })) } }),
          ),
        ),
      )
    }

    function startCardEdit() {
      setCardDraft({
        goal: (card && card.goal) || '',
        current_plan: (card && card.current_plan) || '',
        key_decisions: (card && card.key_decisions) || [],
        in_progress: (card && card.in_progress) || [],
        next_steps: (card && card.next_steps) || [],
      })
      setCardEdit(true)
    }

    async function saveCard() {
      setBusy(true)
      const d = cardDraft
      const res = await api('POST', '/v1/cards/upsert', {
        workspace_id: WORKSPACE_DEFAULT,
        goal: d.goal,
        current_plan: d.current_plan,
        key_decisions: d.key_decisions.filter(function (x) { return String(x).trim() }),
        in_progress: d.in_progress.filter(function (x) { return String(x).trim() }),
        next_steps: d.next_steps.filter(function (x) { return String(x).trim() }),
      })
      setBusy(false)
      if (res && res.error) { setMsg(t('cfgFail') + res.error); return }
      setCardEdit(false)
      setMsg(t('saved'))
      refresh()
    }

    const globalMems = memories.filter(function (m) { return m && m.scope === 'global' })
    const localMems = memories.filter(function (m) { return m && m.scope !== 'global' })

    function section(title, items) {
      return React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-section-head' },
          React.createElement('span', { className: 'dsh-mem-title' }, title),
          React.createElement('span', { className: 'dsh-mem-imp' }, items.length + ' ' + t('count')),
        ),
        items.length
          ? items.map(function (m) { return React.createElement(MemoryRow, { key: String(m.id), m: m, t: t, lang: lang, onDelete: doDelete, onUpdate: doUpdate, onSource: openSource, onRequestApproval: requestApproval, src: src }) })
          : React.createElement('div', { style: { opacity: .6 } }, t('empty')),
      )
    }

    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('panelTitle')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('graph') } }, t('graph')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('archive') } }, t('archive')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('maintain') } }, t('maintain')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('tasks') } }, t('tasks')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('config') } }, t('sessionConfig')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: toggleEnabled }, enabled ? t('enabled') : t('disabled')),
        React.createElement('button', { className: 'dsh-mem-btn', title: 'Switch language', onClick: function () { setLang(lang === 'zh' ? 'en' : 'zh') } }, t('lang')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: refresh, disabled: busy }, busy ? t('loading') : t('refresh')),
      ),
      msg ? React.createElement('div', { style: { opacity: .7 } }, msg) : null,
      React.createElement('div', { className: 'dsh-mem-stats' },
        React.createElement('span', null, t('memories') + ' ' + (stats ? stats.documents : '?') + ' ' + t('count')),
        stats ? React.createElement('span', null, t('atoms') + ' ' + stats.atoms) : null,
        stats ? React.createElement('span', null, t('archived') + ' ' + stats.archived) : null,
        card ? React.createElement('span', null, t('stateV') + card.version) : null,
      ),
      cardEdit
        ? renderCardEditor()
        : React.createElement('div', { className: 'dsh-mem-box' },
          React.createElement('div', { className: 'dsh-mem-actions' },
            React.createElement('div', { className: 'dsh-mem-title', style: { flex: 1 } }, t('stateCard')),
            React.createElement('button', { className: 'dsh-mem-btn', onClick: startCardEdit }, t('cardEdit')),
          ),
          cardLines.length ? cardLines.map(function (l) {
            return React.createElement('div', { key: l.k, className: 'dsh-mem-row' },
              React.createElement('span', { style: { opacity: .7, whiteSpace: 'nowrap' } }, l.k + ': '),
              React.createElement('span', { className: 'dsh-mem-content' }, l.v),
            )
          }) : React.createElement('div', { style: { opacity: .6 } }, t('noCard')),
          revisions.length ? React.createElement('div', { className: 'dsh-mem-meta' }, t('cardRevisions') + ': ' + revisions.length) : null,
        ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('manual')),
        React.createElement('div', { className: 'dsh-mem-form' },
          React.createElement('input', { className: 'dsh-mem-input', style: { flex: '1 1 100%' }, value: newContent, placeholder: t('newPlaceholder'), onChange: function (e) { setNewContent(e.target.value) } }),
          React.createElement('select', { className: 'dsh-mem-select', value: newType, onChange: function (e) { setNewType(e.target.value) } },
            React.createElement('option', { value: 'fact' }, lang === 'en' ? 'fact' : '事实'),
            React.createElement('option', { value: 'preference' }, lang === 'en' ? 'preference' : '偏好'),
            React.createElement('option', { value: 'decision' }, lang === 'en' ? 'decision' : '决定'),
            React.createElement('option', { value: 'plan' }, lang === 'en' ? 'plan' : '计划'),
            React.createElement('option', { value: 'episode' }, lang === 'en' ? 'episode' : '事件'),
          ),
          React.createElement('select', { className: 'dsh-mem-select', value: newScope, onChange: function (e) { setNewScope(e.target.value) } },
            React.createElement('option', { value: 'workspace' }, t('workspace')),
            React.createElement('option', { value: 'global' }, t('global')),
            React.createElement('option', { value: 'session' }, t('session')),
          ),
          React.createElement('select', { className: 'dsh-mem-select', value: newDomain, onChange: function (e) { setNewDomain(e.target.value) } },
            React.createElement('option', { value: 'work' }, t('work')),
            React.createElement('option', { value: 'life' }, t('life')),
          ),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: doAdd }, t('save')),
        ),
      ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('retrieval')),
        React.createElement('div', { style: { display: 'flex', gap: 8 } },
          React.createElement('input', { className: 'dsh-mem-input', value: query, placeholder: t('searchPlaceholder'), onChange: function (e) { setQuery(e.target.value) }, onKeyDown: function (e) { if (e.key === 'Enter') doSearch() } }),
          React.createElement('button', { className: 'dsh-mem-btn', onClick: doSearch }, t('search')),
        ),
      ),
      section(t('globalSection'), globalMems),
      section(t('localSection'), localMems),
    )
  }

  slots.inject('conversation.view', function () {
    return slots.register(
      { name: 'conversation.view', id: 'memory', order: 5, label: '记忆' },
      function (props) { return React.createElement(MemoryPanel, props) },
    )
  })

  // 记忆配置卡片：设置 → 插件 → 插件配置页（官方 configurable tab），独立卡片
  slots.inject('settings.plugin.item', function () {
    return slots.register(
      { name: 'settings.plugin.item', id: 'deepmemory', order: 30, label: 'deepmemory 记忆' },
      function () {
        const dict = I18N.zh
        const t = function (key) { return dict[key] !== undefined ? dict[key] : key }
        return React.createElement(ConfigView, { t: t, lang: 'zh', embedded: true, sessionId: null })
      },
    )
  })
}
