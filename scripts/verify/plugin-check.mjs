// preset 插件格式验证：import 模块 -> mock cordis ctx 完整执行 apply ->
// 三个工具必须通过 dsh-tools defineTool 的扁平 parameters 校验并注册成功。
// 任何 parameters 格式回退（JSON Schema 包裹）都会在这里立刻暴露。
const path = process.env.PLUGIN_PATH
const plugin = await import(path)

if (typeof plugin.apply !== 'function') throw new Error('apply 不是函数')
if (plugin.name !== 'deepmemory') throw new Error('插件 name 异常: ' + plugin.name)

const registered = []
const fakeShell = {
  resolve: () => ({}),
  run: async () => ({ aborted: false, timedOut: false, stdout: { text: '{"value":"x"}' } }),
}
const ctx = {
  get: (k) => (k === 'shell' ? fakeShell : undefined),
  tools: { register: (t) => { registered.push(t.name); return () => {} } },
  effect: (fn) => fn(),
  on: () => {},
  interval: () => {},
}
plugin.apply(ctx)

const expected = ['memory_recall', 'memory_save', 'memory_briefing']
for (const name of expected) {
  if (!registered.includes(name)) throw new Error('工具未注册: ' + name)
}
console.log('plugin apply ok, tools:', registered.join(', '))
