# DSH 0.1.7 agent preset 迁移：目录扫描 → 声明式注册

> **范围**：`harness-memory-task` / `harness-memory` / `harness-memory-daily` / `harness-memory-blank`
> 四个 agent preset，从 DSH **0.1.5「目录扫描格式」** 迁移到 **0.1.7「声明式注册」**。
> **执行环境**：测试机 `/opt/dsh-rc2-test-core`（DSH `0.1.7-rc.2`），`dsh-test.service` `:3091`，
> `DSH_HOME=/www/dsh-test-home`。
> **状态**：2026-09-28 22:22 四个 preset 全部迁移完成并实测通过（证据见 §9）。
> 生产 `/opt/dsh-rc2-core`（`0.1.5-rc.1`，`:3081`，`/www/dsh/home`）**全程未碰**。
>
> 参考产物：[`../tools/preset-017/README.md`](../tools/preset-017/README.md)（部署件快照）
> 生成器：[`../tools/gen-preset-017.py`](../tools/gen-preset-017.py)

---

## 目录

1. [机制差异：0.1.5 vs 0.1.7](#1-机制差异015-vs-017)
2. [三个必须知道的坑](#2-三个必须知道的坑)
3. [热加载事实](#3-热加载事实重要)
4. [四个 preset 对照表](#4-四个-preset-对照表)
5. [关键维护事实](#5-关键维护事实)
6. [迁移操作步骤](#6-迁移操作步骤测试机实做流程)
7. [回滚方法与备份命名约定](#7-回滚方法与备份命名约定)
8. [生产升级清单](#8-生产升级清单)
9. [实测证据](#9-实测证据)
10. [交叉引用](#10-交叉引用)
11. [风险与不确定点](#11-风险与不确定点)

---

## 1. 机制差异：0.1.5 vs 0.1.7

### 1.1 0.1.5 —— 复数包 + **目录扫描**

包：`@deepseek-ai/dsh-agent-presets`（**复数**，`0.1.5-rc.2`）
代码：`.../dsh-agent-presets/lib/index.js`（1823 行）

关键点（行号为该 0.1.5 构建内的位置）：

| 位置 | 内容 |
|---|---|
| L195 | `const USER_PRESET_DIR = ".agent-presets";` |
| L392 | `async function scanRoot(root, harnessBase) { … await readdir(dir, { withFileTypes: true }) … }` |
| L429 | `for (const root of roots) for (const preset of await scanRoot(root, harnessBase)) { … }` |
| L1307 | `path: dshHomePath(USER_PRESET_DIR)` |
| L1294 | `super(ctx, "agentPresets")` —— 服务名就叫 `agentPresets` |
| L1823 | 导出 `discoverPresets, scanRoot, mountPreset, copyComposition, …` |

也就是说：**preset = 磁盘上的一个目录** `$DSH_HOME/.agent-presets/<id>/agent.cordis.yml`，
由插件启动时 `readdir` 扫出来。放个目录进去（重启后）就多一个 preset，不需要改任何配置。

### 1.2 0.1.7 —— 单数包 + **声明式注册**

包：`@deepseek-ai/dsh-agent-preset`（**单数**，`0.1.7-rc.2`）+ 注册表
`@deepseek-ai/dsh-agent-preset-registry`（`0.1.7-rc.2`，812 行）。

`dsh-agent-preset/lib/index.js` **只有 29 行**，全部逻辑就是「把自己这行声明交给注册表」：

```js
import { Service } from "@deepseek-ai/cordis";
import { EntryGroup } from "@deepseek-ai/cordis-plugin-loader";
import z from "@deepseek-ai/schemastery";

var AgentPreset = class {
  ctx; config;
  static inject = ["agentPresets"];
  /** Preserve child expressions until their own plugins activate. */
  static [EntryGroup.key] = true;                    // = Symbol.for("cordis.group")
  static Config = z.object({
    id: z.string().required(),
    name: z.string(),
    description: z.string(),
    order: z.number(),
    plugins: z.array(z.any()).required()
  });
  async *[Service.init]() {
    yield await this.ctx.agentPresets.register(this.config);
  }
};
export { AgentPreset as default };
```

注册表侧（`dsh-agent-preset-registry/lib/index.js` L500 `async register(definition)`）做三件事：
**空 id 报错**、**重复 id 报错**（`Duplicate agent preset: ${id}`）、然后 `activate(record)` 急切加载，
「activation failure remains visible in the roster」。

于是 **preset = 普通 Cordis 组合里的一行**，必须显式 `ctx.agentPresets.register(config)`：

```yaml
- insert:
    - id: preset-<id>
      name: '@deepseek-ai/dsh-agent-preset'
      config:                       # ← PresetDefinition
        id: <id>                    #   必填（registry key）
        name: '<显示名>'            #   可选
        description: '<描述>'       #   可选
        order: 50                   #   可选（排序）
        plugins:                    #   必填，EntryOptions[]
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config: { ... }
```

**`config = PresetDefinition = { id, name?, description?, order?, plugins: EntryOptions[] }`**，
其中 `EntryOptions = { id, name, config?, group?, disabled?, inject? }`。

### 1.3 **0.1.7 不再读 `.agent-presets/`**

这是本次迁移的根本原因，已验证到代码级：

```
$ ls /opt/dsh-rc2-test-core/node_modules/.pnpm/ | grep -i agent-preset
@deepseek-ai+dsh-agent-preset@0.1.7-rc.2_...
@deepseek-ai+dsh-agent-preset-registry@0.1.7-rc.2_...
@deepseek-ai+dsh-client-ui-agent-preset@0.1.7-rc.2_...
```

**0.1.7 core 里根本不存在 `dsh-agent-presets`（复数）包**，也没有任何代码为「发现 preset」
去读 `.agent-presets`（全库 grep 仅命中 README/注释/一个无关的 UI 文案
`client-ui-agent-preset AgentPresetSection id 'agent-presets'`）。

0.1.7 的内置 preset 用的是**同一套声明式写法**，可作范本：
`@deepseek-ai/dsh-web-app/presets/{standard,ptc,minimal,cordis}.patch.yml`，
注册表行在 `dsh-web-app/cordis.patch.yml`（`config.default: standard`）。

> **直接后果**：`$DSH_HOME/.agent-presets/` 下的目录在 0.1.7 里**完全失效**（只剩「源码存档」意义）。
> 测试机上 `liangshen`、`_memory-plugin/` 等目录仍在，但因未写进 patch 而**不进 roster**。

### 1.4 差异对照

| | 0.1.5 | 0.1.7 |
|---|---|---|
| 包 | `@deepseek-ai/dsh-agent-presets`（**复数**） | `@deepseek-ai/dsh-agent-preset`（**单数**）+ `-registry` |
| 服务名 | `agentPresets`（同一个 ctx 服务名） | `agentPresets`（靠 `inject` 注入） |
| preset 载体 | 目录 `$DSH_HOME/.agent-presets/<id>/agent.cordis.yml` | profile patch 里的一行 `EntryGroup` 声明 |
| 发现方式 | `readdir` 扫描（`scanRoot` / `USER_PRESET_DIR`） | 无扫描；只认组合里显式声明的那几行 |
| 注册动作 | 插件自动 `mountPreset` | 必须 `ctx.agentPresets.register(config)`（由 `dsh-agent-preset` 代劳） |
| 配置结构 | 文件顶层 = 插件行数组 | `config.plugins` = 同一份数组 |
| 子插件相对路径基准 | preset 目录 | **声明行所在的 profile 目录**（见坑 1） |
| 新增 preset 的成本 | 建目录 + 重启 | 改 `cordis.patch.yml`（可热挂载）+ 重启求一致 |
| 设置页 | 有 `AgentPresetSettingsSchema` + `settings.register(SETTINGS_NAMESPACE, …)` | 无该 namespace（改由 profile entry config 派生，见交叉引用 [10.2](#102-相关的仓库外文档)） |

---

## 2. 三个必须知道的坑

### 坑 1 —— preset 子插件行的 `name` 以**声明行所在的 profile 目录**为 baseUrl 解析

**不是 preset 目录。** 因此本地插件**必须写绝对路径**，相对路径一律 404。

0.1.5 里这样写是对的（基准是 preset 目录）：

```yaml
  - id: harness-memory
    name: '../_memory-plugin/plugin-v3.js'      # → /…/.agent-presets/_memory-plugin/plugin-v3.js ✅ 0.1.5
```

0.1.7 里会解析到 `profiles/web/../_memory-plugin/plugin-v3.js` → **不存在 → 404**。
正确写法：

```yaml
  - id: harness-memory
    name: '/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js'   # ✅ 0.1.7
```

- 生成器 `tools/gen-preset-017.py` 会自动绝对化 `../` 开头的 `name:`；
  **但不处理 `./` 开头的**（见生成器文件头「已知限制」）。`harness-memory-blank` 的
  `./plugin/plugin.js` 就是**手改**的。
- **推论**：`cordis:include` **不能**用作 preset 内层包装来「恢复相对基准」——
  include 会把 baseUrl 改到 include 文件所在目录，而该目录不在 dsh 的
  `ResolutionRouter` 拦截层内，**裸包名（`@deepseek-ai/...`）解析会失败**。
- 排查特征：journal 里出现模块解析失败/找不到文件，且路径里出现了 `profiles/web/` 前缀。

### 坑 2 —— `@deepseek-ai/dsh-workflow-worker-thread` 在 0.1.7 **已被删除**

必须换成 **`@deepseek-ai/dsh-workflow-ptc`**（provider: `spawn`），否则
**整棵 preset mount 失败**——不是「少一个工具」，而是这**一个 preset 整体挂不上**
（registry 的 `activate` 失败，roster 里表现为该 preset 不可用）。

包存在性验证：

```
0.1.5 (.pnpm): @deepseek-ai+dsh-workflow-worker-thread@0.1.5-rc.2_…      ← 有
0.1.7 (.pnpm): @deepseek-ai+dsh-workflow-ptc@0.1.7-rc.2_…                ← 有
               （无 dsh-workflow-worker-thread）                          ← 已删除
```

生成器已内置替换表：

```python
ROW_SUBSTITUTIONS = {"@deepseek-ai/dsh-workflow-worker-thread": "@deepseek-ai/dsh-workflow-ptc"}
ID_SUBSTITUTIONS  = {"workflow-worker-thread": "workflow-ptc"}
```

> 注意：**源 `agent.cordis.yml` 保持 0.1.5 原样**（测试机上 `harness-memory-task/agent.cordis.yml`
> L214-215 与 `harness-memory/agent.cordis.yml` L259-260 **仍是 `workflow-worker-thread`**），
> 替换只发生在**生成产物**里（快照 L476-477 / L815-816 已是 `workflow-ptc`）。
> 这是有意的：源文件继续可被 0.1.5 读取。

### 坑 3 —— 0.1.7 里 `ctx.logger.info` 在 journal/systemd 里**看不见**

原因链：
- `@deepseek-ai/cordis` 的 `LoggerService` 只把日志写进**内存环形缓冲**；
- `dsh-app-boot` 注册的 exporter **只收集 `warn`/`error`**：

```js
// dsh-app-boot/lib/index.js  (~L4053)
const startupLogs = [];
diagnostics.logger.exporter({
    levels: { default: 2 },
    export: ({ ts, name, type, args }) => {
        if (type === "warn" || type === "error") startupLogs.push({ ts, name, type, args });
    }
});
```

**后果**：`ctx.logger.info(...)` 写进去的「加载成功」标记在部署日志里**根本不会出现**，
容易误判成「插件没加载」。

**做法**：需要日志证据时用 **`console.log`**。
实测样本——`harness-memory-blank/plugin/plugin.js` 保留原 `ctx.logger.info` 调用、
**另加一行 `console.log`**（纯日志，无行为变化）：

```js
export function apply(ctx) {
  ctx.logger?.info?.('[blank-template] plugin loaded');   // 0.1.7 看不见（保留，对 0.1.5 有效）
  console.log('[blank-template] plugin loaded');          // 0.1.7 可见 ← 真正的证据
}
```

重启后 journal 出现 `[blank-template] plugin loaded`（22:22:17）= 空模板桩**确实加载了**。

---

## 3. 热加载事实（重要）

`cordis.patch.yml` 被 **HMR 监视并增量热挂载**：改完**当场部分生效**，不必等重启。

**实测**：追加 daily/blank 两块后，**22:20:04 未重启**即出现 1 条
`[deepmemory] ready (preset plugin P2: relations + cross-turn query + graph route)`
—— 热挂载确实挂上了新加的记忆插件。

**但增量 ≠ 一致**：

> **实测追加后未重启只挂了 1 个 preset，而不是全部。**

因此：

- ✅ 想快速看「新块能不能解析、插件能不能 apply」→ 可以只看 HMR 日志；
- ❌ **完整一致性（4 个 preset 全部在册 + 全部激活正确）必须重启**。
  重启后同一进程打出 **3 条** `[deepmemory] ready`（22:22:18，PID 791326），
  与 hot-mount 时的 1 条形成鲜明对比。

**规则：把「重启 + 四项验证」当作迁移完成的**唯一**判定标准；HMR 只用于早期排错。**

---

## 4. 四个 preset 对照表

| preset id | order | 显示名 | 记忆 | 任务看板 / 子 agent / workflow | 文献 kb | 插件清单来源 |
|---|---|---|---|---|---|---|
| `harness-memory-task` | 50 | 任务型 Agent（长期记忆 / deepmemory） | ✅ 完整 | ✅ 全有 | ✅ | `.agent-presets/harness-memory-task/agent.cordis.yml`（281 行） |
| `harness-memory` | 50 | 记忆增强模式 | ✅ 完整 | ➖ 标准编码 Agent | ✅ | `.agent-presets/harness-memory/agent.cordis.yml`（309 行） |
| `harness-memory-daily` | 51 | 日常问答模式 | ✅ 完整，但 `preset_mode: daily` | ❌ 无 | ❌ | `.agent-presets/harness-memory-daily/agent.cordis.yml`（118 行） |
| `harness-memory-blank` | 52 | DeepSeek Harness 子插件空白模板 | ❌ **设计上完全无记忆** | ❌ 无 | ❌ | `.agent-presets/harness-memory-blank/agent.cordis.yml`（93 行） |

补充说明：

- **blank 的语义**：`harness-memory-blank` **不是**「记忆插件的空配置」，而是
  「给第三方开发子插件的**最小 capability 模板**」——只有 `persona` + `agent-instructions`
  + 一个 **6 行空模板桩**（`blank-template/plugin/plugin.js`，仅 `export function apply(ctx)` 打一行日志）。
  它**不挂任何记忆插件**（README 明写 "This template does NOT mount deepmemory by default"）。
  → **验证项「记忆注入」对它 N/A**；实测其真实回合 runtime-context 里只有
  `sandbox:policy` / `approval:policy`，**无** `[长期记忆召回]`——这是**正确**结果。
- **daily 的 `preset_mode`**：`harness-memory-daily` 仍挂完整 `plugin-v3.js`，
  但用 `config: { preset_mode: daily, budget_profile: daily-default }` 关闭任务看板；
  task 用 `preset_mode: task` + `budget_profile: task-default`。
- **order 50/50/51/52**：写在 `config.order`；同一 order（两个 50）时由 registry 名单顺序决定。

---

## 5. 关键维护事实

### 5.1 三个 preset 共享**同一份** `plugin-v3.js` → 改它要一起回归

`harness-memory-task` / `harness-memory` / `harness-memory-daily`
**三个 preset 指向同一个文件**（快照 L511 / L858 / L972）：

```
/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js
1155 行 · md5 a215b7dbd68c5b75f9d73efd5032cbd7
```

> ⚠️ **改这个文件 = 同时影响三个 preset**。回归必须三个都跑
> （建会话 → 真实回合 → 断言 `[长期记忆召回]` 注入 / `turn/end completed`）。
> 只有 `harness-memory-blank` 不受影响（它不挂记忆插件）。

**判别「修好版 vs 旧副本」的运行时硬证据**（比行数/md5 更直接）：

1. 新插件注册 `memory_source` 工具 —— 旧副本**没有**这个工具；
2. 日志格式 `[deepmemory] session memory cache updated sid=… (total N)` —— 只有新插件里才有。

### 5.2 `harness-memory/agent.cordis.yml` 的插件指向**已修正**

| | 迁移前 | 迁移后 |
|---|---|---|
| 指向 | `./memory-plugin/plugin-v3.js`（preset **自带的旧副本**） | `/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js`（**共享的修好版**） |
| 行数 | **610 行** | **1155 行** |
| md5 | `4d89e6d0c2f0eab575e1cf5512635758` | `a215b7dbd68c5b75f9d73efd5032cbd7` |
| 修复标记 | **0 个** | 含全部修复 |

修正同时落在两处（源文件 + 生成产物）：`agent.cordis.yml` L300-302 的注释与绝对路径，
以及快照 L855-858。源文件备份 `agent.cordis.yml.bak-017adapt-hm-20260928-221434`。

- `harness-memory-daily` **本来就是对的**（源里就是 `../_memory-plugin/plugin-v3.js`，
  经生成器绝对化即可，**源 yml 未改**）。
- `harness-memory-blank` 无记忆插件（设计如此）。

> 为什么 `harness-memory` 会挂旧副本？历史上它是独立维护的一份；后来共享版
> `_memory-plugin/plugin-v3.js` 累积了全部修复（1155 行），自带的 610 行副本没跟上。
> 迁移正好把这个隐患一起修掉。

---

## 6. 迁移操作步骤（测试机实做流程）

对**每个** preset 循环：

```bash
# 0) 备份（命名约定见 §7）
cp $DSH_HOME/profiles/web/cordis.patch.yml \
   $DSH_HOME/profiles/web/cordis.patch.yml.bak-017adapt-<tag>-$(date +%Y%m%d-%H%M%S)

# 1) （仅当源里有 ./ 开头的相对 name）手工改成绝对路径
#    生成器只绝对化 ../，不处理 ./

# 2) 生成声明片段
cd "/www/deepseek harness workspace/dsh-deepmemory"
./tools/gen-preset-017.py \
    --preset-dir "$DSH_HOME/.agent-presets/<preset-id>" \
    --id <preset-id> --order <N> --name '<中文名>' --description '<中文描述>' \
    --out /tmp/preset-<preset-id>.yml

# 3) 追加到 profile patch（**只追加**，既有内容逐字节不动）
cat /tmp/preset-<preset-id>.yml >> $DSH_HOME/profiles/web/cordis.patch.yml

# 4) 重启（HMR 只热挂部分，一致性必须重启）
systemctl restart dsh-test.service

# 5) 四项验证 —— 见 §9
```

**「只追加」是硬约束**：迁移后必须校验既有前缀逐字节未变。本次的校验手法：

```bash
head -865 cordis.patch.yml | sha256sum
# f2f62820511eb586812e941dc1e9388df51d4dee552213941ce2fe97188cd834
# 与备份 .bak-017adapt-dailyblank-20260928-221949 完全一致
```

> 仓库自带的 `AGENTS.md` 有两阶段铁律（测试机 → 生产）与 preflight 清单；
> 本次是**纯配置/patch 追加**，未改任何插件 JS，故不触发 JS preflight，
> 但「重启后真实回合冒烟」这一条**必须做**（只看启动日志零错误不够——参见仓库
> `AGENTS.md` 的 0.1.5 升级事故：日志干净但每个回合都炸）。

---

## 7. 回滚方法与备份命名约定

### 7.1 备份命名约定

统一前缀 **`.bak-017adapt-…`**，便于一次性识别「本次 0.1.7 适配」产生的备份：

| 文件 | 说明 |
|---|---|
| `profiles/web/cordis.patch.yml.bak-017adapt-20260928-212557` | 迁移**开始前**的原始 patch（7582 B） |
| `profiles/web/cordis.patch.yml.bak-017adapt-hm-20260928-221434` | 追加 `harness-memory` 块**之前**（24653 B） |
| `profiles/web/cordis.patch.yml.bak-017adapt-dailyblank-20260928-221949` | 追加 daily/blank **之前**（44965 B，= 865 行完整可用态） |
| `.agent-presets/harness-memory/agent.cordis.yml.bak-017adapt-hm-20260928-221434` | 源 yml（`./` → 绝对路径）改动前 |
| `.agent-presets/harness-memory-blank/agent.cordis.yml.bak-017adapt-blank-20260928-221949` | 源 yml（`./` → 绝对路径）改动前 |
| `.agent-presets/harness-memory-blank/plugin/plugin.js.bak-017adapt-probe-20260928-221949` | 桩加 `console.log` 前 |

> 仓库 `.gitignore` 已忽略 `*.bak` / `*.bak-*` / `*.bak.*` —— 备份**不会**被误提交。

### 7.2 回滚方法

**只回滚 patch（最常见）**：

```bash
# 回到「三块都在、blank 未加」的中间态
cp $DSH_HOME/profiles/web/cordis.patch.yml.bak-017adapt-dailyblank-20260928-221949 \
   $DSH_HOME/profiles/web/cordis.patch.yml
systemctl restart dsh-test.service
```

**回到迁移开始前（完整回滚，源码目录也还原）**：

```bash
cp $DSH_HOME/profiles/web/cordis.patch.yml.bak-017adapt-20260928-212557 \
   $DSH_HOME/profiles/web/cordis.patch.yml
cp $DSH_HOME/.agent-presets/harness-memory/agent.cordis.yml.bak-017adapt-hm-20260928-221434 \
   $DSH_HOME/.agent-presets/harness-memory/agent.cordis.yml
cp $DSH_HOME/.agent-presets/harness-memory-blank/agent.cordis.yml.bak-017adapt-blank-20260928-221949 \
   $DSH_HOME/.agent-presets/harness-memory-blank/agent.cordis.yml
cp $DSH_HOME/.agent-presets/harness-memory-blank/plugin/plugin.js.bak-017adapt-probe-20260928-221949 \
   $DSH_HOME/.agent-presets/harness-memory-blank/plugin/plugin.js
systemctl restart dsh-test.service
```

**注意**：

- **回滚也必须重启**（HMR 不会撤销已热挂载的 preset；且 registry 对重复 id 报
  `Duplicate agent preset`）。
- 回滚后校验：journal 里应只剩**内置 4 个** preset，且不再有 `[deepmemory] ready`。
- 回滚 **0.1.7 → 0.1.5** 是另一件事（要回退 core 版本），不在本文范围；
  **0.1.7 上把 patch 还原成 0.1.5 目录扫描格式 = preset 全部消失**。

---

## 8. 生产升级清单

> 前置铁律（仓库 `AGENTS.md`）：**测试机(3091) 通过 → 才允许碰生产(3081)**。

生产环境：`/opt/dsh-rc2-core` = `0.1.5-rc.1`，`:3081`，`DSH_HOME=/www/dsh/home`。
**当前生产未动**。生产升级到 0.1.7 后，需对**生产 `DSH_HOME`** 重跑同一流程：

- [ ] **0. 先确认 core 真的到了 0.1.7**（0.1.5 上做这套声明式迁移 = preset 全废）
      —— 校验 `.pnpm` 里有 `dsh-agent-preset`（单数）且**没有** `dsh-agent-presets`（复数）。
- [ ] **1. 备份**：`/www/dsh/home/profiles/web/cordis.patch.yml` → `.bak-017adapt-<ts>`。
- [ ] **2. 备份源 preset**：`/www/dsh/home/.agent-presets/harness-memory{,-blank}/agent.cordis.yml`
      以及 blank 的 `plugin/plugin.js`。
- [ ] **3. 手工改 `./` 开头的相对 `name:` 为绝对路径**（生产前缀 `/www/dsh/home/...`；
      生成器不处理 `./`）。
- [ ] **4. 修 `harness-memory` 的插件指向**：确认它指向共享的
      `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`（**1155 行修好版**），
      不是自带的 610 行旧副本。**先核对生产那份 `_memory-plugin/plugin-v3.js`
      的行数/md5 是否与测试机的 1155 行版一致**（生产可能落后）。
- [ ] **5. 对四个 preset 各跑一次生成器**，`--preset-dir` 用**生产** `DSH_HOME` 路径。
- [ ] **6. 追加到生产 `profiles/web/cordis.patch.yml`**（**只追加**），并校验既有前缀
      与备份逐字节一致。
- [ ] **7. 检查第 1–256 行那类既有条目**（provider 路由、`reasoning-guard` 等）
      在生产是否也适用于 0.1.7 —— **不要**把测试机快照整体拷过去（见
      [`../tools/preset-017/README.md`](../tools/preset-017/README.md) §3）。
- [ ] **8. 重启生产 dsh-web**（**确认无 running 会话**再重启 —— 见仓库 `AGENTS.md`
      运维红线 2）。
- [ ] **9. 四项验证**（见 §9）：roster 数量、建会话回显 preset、journal `[deepmemory] ready`
      条数、**真实回合**注入 `[长期记忆召回]`。
- [ ] **10. 记录**：把生产的备份文件名、roster 数量、验证结论写进交接文档。

---

## 9. 实测证据

全部来自测试机 `dsh-test.service`（`DSH_HOME=/www/dsh-test-home`，core 0.1.7-rc.2）：

### 9.1 roster 8 条

`agentPresets` 注册表里最终 **8 个** preset：

| 来源 | 数量 | id |
|---|---|---|
| 0.1.7 内置 | 4 | `standard`、`ptc`、`minimal`、`cordis` |
| 本次迁移 | 4 | `harness-memory-task`、`harness-memory`、`harness-memory-daily`、`harness-memory-blank` |

> 分母可验证：`.agent-presets/` 下的 `liangshen` **没有**写进 patch
> （`grep -n "preset-liangshen" cordis.patch.yml` 无命中），0.1.7 又不扫目录 → **不进 roster**。
> 4 + 4 = 8，与观测一致。

### 9.2 建会话回显

建会话时 `agentPreset=harness-memory`（显示名「记忆增强模式」，order 50）等，
说明声明行被 registry 正确解析并投影到会话。

### 9.3 journal `[deepmemory] ready` ×3

```
Sep 28 22:20:04 zz node[780881]: [deepmemory] config: inject=true card=true extract=true decay=0.01 k=5 thr=4 ws=deepseek-harness
Sep 28 22:20:04 zz node[780881]: [deepmemory] ready (preset plugin P2: relations + cross-turn query + graph route)   ← HMR，未重启，只 1 条
...
Sep 28 22:22:17 zz node[791326]: [blank-template] plugin loaded                                                      ← 桩加载（console.log）
Sep 28 22:22:18 zz node[791326]: [deepmemory] … / [deepmemory] ready   ×3                                            ← 重启后 3 条
```

**3 条 = 三个挂记忆插件的 preset（task / harness-memory / daily）各挂一份；
blank 不挂记忆 → 只有自己的桩标记。**（这两个数字互为交叉验证。）

### 9.4 真实回合注入

真实回合 `turn/end` = `completed`，且注入块里出现 **`[长期记忆召回]`**
（task / harness-memory / daily 三者都验过）。blank 的真实回合也 `completed`，
但 runtime-context 只有 `sandbox:policy` / `approval:policy` —— **符合设计**。

### 9.5 产物指纹

| 项 | 值 |
|---|---|
| `cordis.patch.yml`（迁移完成态） | 1099 行 / 56162 B / sha256 `00eaefc3…706f` |
| 前 865 行 sha256 | `f2f62820…cd834`（既有内容零改动） |
| 共享 `_memory-plugin/plugin-v3.js` | 1155 行 / md5 `a215b7db…` |
| `harness-memory` 旧自带副本 | 610 行 / md5 `4d89e6d0…` |

---

## 10. 交叉引用

### 10.1 仓库内

- [`compat-015-diagnosis.md`](./compat-015-diagnosis.md) —— **0.1.5-rc.2 插件兼容性诊断**。
  本文不重复它的内容：它讲的是 **0.1.5 时代** deepmemory 插件自身的静默失效
  （① 结论摘要 / ①.5 修复状态 / ② 不兼容清单 / ③ 风险与潜在项 / ④ 覆盖范围 /
  ⑤ 建议修复顺序 / ⑥ 不确定的地方）。**两篇的关系**：
  - 那篇解决的是「插件本体在 0.1.5 上不对」；
  - 本文解决的是「0.1.5 → 0.1.7 **载体格式**变了，preset 挂不上去」。
  - 两者叠加才等于「deepmemory 在 0.1.7 上真正可用」。
- [`../tools/preset-017/README.md`](../tools/preset-017/README.md) —— 迁移产物快照的
  结构导读与「不可拷到生产」说明。
- [`../tools/gen-preset-017.py`](../tools/gen-preset-017.py) —— 生成器（含 I/O 约定与已知限制）。
- [`../AGENTS.md`](../AGENTS.md) —— 仓库级 preset 部署铁律与 preflight 清单。
- [`../CHANGELOG.md`](../CHANGELOG.md) / [`../CONTEXT.md`](../CONTEXT.md) —— 变更历史与上下文。

### 10.2 相关的仓库外文档

- `DSH-0.1.7-plugin-settings-migration.md`（**工作区根目录，不在本仓库**：
  `/www/deepseek harness workspace/DSH-0.1.7-plugin-settings-migration.md`）——
  0.1.7 的 **settings API** 迁移（`ctx.settings.register` → `SettingsForms`）。
  **与本文是同一批 0.1.7 适配工作的两条线**：
  - 那篇 = **插件本体**（host/client 半）适配 0.1.7 API；
  - 本文 = **preset 载体格式**（目录扫描 → 声明式注册）适配 0.1.7。
  - 两者**互不替代**：settings 迁好了但 preset 挂不上 → 插件根本不会 apply；
    preset 挂上了但 settings 没迁 → 启动就抛 `ctx.settings.register is not a function`。
  - 测试机上这两件事**都已做完**（settings：2026-09-28 16:56 前后；preset：22:22）。
- `AGENTS.md`（工作区根，运维红线）—— 尤其是重启纪律、0.1.5 升级事故的教训。

### 10.3 0.1.7 官方范本（core 内置）

- `@deepseek-ai/dsh-web-app/presets/{standard,ptc,minimal,cordis}.patch.yml`
- `@deepseek-ai/dsh-web-app/cordis.patch.yml`（registry 声明行，`config.default: standard`）

---

## 11. 风险与不确定点

| # | 风险 / 不确定点 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **生成器只绝对化 `../`，不处理 `./`** | `./` 开头的相对 `name:` 静默保留 → 0.1.7 下 404 | 生成后**人工 grep** `name: '\./` 或 `name: "\./`；blank 已手改 |
| 2 | **HMR 只热挂部分**，易误判「已生效」 | 少挂 preset 而不自知 | 迁移完成的判定 = **重启 + 四项验证**；HMR 只用于早期排错 |
| 3 | **`plugin-v3.js` 三 preset 共享** | 改一处炸三个 | 回归必须三个一起跑；改前 `node --check` + ESM import 冒烟（仓库 `AGENTS.md` preflight） |
| 4 | **生产 `_memory-plugin/plugin-v3.js` 可能落后** | 生产升级时若指向旧副本 → 静默少修复 | 生产升级**第 4 步**必须先核对行数/md5（期望 1155 行 / `a215b7db…`） |
| 5 | **roster 8 条中的「4 内置」随 core 版本走** | 换 core 小版本可能变成 3 或 5 条 | 校验时**看差值**（迁移前后 +4）而不是死抠绝对数 |
| 6 | **`liangshen` 未迁移** | 0.1.7 下它彻底不可用（既不扫目录、patch 里也没有） | 若仍需它，按同一流程补迁移；否则接受淘汰 |
| 7 | **`preset_mode` / `budget_profile` 的语义靠插件自解释** | 0.1.7 core 只校验 `PresetDefinition` 结构，不校验 config 内容 | 误写不会报错、只会静默退化 → 靠真实回合断言注入块 |
| 8 | **本仓库 GitHub 端为公开仓库** | 快照含内网 baseURL / 端口 / 绝对路径（**无凭据值**） | 见 `tools/preset-017/README.md` §5；必要时移出公开镜像 |
| 9 | **0.1.7 的 `ctx.logger.info` 不可观测** | 缺日志证据 → 误判插件未加载 | 用 `console.log`；不要因为「日志里没有」就断定失败 |
| 10 | `DROP_KEYS` 的块删除基于行首锚点 | 可能过度删除嵌套同名键 | 生成后 **diff 人工确认**（本次四个 preset 已逐个确认） |

---

*本文档 2026-09-28 由测试机迁移成果整理入库。生产未动。*
