# DSH 0.1.5-rc.2 插件兼容性诊断

**诊断日期**：2026-09-22
**核心版本**：`/opt/dsh-rc2-core`（0.1.5-rc.2）
**执行方式**：astra（`uuapi-astra/gpt-6-astra`，xhigh）**只读**静态诊断 + **主 agent 逐条抽查核实**

> ⚠️ **来源与完整性说明（必读）**
> astra 在交稿前的最终消息中**失败**，**未交付完整报告正文**。它最后确认：静态诊断已完成，归并为 **7 类不兼容 + 4 类风险**，生产 8 条模型路由均显式配置了合法 effort（默认 `max` 未触发）、生产旧 anysearch 未挂载。
> 本报告由主 agent 依据 astra 在会话中**逐条给出的发现**，**逐条抽查核实后**撰写。每条均标注【已核实】/【未核实】及核实方式。**未核实的项不作为结论使用。**

---

## ① 结论摘要

当前**已在生产中实际发生**的不兼容为 **2 类，且都是"静默失效"** —— 不抛错、日志不可见：

| 级别 | 问题 | 状态 | 影响面 |
|---|---|---|---|
| 🔴 | `[约束前提]`（bias 全局行为约束）**从未注入** | **已发生** | 生产默认预设 = 所有会话 |
| 🟠 | `liangshen` persona 段过滤**恒为空** | **已发生** | 使用 liangshen 预设的会话 |
| 🟠 | deepmemory 注入刷新**滞后一次** assembly | **已发生**（影响小） | 生产默认预设 |
| 🟡 | 生产 provider 档位表缺 `max`/`xhigh`，而默认 effort=`max` | **潜在未触发** | 新建自定义路由时会踩 |
| 🟡 | `liangshen` 冷启动历史回放被跳过 | 风险 | 恢复会话时推广状态可能丢失 |
| 🟡 | 测试机 `dsh-synapse` 裸 `session.events` | 仅测试机 | 该插件自身 |
| ⚪ | 生产 `dsh-anysearch` 静态 import 已删除 API | 未挂载 | 仅潜在启用事故 |

**核心判断**：这批问题里最危险的不是"崩溃"，而是**静默失效** —— 前三条都不会在日志里留下任何痕迹（跳过分支 + 外层 try/catch），只靠看日志永远发现不了。

---

## ② 不兼容清单（均【已核实】）

### 2.1 🔴 `[约束前提]` 从未注入（最严重）

- **位置**：`/www/dsh/home/.agent-presets/_literature-kb-plugin/plugin-v1.js:89-90`
- **代码**：
  ```js
  if (assembly && typeof assembly.push === 'function') {
    assembly.push({ role: 'system', content: `[约束前提]（来自 literature bias 库，均须遵守）：\n${text}` })
  }
  ```
- **核心事实**：`dsh-system-prompt/lib/types/index.d.ts:103-107` 的 `PromptAssembly` = `{sections, contexts, tools, variables}` —— **没有 `push`**，该 `.d.ts` 中 `"push"` 出现 **0 次**。故条件恒假。
- **挂载点**：`harness-memory-task/agent.cordis.yml:256` → `../_literature-kb-plugin/plugin-v1.js`（注释："轨 B：知识库约束注入（`[约束前提]` 段——6260 bias 知识；预算外）"）
- **影响**：`harness-memory-task` 是 `settings.yaml:16` 指定的**生产默认预设** → **所有会话**都收不到 bias 库的全局行为约束。
- **为何长期未被发现**：`typeof` 判断为假只是**静默跳过**，外层还有 `catch` 兜底 → 日志零痕迹。
- **核实方式**：主 agent 读取插件 `:78-94` 全文 + 核心 `PromptAssembly` 类型定义 + `grep -c push`（=0）+ 挂载行。

### 2.2 🟠 `liangshen` persona 段过滤恒为空

- **位置**：`/www/dsh/home/.agent-presets/liangshen/tool-bootstrap.mjs:64`（集合）、`:511`（过滤）
- **代码**：`const PERSONA_SECTION_NAMES = new Set(['deployment:persona', 'persona'])` → `assembled.sections.filter(s => PERSONA_SECTION_NAMES.has(s?.name))`
- **核心事实**：0.1.5 的段名已改为 `deployment:persona-prefix` / `deployment:persona-suffix`
  （`dsh-system-prompt/lib/index.js:54,56`；`lib/types/index.d.ts:157,159`）→ **两个旧名都对不上，过滤结果恒为空**
- **影响**：persona 相关链路（含工具引导/推广）**静默失效**。
- **核实方式**：主 agent 读取插件两处 + 核心两处常量定义。

### 2.3 🟠 deepmemory 注入刷新滞后一次 assembly

- **位置**：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js:578`（注册）与 `:618-621`（刷新）
- **代码事实**：`:578` 注册的是 **`systemPrompt.context({name:'deepmemory'})`**（全文件唯一注册点），而 `:618` 修改的是 `assembled.sections` 中 `name === 'deepmemory'` 的项 —— `sections` 里不存在该项，**`map` 恒不匹配**。
- **时序事实**（决定后果量级）：
  - 核心 `dsh-system-prompt/lib/index.js:308` 的 `async assemble(context={})` 是**方法**，`:344-347` 在其方法体内**每次调用**都重新执行 `entry.text(context)`；
  - 插件 `:584` 的 `entry.text` 读 `memoryCache`（当时值），`:557` 在瀑布内 `await` 刷新后写入新缓存。
  - → **本次瀑布内的刷新不回填本次 contexts；下一次 assembly 才能看到**（若事件侧预刷新更早完成则可能直接命中）。即**滞后一次**，**不是"永不刷新"**。
- **对照**：`harness-memory/memory-plugin/plugin-v3.js:322` 注册的是 **`section`**、`:362` 改 `sections` —— 类型一致故**正确**。可见是移植时把 `section` 误写成 `context`、却保留了改 `sections` 的代码。
- **修复方向**：应改为更新 `assembled.contexts`；**不建议**为迁就错误 `map` 而把动态记忆退回注册成 `section`（context 与 section 语义不同）。
- **核实方式**：主 agent 读取插件三处 + 核心 `assemble()` 方法体 + `grep` 确认注册仅一处。

### 2.4 ⚪ 生产 `dsh-anysearch` 静态 import 已删除 API（未挂载）

- **位置**：`/www/dsh/home/profiles/web/node_modules/dsh-anysearch/lib/index.js:16`
- **代码**：`import { installSettingsSection, settingsNamespace } from '@deepseek-ai/dsh-settings';`（两导出已于 0.1.2 移除）
- **但**：生产 `package.json` 的 bundles **未引用它**，`cordis.patch.yml` 已摘除 → **未被加载**，属**安装残留**，**不构成启动错误**。
- **若被启用**：**fail-loud 而非静默** —— rc2 `cordis-plugin-loader/index.js:519-529` 抛 import 错误、`:97-121` 聚合回滚；`dsh-app-boot/index.js:1434-1438` 的 `assertEntriesLoaded` 判启动失败、`:1407-1409` **`exit(1)`**。
- **⚠️ 与 AGENTS.md 的冲突**：AGENTS.md 第 1 条"`dsh-tavily-search` 与 `dsh-anysearch` 均已弃用、装上必失败"**已与实测不符** —— **测试机上这两个包已移植**（见 2.5），反而是**生产那份 anysearch 是未移植旧版**。（本节结论为 astra 报 + 主 agent 核实生产/测试两侧代码差异）
- **核实方式**：主 agent 读取生产 `:16`、测试机 `:52-60`、包存在性对照、`package.json`/`patch` 引用检查。

### 2.5 测试机 `anysearch`/`tavily-search` 已移植（不兼容的**反面**证据）

- **测试机** `/www/dsh-test-home/profiles/web/node_modules/dsh-anysearch/lib/index.js:52-60` 已有：
  ```js
  function installSettingsSectionCompat(ctx, ns, schema, entry, hooks) {
    const settings = ctx.get('settings');
    if (settings !== undefined && typeof settings.installSection === 'function') {
      return settings.installSection(ctx, ns, schema, entry, hooks);
    }
    ctx.inject(['settings'], (sctx) => { const scope = sctx.settings.register(ns, schema, {…}) })
  ```
  → 先特检 `installSection`，否则回退 `ctx.settings.register` ✓
- **包存在性**：`anysearch` 生产有/测试机有；`tavily-search`、`synapse` **仅测试机有**（生产无）。
- **核实方式**：主 agent 读取两侧文件 + 目录存在性对照。

---

## ③ 风险与潜在项

### 3.1 🟡 生产 provider 档位表漂移（潜在未触发）

- **差异**：`dsh-custom-provider-reasoning/lib/index.js:95`
  - 生产：`DEFAULT_LEVELS = { off: null, low: "low", medium: "medium", high: "high" }`（**4 档**）
  - 测试：`{ …, xhigh: "xhigh", max: "max" }`（**6 档**）
  - 完整文本 diff 的**唯一 opcode 即 `:95` 替换**，`THINKING_LEVELS` 无差异
- **风险链**：两边 `:145` 均为 `defaultEffort: …default("max")`；`:319-320` 对缺少 `profile.reasoning` 的路由写 `max`；核心 `dsh-llm-pi-ai/lib/index.js` 的 `resolveReasoningLevel()` 对不支持的 effort **直接 throw `UNSUPPORTED_REASONING_EFFORT`**（不降级）。
- **可达性【已核实】**：生产 `settings.yaml` 中 **8 条自定义路由全部显式配置了 `reasoning`**（5×`high` + 3×`max`），且均在各自模型的 `reasoningEfforts` 内 → **当前无缺省路由，默认值未被触发**。
- **触发条件（写明）**：生产**新建**自定义 provider 路由、或任何未显式给 `reasoning` 的入口 → 落到 `max` → 4 档表不含 → 请求失败。
- **建议**：优先把 `defaultEffort` 的生产默认值改为**表内已存在的 `high`**（**收窄**）；**不建议**为对齐测试机而直接补 `max`/`xhigh`（会**虚构**各 endpoint 的能力），"与测试六档对齐"应待**供应商确认**各 endpoint 实际支持档位后再做。
- **核实方式**：主 agent 读取两侧 `:95`/`:145` + 核心 `resolveReasoningLevel` + 统计 `settings.yaml` 的 `reasoning` 条数与档位分布（只取档位，未读取 key/baseURL）。

### 3.2 🟡 `liangshen` 冷启动历史回放被跳过

- **位置**：`liangshen/tool-bootstrap.mjs:362`
- **代码**：`const events = session?.events; if (!Array.isArray(events)) return` —— 防御到位（不崩），但**回放整个被跳过**。
- **补充事实**：核心 `Session` 的 constructor seed **不发 `session/event`**（`dsh-session/lib/types/index.d.ts:123-126`），故 live hook 补不回冷启动历史。
- **影响**：恢复会话时的**推广状态可能丢失**。
- **核实方式**：主 agent 读取插件 `:359-366`（另两条核心依据为 astra 报，未逐一核实）。

### 3.3 🟡 测试机 `dsh-synapse` 裸 `session.events`

- **位置**：`/www/dsh-test-home/profiles/web/node_modules/dsh-synapse/index.js:203`
- **代码**：`for (const event of session.events) { … }` —— **无 `Array.isArray` 防御**；外层 `:758` catch 仅记日志。
- **影响**：会抛 TypeError；影响隔离在该插件内（生产**未安装**此包）。
- **核实方式**：主 agent 读取 `:201-206` + 包存在性对照（生产无）。

---

## ④ 覆盖范围

**【已核实】**（主 agent 亲自执行）：

- `_memory-plugin/plugin-v3.js`（注册点、刷新处、缓存读写三处）
- `_literature-kb-plugin/plugin-v1.js`（assemble 钩子全文）
- `liangshen/tool-bootstrap.mjs`（`:64`/`:362`/`:511` 三处）
- `harness-memory/memory-plugin/plugin-v3.js`（`:322`/`:362` 对照）
- 核心侧：`dsh-system-prompt`（`PromptAssembly` 类型、`assemble()` 方法体、`PERSONA_*_SECTION` 常量）、`dsh-llm-pi-ai`（`resolveReasoningLevel`）、`dsh-session`（`snapshotEvents` 相关，前序轮次）
- 生产/测试两侧：`dsh-anysearch`、`dsh-custom-provider-reasoning`、`dsh-synapse` 的存在性与关键行
- 生产 `settings.yaml` 的 `reasoning` 条数与档位分布（未读取敏感字段）

**【astra 报告、主 agent 未逐条核实】**（**不作为结论**，仅供参考）：
- 覆盖统计：生产 12 个 preset JS/MJS、8 个顶层 `dsh-*` + 223 个 scoped 包；测试 13 个、9 + 57
- "scoped 整树核心 API 检索零命中"
- 测试机 scoped 唯一命中：旧 `@deepseek-ai/dsh-web` 导入 `HarnessError`（该导出 rc2 仍保留）
- 测试机 `_worktree-plugin:171`/`:201` 同款 `context`/`sections` 错位，但**未挂载**
- `dsh-app-boot` 的 `assertEntriesLoaded`/fail-loud 具体行号（`:1434-1438`、`:1407-1409`）

**【明确未覆盖】**：
- **完整六节报告正文**：astra 在交稿前失败，**未交付**；本报告为其发现的重建版
- `harness-memory-daily` / `harness-memory-blank` 等其余预设的逐文件扫描结果
- profile 下 `dsh-literature`、`dsh-whale-widget`、`dsh-better-sidebar`、`@huanlin/*`、`dsh-video-preview` 的逐包结论
- 各插件的**运行时**行为验证（本次为**纯静态**诊断，未执行任何插件、未重启任何服务）

---

## ⑤ 建议修复顺序（**均未实施**，待用户决定）

| 顺序 | 项 | 理由 |
|---|---|---|
| 1 | `_literature-kb-plugin` 的 `[约束前提]` 注入 | 生产默认预设、**当前完全失效**、影响所有会话 |
| 2 | `liangshen` persona 段名 | 已发生、链路静默失效、改动局部（常量集合） |
| 3 | 生产 `defaultEffort` 默认值 → `high` | 一行改动、**收窄**语义、消除潜在事故 |
| 4 | `_memory-plugin` 用 `assembled.contexts` 修正刷新 | 影响较小（滞后一次），但属正确性 |
| 5 | `liangshen` 冷启动回放 | 需设计（constructor seed 不发事件） |
| 6 | 清理生产 `dsh-anysearch` 残留 | 未被引用，仅整洁性 |
| 7 | 测试机 `dsh-synapse` 加防御 | 仅测试机、隔离 |

**注意**：1 与 4 都位于 `system-prompt/assemble` **核心链路**，改动风险高（历史上有过"插件在该钩子抛错 → 炸掉所有会话每个回合"的事故）。任何改动都应先测试机四步 preflight + 真实 prompt 冒烟。

---

## ⑥ 不确定的地方

1. **astra 的覆盖统计未经我核实**（见 ④）——若需要"零命中"这类结论作数，应重跑一次可复核的扫描。
2. **本次为纯静态诊断**：所有结论基于源码阅读，**未在任何运行时验证**（未启动插件、未重启服务、未发测试消息）。尤其"静默失效"类结论，建议用一次真实会话 + 定向日志埋点确认。
3. `dsh-persona` 侧我只核到 `dsh-system-prompt` 的段名常量，**未核实** astra 提到的 `dsh-persona/lib/index.js:36-46`。
4. 3.2 的核心依据（constructor seed 不发事件）为 astra 报告，我未逐一核实。
5. 生产 `settings.yaml` 我仅统计了 `reasoning` 档位分布，**未核对每条路由与其模型 `reasoningEfforts` 的对应关系**（那部分依赖 astra 的检查）。
