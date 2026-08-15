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
  },
}

const PANEL_CSS = `
.dsh-mem-panel { padding:16px 20px; display:flex; flex-direction:column; gap:12px; font-size:13px; max-width:860px; }
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
.dsh-mem-graph-svg { width:100%; height:auto; background:var(--dsw-alias-bg-layer-1, rgba(128,128,128,.06)); border-radius:8px; }
.dsh-mem-graph-node { fill:var(--dsw-alias-brand-primary, #4c8dff); opacity:.85; }
.dsh-mem-graph-label { fill:var(--dsw-alias-label-primary, #e8e8e8); font-size:11px; pointer-events:none; }
.dsh-mem-graph-edge { stroke:var(--dsw-alias-border-l2, rgba(128,128,128,.5)); stroke-width:1.2; }
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
  ctx.effect(() => () => styleEl.remove())

  function GraphView(props) {
    const t = props.t
    const lang = props.lang
    const [data, setData] = React.useState(null)
    const [selected, setSelected] = React.useState(null)
    const [linked, setLinked] = React.useState(null)
    const [hover, setHover] = React.useState(null)
    const [pos, setPos] = React.useState({})
    const [zoom, setZoom] = React.useState(1)
    const [pan, setPan] = React.useState({ x: 0, y: 0 })
    const drag = React.useRef(null)
    async function load() {
      const g = await api('GET', '/v1/graph')
      if (g && Array.isArray(g.nodes)) { setData(g); setPos({}) }
    }
    React.useEffect(function () { load() }, [])
    async function selectNode(n) {
      if (selected && selected.id === n.id) { setSelected(null); setLinked(null); return }
      setSelected(n)
      setLinked(null)
      const res = await api('GET', '/v1/graph/memories?entity=' + encodeURIComponent(n.name))
      if (res && Array.isArray(res.memories)) setLinked(res.memories)
    }
    if (!data) return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { style: { opacity: .6 } }, t('loading')))
    const nodes = data.nodes || []
    const edges = data.edges || []
    const W = 640, H = 400, cx = W / 2, cy = H / 2
    const R = Math.min(W, H) / 2 - 60
    // 初始环形布局；pos 覆盖被拖拽过的节点（Obsidian 风格：拖哪停哪）
    const base = {}
    nodes.forEach(function (n, i) {
      const a = (2 * Math.PI * i) / Math.max(1, nodes.length) - Math.PI / 2
      base[n.id] = [cx + R * Math.cos(a), cy + R * Math.sin(a)]
    })
    const getPos = function (nid) { return pos[nid] || base[nid] }
    // 节点度数 -> 半径
    const deg = {}
    edges.forEach(function (e) {
      deg[e.source_id] = (deg[e.source_id] || 0) + 1
      deg[e.target_id] = (deg[e.target_id] || 0) + 1
    })
    const radiusOf = function (n) { return 4 + Math.min(deg[n.id] || 0, 8) * 1.5 }
    // hover 邻居集
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
    // 鼠标交互：节点拖拽 / 空白平移 / 滚轮缩放
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
      const dx = e.clientX - d.sx
      const dy = e.clientY - d.sy
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
      if (!d.moved) return
      if (d.kind === 'node') {
        setPos(function (prev) {
          const next = Object.assign({}, prev)
          next[d.id] = [d.ox + dx, d.oy + dy]
          return next
        })
      } else if (d.kind === 'pan') {
        setPan({ x: d.ox + dx, y: d.oy + dy })
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
      const factor = e.deltaY < 0 ? 1.1 : 0.9
      setZoom(function (z) { return Math.min(4, Math.max(0.4, z * factor)) })
    }
    const edgeEls = edges.map(function (e, i) {
      const a = getPos(e.source_id), b = getPos(e.target_id)
      if (!a || !b) return null
      const hot = hover === null || hover === e.source_id || hover === e.target_id
      return React.createElement('line', {
        key: 'e' + i,
        className: 'dsh-mem-graph-edge',
        x1: a[0], y1: a[1], x2: b[0], y2: b[1],
        opacity: hot ? 1 : 0.2,
        strokeWidth: hot ? 1.6 : 1,
      }, React.createElement('title', null, (id2name[e.source_id] || '?') + ' → ' + (e.relation || '') + ' → ' + (id2name[e.target_id] || '?')))
    }).filter(Boolean)
    const typeLabels = lang === 'en' ? TYPE_EN : TYPE_ZH
    const nodeEls = nodes.map(function (n) {
      const p = getPos(n.id)
      if (!p) return null
      const isSel = selected && selected.id === n.id
      const dim = neighbors !== null && !neighbors.has(n.id)
      const hot = hover === n.id
      return React.createElement('g', {
        key: 'n' + n.id,
        style: { cursor: 'grab' },
        onMouseDown: function (e) { onNodeDown(e, n) },
        onMouseEnter: function () { setHover(n.id) },
        onMouseLeave: function () { setHover(null) },
      },
        React.createElement('circle', {
          className: 'dsh-mem-graph-node', cx: p[0], cy: p[1], r: radiusOf(n),
          opacity: dim ? 0.18 : 1,
          stroke: hot ? 'var(--dsw-alias-brand-primary, #4c8dff)' : (isSel ? 'var(--dsw-alias-label-primary, #fff)' : 'none'),
          strokeWidth: (hot || isSel) ? 2 : 0,
          title: n.name + ' (' + (n.kind || '') + ') · ' + (deg[n.id] || 0) + ' 关系',
        }),
        React.createElement('text', {
          className: 'dsh-mem-graph-label', x: p[0] + 12, y: p[1] + 4, textAnchor: 'start',
          fontWeight: (isSel || hot) ? 700 : 400,
          opacity: dim ? 0.18 : 1,
        }, String(n.name).slice(0, 16)),
      )
    })
    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('graphTitle')),
        React.createElement('span', { className: 'dsh-mem-imp' }, t('graphDragHint')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setPos({}); setPan({ x: 0, y: 0 }); setZoom(1) } }, t('graphReset')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: props.onBack }, t('back')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: load }, t('refresh')),
      ),
      React.createElement('div', { className: 'dsh-mem-stats' },
        React.createElement('span', null, t('nodeCount') + ' ' + nodes.length),
        React.createElement('span', null, t('edgeCount') + ' ' + edges.length),
      ),
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('graphHint')),
        nodes.length
          ? React.createElement('svg', {
            className: 'dsh-mem-graph-svg', viewBox: '0 0 ' + W + ' ' + H,
            onMouseDown: onBgDown, onMouseMove: onMove, onMouseUp: onUp, onWheel: onWheel,
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
    const [schema, setSchema] = React.useState(null)
    const [values, setValues] = React.useState({})
    const [loaded, setLoaded] = React.useState(false)
    const [busy, setBusy] = React.useState(false)
    const [msg, setMsg] = React.useState('')

    async function load() {
      const sres = await api('GET', '/v1/config-schema')
      const vres = await api('GET', '/v1/config')
      if (sres && sres.schema) setSchema(sres.schema)
      if (vres && vres.config) setValues(vres.config)
      setLoaded(true)
    }

    React.useEffect(function () { load() }, [])

    async function save() {
      setBusy(true)
      const res = await api('POST', '/v1/config', values)
      setBusy(false)
      if (res && res.error) { setMsg(t('cfgFail') + res.error); return }
      setMsg(t('cfgSaved').replace('N', String(res ? res.saved : 0)))
    }

    function setValue(key, v) {
      setValues(function (prev) { const next = Object.assign({}, prev); next[key] = v; return next })
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
                      React.createElement('span', { className: 'dsh-mem-cfg-label' }, f.spec.description || f.key),
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
    const src = props.src
    const srcOpen = src && String(src.id) === String(m.id)
    const typeLabels = props.lang === 'en' ? TYPE_EN : TYPE_ZH
    return React.createElement('div', { className: 'dsh-mem-row' },
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 3, flex: 1 } },
        React.createElement('span', { className: 'dsh-mem-content' }, m.content),
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
          React.createElement('button', { className: 'dsh-mem-btn', style: { padding: '0 8px', fontSize: 11 }, onClick: function () { onSource(m.id) } }, t('source')),
        ),
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

  function MemoryPanel(props) {
    const [view, setView] = React.useState('main')
    const [lang, setLang] = React.useState('zh')
    const [memories, setMemories] = React.useState([])
    const [card, setCard] = React.useState(null)
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

    const sid = props && props.sessionId ? String(props.sessionId) : ''
    const dict = I18N[lang] || I18N.zh
    function t(key) { return dict[key] !== undefined ? dict[key] : key }

    async function openSource(id) {
      if (src && String(src.id) === String(id)) { setSrc(null); return }
      setSrc({ id: id, loading: true })
      const res = await api('GET', '/v1/memories/' + encodeURIComponent(String(id)) + '/source')
      const list = res && Array.isArray(res.sources) ? res.sources : []
      setSrc({ id: id, loading: false, text: list.length ? list[0].content : null })
    }

    async function refresh() {
      setBusy(true)
      const res = await api('GET', '/v1/overview?workspace_id=' + encodeURIComponent(WORKSPACE_DEFAULT) + '&session_id=' + encodeURIComponent(sid))
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
        content: newContent.trim(), type: newType, scope: newScope, domain: newDomain, workspace_id: WORKSPACE_DEFAULT,
      })
      if (res && res.error) { setMsg(t('cfgFail') + res.error) } else { setNewContent(''); setMsg(t('saved')); refresh() }
    }

    async function doDelete(id) {
      await api('DELETE', '/v1/memories/' + encodeURIComponent(String(id)))
      refresh()
    }

    async function doUpdate(id, patch) {
      const res = await api('PUT', '/v1/memories/' + encodeURIComponent(String(id)), patch)
      if (res && res.error) { setMsg(t('updateFail') + res.error); return }
      setMemories(function (prev) {
        return prev.map(function (m) { return String(m.id) === String(id) ? Object.assign({}, m, patch) : m })
      })
      setMsg(t('adjustDone'))
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

    const cardLines = []
    if (card && card.goal) cardLines.push({ k: '目标', v: card.goal })
    if (card && card.current_plan) cardLines.push({ k: '当前方案', v: card.current_plan })
    if (card && card.next_steps && card.next_steps.length) cardLines.push({ k: '下一步', v: card.next_steps.join('；') })
    if (card && card.in_progress && card.in_progress.length) cardLines.push({ k: '进行中', v: card.in_progress.join('；') })

    const globalMems = memories.filter(function (m) { return m && m.scope === 'global' })
    const localMems = memories.filter(function (m) { return m && m.scope !== 'global' })

    function section(title, items) {
      return React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-section-head' },
          React.createElement('span', { className: 'dsh-mem-title' }, title),
          React.createElement('span', { className: 'dsh-mem-imp' }, items.length + ' ' + t('count')),
        ),
        items.length
          ? items.map(function (m) { return React.createElement(MemoryRow, { key: String(m.id), m: m, t: t, lang: lang, onDelete: doDelete, onUpdate: doUpdate, onSource: openSource, src: src }) })
          : React.createElement('div', { style: { opacity: .6 } }, t('empty')),
      )
    }

    return React.createElement('div', { className: 'dsh-mem-panel' },
      React.createElement('div', { className: 'dsh-mem-actions' },
        React.createElement('span', { className: 'dsh-mem-title', style: { flex: 1 } }, t('panelTitle')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('graph') } }, t('graph')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('archive') } }, t('archive')),
        React.createElement('button', { className: 'dsh-mem-btn', onClick: function () { setView('maintain') } }, t('maintain')),
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
      React.createElement('div', { className: 'dsh-mem-box' },
        React.createElement('div', { className: 'dsh-mem-title' }, t('stateCard')),
        cardLines.length ? cardLines.map(function (l) {
          return React.createElement('div', { key: l.k, className: 'dsh-mem-row' },
            React.createElement('span', { style: { opacity: .7, whiteSpace: 'nowrap' } }, l.k + ': '),
            React.createElement('span', { className: 'dsh-mem-content' }, l.v),
          )
        }) : React.createElement('div', { style: { opacity: .6 } }, t('noCard')),
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
        return React.createElement(ConfigView, { t: t, lang: 'zh', embedded: true })
      },
    )
  })
}
