# 0.1.7 → 0.2.1-rc.2 升级研究（2026-10-23）

## 已实测的事实

1. 0.2.1 core 在独立目录可启动：`/opt/dsh-021-rc2-stage`，0 skipped / 0 inactive（修好 bridge-tools 后）。
2. **但会话创建失败**（staged 副本 profile）：
   - `standard` → `tool "send_message" is already registered`
   - `minimal` → `persistent-bash: tool "bash" is already registered`
   - `harness-memory` → `tool-fs: tool "read" already registered`（glob/job_output/skill/get_goal 同类）
   - 错误统一提示：*for a per-agent variant, register through that agent's agent.ctx instead*
3. 相同 core 版本在 `/www/dsh-test-home` 上工作正常 → 差异在 **profile/home 内容**，不在 core 二进制。
4. 测试机 patch 与生产 patch 的 preset 段（tool-fs 等）**逐行相同**；bundles 略有差异（测试有 synapse/livetaskboard/better-display，生产有 bridge-tools）。
5. 0.2.1 的其它破坏性变化（已遇到/已修）：
   - 插件 peer 门槛 + `compatibility.json` 豁免机制（deepmemory/literature/shell-notify 需要豁免）
   - `toolFilter` 被移除
   - `webRuntime` → `webApp`（video-preview 0.1.4 因此 pending）
   - 工具 JSON Schema 校验变严（bridge-tools 27 处旧写法 + 1 处 `type:'enum'`）
   - 0.2.1 的 token 端点/请求形态未变（RPC 正常，auth 303）

## 结论（暂定）

0.2.1 把「工具注册」变成**每个作用域只能注册一次**，per-agent 变体必须走 `agent.ctx`。
生产 profile 是从 0.1.7 原样拷来的，其中某些条目在 0.2.1 下会与 core 默认工具面合并出重复注册。
重复出现在所有 preset ⇒ 更可能是 **profile 级条目或 home 级内容**导致，而不是单个 preset。

## 优雅迁移方案（建议）

**不要手工删 preset 里的 tool 行**（脆弱、易漏、以后生成器会写回）。改为：

1. **以测试机 0.2.1 profile 为模板重建生产 profile**：bundles、preset 定义、patch 条目都取 0.2.1 已验证版本；
2. 把生产**特有配置**作为薄补丁叠加：provider（uuapi-astra 等）、模型默认值、sidebar 配额、reasoning-guard、memory 插件绝对路径；
3. 第三方插件按测试机版本 + `compatibility.json` 豁免；本地插件（bridge-tools）用修复版；
4. 验收脚本固定为：启动 0 skipped/0 inactive → `session/create` × 3 个 preset（standard/minimal/harness-memory）→ `prompt` 一轮 completed → 会话列表可见。

## 下一步（bisect）

1. 空 patch 启动 staged → 若 `standard` 可建会话 ⇒ 问题在 patch，逐块加回定位；
2. 若空 patch 仍失败 ⇒ 问题在 home（settings/其它 profile 文件），对比测试机 home 逐项。
3. 定位后按上面「重建 profile」路线实施，不采用删行法。

## 补充实验（2026-10-23 下午）

- **空 patch（`[]`）+ 原 bundles**：`standard` 仍报 `tool "send_message" is already registered`。
  ⇒ **重复注册与 preset / cordis.patch.yml 无关**，问题在 profile 的 bundle 组合或 home 遗留状态。
- `dsh-base` 0.2.1 与 0.1.7 的 bundle patch **都含 16 个 tool 行**（bash/fs/fs-search/jobs/skill/goal/todo/web/subagent…）
  ⇒ 0.2.1 并没有“核心工具不再由 bundle 注入”；base 一直在 profile 层注入工具。
- 测试机 0.2.1 的 harness-memory preset 与生产副本的 preset **id 列表基本一致（30 vs 31，差异仅边界）**
  ⇒ 测试机能跑说明 preset 写法不是根因。
- 空 bundles 启动无法提供 web 界面（web-app 本身是 bundle），无法用该方式判定 core 单独行为。

## 结论（更新）

- 不是“插件不用写 preset 了”。preset 仍然声明 agent 侧插件；base 的 profile 级工具注入两版都有。
- 重复注册来自 staged **profile bundle 组合**（最可疑：`@deepseek-ai/dsh-experimental-agent-team-profile`、`auto-review`、`bridge-tools` 或 home 遗留）。
- 下一步：以测试机 profile 的 bundle 列表为基线，逐项加回定位；最终按“测试机模板重建生产 profile”落地。
