# DeepSeek Harness 子插件空白模板

复制整个目录后再改名，用来开发第三方子插件。

这个模板故意不包含 deepmemory 的记忆注入、自动抽取、状态卡、任务看板、子 agent 或 workflow。它只提供：

- 最小 persona 和 agent instructions
- 可选工具的注释入口
- 一个相对路径插件入口：`plugin/plugin.js`
- preset-local realm 的示例
- 上下文预算配置的注释示例

## 使用方式

1. 复制 `agent-preset/blank-template/` 到 `${DSH_HOME}/.agent-presets/<your-preset>/`。
2. 修改 `preset.yml` 的名称和描述。
3. 修改 `agent.cordis.yml` 的 persona、工具和插件入口。
4. 把业务代码放进 `plugin/`，不要把会话状态写进 preset 文件。
5. 按 Harness 当前部署方式挂载并选择这个 preset。

## 边界

模型上下文窗口由具体 provider/model adapter 提供。模板里的预算只是子插件的目标策略，实际分配前应读取 `ctx.llm.resolveModelInfo().context` 和 `ctx.tokenMeter` 的请求压力。

需要 host-plane 服务时，使用 Harness 已有 registry，不要在模板里复制一份。需要 preset-local 服务时，放进带 `isolate` 的 group，并让消费者与服务共享正确的 realm。
