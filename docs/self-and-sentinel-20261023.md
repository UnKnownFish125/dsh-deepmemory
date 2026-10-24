# deepmemory 补齐：头部人格/命令层 + 摘要门牌 + 哨兵（测试机）

> 2026-10-23 · 测试机 `/www/dsh-test-home`（0.2.1-rc.2，:3091）先行；生产未动
> 起因：对照 dsh-agent-soul 的 ECHO/VITAL 实现，确认我们缺两块——「失败了怎么办」与「模型人格」。

## 1. P0 · SELF 头部命令层（已落地并验证）

- 文件：`/www/dsh-test-home/.agent-presets/_memory-plugin/SELF.md`（身份 / 硬线 / 5 条命令 / 门在哪）
- 插件：`plugin-v3.js`
  - 新增 `readSelfHead()`（mtime 缓存，不每轮读盘）
  - `systemPrompt.section({ name:'deepmemory:self', order:100, text:()=>… })` —— **头部**
  - 缺失 → 注入一行显式告警 + 最小命令集；超 4000 字符 → 截断 + 显式告警；异常不抛
- 为什么放头部：dsv4.1-flash 注意力集中在上下文前部；**静态内容放头部不破坏前缀缓存**，
  时钟/记忆/摘要等易变内容仍走 `systemPrompt.context`（尾部）。
- 实测：新会话 `session-f64fb263…` 的 `system/message` 中 `【SELF·常驻】` 位于 **1.3%**（系统提示词 8,616 字符）。

## 2. P1 · 压缩摘要门牌（代码已落地，待下一次真实压缩验证）

- `compaction/summary` 事件自带 `shadowedRange.start/end`（实测 9/71、84/87）。
- 存储时在 `summary` 前加 `[log:<会话ID>#seq=<start>] `；拿不到 → `[seq 未知 · 会话 <id> · 检索入口 memory_call]`（**不猜**）。
- 注入端解析该前缀并优先展示门牌，正文仍截 240 字符。
- 注意：门牌只进 memory-server 的 `topic_summaries`，不参与 DSH 自身的压缩体积校验。

## 3. P2 · 哨兵 doctor（已落地并实跑）

- 路径：`/www/dsh-test-home/.agent-presets/_memory-plugin/memory-doctor.mjs`
- 检查：SELF 文件 / 头部注入位置 / 摘要门牌覆盖率 / 压缩失败计数 / memory-server 健康 / 插件加载与未激活条目 / **本次未覆盖清单**
- 四态 `ok/warn/error/pending`；`pending` 不计为通过；有 error 退出码 2。
- **实跑首轮就抓到真问题**：最近 32 次压缩里有 **2 次失败**（`summary is not smaller than the shadowed content (2653 >= 2235)`）——此前无人发现，正是「坏了没人知道」的实例。

## 4. 未完成 / 待办

1. P1 需触发一次真实压缩，确认 `topic_summaries.summary` 以 `[log:…#seq=` 开头（doctor 的 summary-door 当前为 pending）。
2. SELF.md 内容目前是通用模板，需要按实际身份/硬线定制。
3. 生产未同步：生产插件是另一份 `plugin-v3.js`（1620 行），需移植这三处改动并在 0.1.7 上实测 `systemPrompt.section`。
4. doctor 尚未接入面板/定时；建议每日或每会话跑一次。
