# _literature-kb-plugin（知识库工具 + 轨 B 注入）

生产加载：`/www/dsh/home/.agent-presets/_literature-kb-plugin/plugin-v1.js`（preset harness-memory-task 组件）

- 工具：kb_query / kb_browse / kb_constraints / kb_contracts / kb_graph（经 /lit-api → 6260）
- 轨 B：`systemPrompt.context`（order:45）注入 `[约束前提]`（6260 kb/constraints——bias 知识优先+去重，TTL 300s）
- **纪律**：改此文件后同步生产/测试机副本 + 门禁（node --check /tmp/chk.mjs + ESM import + session.models 断言）
- 历史教训（48h 6 连崩）：漏 inject=['tools']、assemble assembly.push 无效、链式礼仪（勿吞 next 返回值）
