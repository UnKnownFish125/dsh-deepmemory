// client.js 渲染级验证：转换后的 __ModuleLoader__ 产物在模拟浏览器环境里
// 必须能 apply + 注册 slot + 渲染出非空面板。React 绑定缺失、渲染抛错都会在此失败。
import { createRequire } from 'module'
import fs from 'fs'
import vm from 'vm'

const path = process.env.CLIENT_JS
const require = createRequire('/www/dsh/home/profiles/web/package.json')
const React = require('react')
const ReactDOMServer = require('react-dom/server')

const src = fs.readFileSync(path, 'utf8')
let renderFn = null
const fakeSlots = {
  inject: (key, cb) => { cb() },
  register: (config, fn) => { renderFn = fn; return () => {} },
}
const fakeCtx = {
  get: (k) => (k === 'slots' ? fakeSlots : undefined),
  effect: () => () => {},
}
const sandbox = {
  require: (p) => { if (p === 'react') return React; throw new Error('unexpected require ' + p) },
  document: { createElement: () => ({ dataset: {}, style: {}, appendChild() {}, remove() {} }), head: { appendChild() {} } },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ memories: [], card: null, documents: 0, archived: 0, atoms: 0, session_enabled: true }) }),
  console,
  __ModuleLoader__: { load: (spec) => { const m = spec.factory(sandbox.require); m.apply(fakeCtx) } },
}
vm.createContext(sandbox)
vm.runInNewContext(src, sandbox, { filename: 'client.js' })
if (typeof renderFn !== 'function') throw new Error('slot renderFn 未注册')
const html = ReactDOMServer.renderToString(React.createElement(renderFn, { sessionId: 'verify' }))
if (!html || html.length < 100) throw new Error('渲染输出为空或异常: ' + html.length)
if (!html.includes('记忆') && !html.includes('deepmemory')) throw new Error('渲染内容不含面板标识')
console.log('render ok, html length', html.length)
