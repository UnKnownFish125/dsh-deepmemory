// Preset plugin contract + HTTP transport verification.
const path = process.env.PLUGIN_PATH
const requests = []
globalThis.fetch = async (url, options = {}) => {
  requests.push({ url: String(url), method: options.method || 'GET', body: options.body })
  const pathname = new URL(url).pathname
  let data = { value: null }
  if (pathname.endsWith('/memories/add')) data = { id: 1 }
  if (pathname.endsWith('/memories/search')) data = { count: 0, results: [] }
  if (pathname.endsWith('/briefing')) data = { count: 0, briefing: '' }
  return { ok: true, status: 200, json: async () => data }
}

const plugin = await import(path)
if (typeof plugin.apply !== 'function') throw new Error('apply 不是函数')
if (plugin.name !== 'deepmemory') throw new Error('插件 name 异常: ' + plugin.name)

const registered = new Map()
const ctx = {
  get: () => undefined,
  tools: { register: (tool) => { registered.set(tool.name, tool); return () => {} } },
  effect: (fn) => fn(),
  on: () => {},
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
if (requests.some((item) => !item.url.startsWith('http://localhost:6230/'))) {
  throw new Error('默认请求越出本地 memory-server 边界')
}
console.log('plugin apply + native fetch ok, tools:', expected.join(', '))
