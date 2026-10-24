# docs/ 文档索引与归档状态

> **文档状态**：现行（2026-10-21 建立）
> **范围**：`/www/deepseek harness workspace/dsh-deepmemory/docs/`
> **说明**：本索引对每份文档给出一句话摘要 + 状态。状态判定依据是**当前代码/部署事实**，
> 不是文档自身的"最新"声明。
>
> **数量校正**：本次盘点实际为 **26 份既有文档**（任务描述中的"27 份"多计 1 份；
> 已逐文件 `ls` 核对）。加上本次新建的 `ARCHITECTURE.md` / `PROVENANCE.md` / `INDEX.md` 共 29 份。

**状态图例**

| 状态 | 含义 |
|---|---|
| 🟢 **现行** | 结论仍与代码/部署一致，可直接参考 |
| 🟡 **部分过时** | 主体仍有效，但行号、环境基线或实施状态已过期 |
| ⚪ **历史归档** | 仅作决策/事故留痕，不应作为实施依据 |

---

## 一、架构与来源（本次新建）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 模块级架构：四份插件/两份 server 的副本谱系、三层依赖方向、行号级模块表、端到端数据流、端口/文件/环境变量清单、隐藏耦合与可插拔点 | 🟢 现行（2026-10-21） |
| [`PROVENANCE.md`](PROVENANCE.md) | 来源与出处：自研/借鉴 AstrBot/第三方库与模型、许可证、`billion-context`/`dsh-evil-mcp`/`reconcile-agent-memory` 均"参考未采纳" | 🟢 现行（2026-10-21） |
| [`INDEX.md`](INDEX.md) | 本文件：26 份既有文档的分类、摘要、状态 | 🟢 现行 |

---

## 二、契约类（contract）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`deepmemory-library-model-contract.md`](deepmemory-library-model-contract.md) | 知识库模型扩展契约 v0.2：分库（bias/core/eco/project/runtime）、归档、文档↔记忆溯源、双轨注入 | 🟢 现行（接口已实现，契约仍为准） |
| [`deepmemory-literature-export-contract.md`](deepmemory-literature-export-contract.md) | 契约修订 v0.3.1：向 literature 导出"带原始对话的归档"（since/workspace/敏感策略/复用 source 端点） | 🟢 现行（literature 对接面） |
| [`dsh-literatum-contract.md`](dsh-literatum-contract.md) | 独立插件 dsh-literatum 的冻结契约：文献/证据/知识三层 + 溯源链 | 🟢 现行（独立项目，非 deepmemory 内部） |
| [`dsh-literatum-dev-plan.md`](dsh-literatum-dev-plan.md) | dsh-literatum 开发方案（绝对路径基线、库表、API、里程碑） | 🟢 现行（独立项目） |
| [`adaptation.md`](adaptation.md) | 适配新环境的操作指南：所有参数在 memory-server `settings` 表，改配置不改代码 | 🟢 现行（运维向） |

> 契约类的共同点：**描述的是对外承诺**。若代码与契约冲突，以契约为准并修代码；
> 本次梳理未发现 ARCHITECTURE 与三份契约的直接矛盾。

---

## 三、评审类（review）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`astra-plugin-review.md`](astra-plugin-review.md) | 2026-09-15 astra 第一轮全插件审核（S01–S16 + N01–N25），基准是 3462/3718 行的旧 server | ⚪ 历史归档（行号/基准已过时；发现已被 fix-delivery 追踪） |
| [`codex-review.md`](codex-review.md) | codex 本体审核：鉴权可选、workspace 隔离绕过、v2 写路径绕过脱敏、purge 必败等 10 项高危 | ⚪ 历史归档（多数已修，见 fix-delivery） |
| [`codex-review-2.md`](codex-review-2.md) | codex 二轮：设计语义层三缺陷（无"当前可信事实"、相似=相同、固化次数=确认次数） | ⚪ 历史归档（结论已演进为 `semantic-model-v04.md`） |
| [`adversarial-review-findings.md`](adversarial-review-findings.md) | 2026-09-16 对 astra 修复的事后对抗性验证：无回归，3 个未修缺口 + 1 个必须动作（磁盘修复未在运行进程生效） | ⚪ 历史归档（验证已完成，当前状态见 ARCHITECTURE §5） |

---

## 四、计划类（plan）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`preset-017-migration.md`](preset-017-migration.md) | DSH 0.1.7 preset 迁移：目录扫描 → 声明式注册，四个 preset 在测试机全量验证 | 🟢 现行（2026-09-28，最新迁移记录） |
| [`semantic-model-v04.md`](semantic-model-v04.md) | v0.4 语义模型设计稿：assertions/assertion_events、当前可信事实、独立确认、争议链 | 🟡 部分过时（仓库已实现，**生产未上**；见 ARCHITECTURE §0.2） |
| [`s05-p1-deployment-decision.md`](s05-p1-deployment-decision.md) | P1（断言事件机制）是否上生产的决策分析（生产/仓库/测试机三方 md5 对照） | 🟡 部分过时（决策仍有效，行号/md5 已变） |
| [`workbench-plan.md`](workbench-plan.md) | 非线性工作台计划：项目结构树 + 统一会话管理 + 子节点继承上下文 | 🟢 现行（规划中，独立项目，未实施） |
| [`deepmemory-upgrade-plan.md`](deepmemory-upgrade-plan.md) | 升级计划 v1（绝对路径基线）：生产/测试/库/工具链的真实路径表 | 🟡 部分过时（环境基线仍准；升级步骤已被 `preset-017-migration.md` 取代） |
| [`deepmemory-next-plan.md`](deepmemory-next-plan.md) | 2026-09-01 的"下一步"：命中率检查脚本 + 嵌入迁移工具 + 生产同步 | ⚪ 已过时（三项均已完成或改道） |
| [`session-memory-migration-plan.md`](session-memory-migration-plan.md) | 会话记忆治理 v0.6：跨会话迁移、会话库 key、归档清理、状态卡/任务板 AI 自动更新 | ⚪ 历史归档（主体已落地为 v2 API） |
| [`v2-plan.md`](v2-plan.md) | v2.1 讨论稿：对话状态、分层记忆、按需召回 | ⚪ 已过时（v2 已实现，被后续 v0.4/契约文档取代） |
| [`plan-deepmemory-side.md`](plan-deepmemory-side.md) | deepmemory 侧对接 literature 的改动清单（R1/R2/R3/R5/R6） | ⚪ 历史归档（已实施，见 CHANGELOG） |
| [`plan-literature-side.md`](plan-literature-side.md) | literature 侧改动方案：文献不做向量、知识走向量 | ⚪ 历史归档（literature 侧已独立演进） |
| [`memory-architecture-proposal.md`](memory-architecture-proposal.md) | 记忆系统改造方案（待审）：输入构成实测 + 注入/压缩/检索的改造提案 | 🟡 **已被 `ARCHITECTURE.md` 取代/补充**（方案性内容保留参考） |

---

## 五、诊断与迁移（diagnosis / migration）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`compat-015-diagnosis.md`](compat-015-diagnosis.md) | DSH 0.1.5-rc.2 插件兼容性诊断：7 类不兼容 + 4 类风险（含来源完整性声明） | ⚪ 历史归档（诊断对象已升级到 0.1.7） |
| [`deepmemory-fix-delivery.md`](deepmemory-fix-delivery.md) | 审核修复交付说明：批次 1–5 的落地状态、文件、验证 | ⚪ 历史归档（批次状态已过期） |
| [`n17-session-config-plan.md`](n17-session-config-plan.md) | N17 会话级配置按会话解析的只读分析与实施方案（含"测试库指向生产"高危发现） | ⚪ 历史归档（对象是 988 行旧插件；**风险教训仍有效**） |
| [`knowledge-visibility-report.md`](knowledge-visibility-report.md) | "知识库看不到"排查：后端 101 条全通，疑似前端缓存/workspace 参数 | ⚪ 历史归档（2026-09-06 一次性排查） |
| [`deepmemory-literature-handoff.md`](deepmemory-literature-handoff.md) | deepmemory ← literature 专属对接移交包（契约 + 依赖接口面 + 待实施点） | ⚪ 历史归档（移交已完成） |

---

## 六、运维（operational）

| 文档 | 一句话摘要 | 状态 |
|---|---|---|
| [`operational-notes.md`](operational-notes.md) | 运营红线：禁止直接 kill dsh web（截断 zstd 帧）、必须走安全重启脚本 | 🟢 现行（**必读**） |
| [`adaptation.md`](adaptation.md) | 适配指南（见契约类） | 🟢 现行 |

---

## 七、已过时 / 不应再作为依据的文档

| 文档 | 过时原因 | 现行替代 |
|---|---|---|
| `v2-plan.md` | v2 已实现并继续演进 | `ARCHITECTURE.md` §2.2 + 契约类 |
| `deepmemory-next-plan.md` | 三项任务已完成/改道 | `preset-017-migration.md` |
| `deepmemory-upgrade-plan.md` | 升级步骤基于 0.1.5 目录扫描 | `preset-017-migration.md` |
| `astra-plugin-review.md` | 审核基准是 3462/3718 行旧文件 | `ARCHITECTURE.md` §5（现状风险） |
| `codex-review.md` / `codex-review-2.md` | 发现已修复或已演进 | `semantic-model-v04.md` + `deepmemory-fix-delivery.md` |
| `deepmemory-fix-delivery.md` | "待重启生效"等状态已不成立 | `ARCHITECTURE.md` §0（副本谱系） |
| `n17-session-config-plan.md` | 分析对象 988 行旧插件 | `ARCHITECTURE.md` §5.5（配置覆盖风险） |
| `compat-015-diagnosis.md` | 0.1.5 专用 | `preset-017-migration.md` |
| `memory-architecture-proposal.md` | 方案性描述，非现状 | **`ARCHITECTURE.md`** |

---

## 八、文档之间的关系（阅读顺序建议）

```
想快速理解系统
  └─ ARCHITECTURE.md（§1 依赖图 → §2 模块表 → §3 数据流）

想知道"为什么这么设计"
  └─ memory-architecture-proposal.md（历史方案）
     └─ v2-plan.md（v2 动机，已过时）

想知道"接口承诺"
  └─ deepmemory-library-model-contract.md
     └─ deepmemory-literature-export-contract.md

想知道"当前能不能这么改"
  └─ preset-017-migration.md（0.1.7 声明式注册）
     └─ ARCHITECTURE.md §5（隐藏耦合）
     └─ PROVENANCE.md（依赖与许可）

出事故时
  └─ operational-notes.md（重启红线）
     └─ ARCHITECTURE.md §4（端口/文件/token）
     └─ ARCHITECTURE.md §5（已知耦合）
```

---

## 九、维护约定（建议）

1. **新增文档**必须在本索引登记：分类 + 一句话摘要 + 状态。
2. **状态变更**（如 v0.4 上生产后）要同步更新本索引与 `ARCHITECTURE.md` §0/§5。
3. **行号级文档**（review/diagnosis）一旦代码变动超过 50 行，应标 🟡 或 ⚪，
   不要在旧行号上继续追加结论。
4. 契约类文档的修改需**同时**改代码与索引，避免"契约说 A、代码做 B"。
