# deepmemory v2 完整方案（记忆取代上下文 · 双域分离 · 可编辑状态栏）

> 版本：v2.0 草案 | 状态：待用户确认
> 目标：①记忆大量取代上下文窗口 ②消除抽取遗漏（干过什么/结果） ③工作/生活双域完整机制 ④错误记忆可剔除 ⑤敏感信息拦截

---

## 1. 背景与问题

| # | 问题 | 现状证据 |
|---|---|---|
| 1 | 上下文窗口不可持续增长，compaction 丢细节 | 长会话依赖压缩，压缩即失忆 |
| 2 | 抽取大量遗漏 | episode 仅占 4.7%；过程/结果/教训不落库 |
| 3 | 重要性通胀 | 58% 记忆 ≥0.7，"聊天历史优化"被标 0.8/0.9 |
| 4 | 工作/生活不分 | 生活内容也会更新工作区状态卡 |
| 5 | 错误记忆无剔除路径 | 只能手动面板删除 |
| 6 | 状态卡只读 | LLM 全权维护，用户不能改 |

---

## 2. 总体架构

```
┌────────────────────────── 每轮上下文（token 预算可调） ──────────────────────────┐
│ ① 短期原文    最近 N 轮消息原文，原生保留（压缩机制接管，记忆系统不干预）          │
│ ② 记忆注入    预算 injection.budget_tokens（默认 4000，0 关闭 / 8000 上限）      │
│    组装顺序（预算内依次填充，超出即截断）：                                      │
│      a. 域状态卡（工作区状态卡 / 生活画像卡）        —— 固定 ~400 token          │
│      b. 语义记忆 Top-K（按当前对话域优先）            —— 每条 ~60 token          │
│      c. 过程记忆 Top-K（干过什么+结果）               —— 每条 ~50 token          │
│      d. 引导句（提示模型可用 recall/forget 深挖）     —— ~40 token               │
│ ③ 深挖通道    模型主动 memory_recall / memory_briefing，不占注入预算              │
└──────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────── 记忆库三层 ──────────────────────────┐
│ 热库 active      语义条目 + 过程记忆 + 原子（参与检索与注入）      │
│ 冷库 cold        过程记忆衰减到期移入（不进检索/注入，可查询恢复） │
│ 归档 archived    错误/冲突/手动删除（superseded/archived 可追溯） │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. 记忆模型（五类载体）

| 载体 | 表 | 内容 | 生命周期 | 域 |
|---|---|---|---|---|
| 语义条目 | documents | fact/decision/preference/plan/episode 一句话 | 长期，重要性锚定状态卡价值 | work+life |
| 过程记忆 | process_memories | 一次回复=一组：做了什么+结果+关键产出+失败点，组内原文全量落库 | **15 天衰减（可配置）→ 冷库** | work 为主，life 可关 |
| 记忆原子 | atoms | 独立事实单元，TTL+强化 | 现有机制保留 | work+life |
| 工作区状态卡 | workspace_cards | goal/current_plan/key_decisions/in_progress/next_steps | 常驻，**可编辑看板** | 仅 work |
| 生活画像卡 | persona_profiles | 基础档案/当前生活状态/生活事件线 | 常驻，**可编辑** | 仅 life |

---

## 4. 写入路径（四通道）

### 4.1 过程记忆通道（治"漏"的核心）

```
触发：每次 agent 回复结束（turn-stopping）
分组：一次回复 = 一组（用户已拍板）
流程：
  捕获该回复内的全部工具调用（名称+参数摘要+结果摘要）与消息
  → 组内原文全量落库（原文事件表，不截断）
  → 生成概要：做了什么（1句）｜结果如何（1句）｜关键产出（≤3项）｜失败点（如有）
  → 写入 process_memories（importance 默认 0.3，ttl 15 天）
  → 生命周期到期 → 移入冷库（不进检索、不进注入）
```

数据模型：

```sql
CREATE TABLE process_memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL,           -- 一次回复一组
  session_id TEXT, workspace_id TEXT,
  summary TEXT NOT NULL,            -- 概要（检索/注入用）
  detail TEXT DEFAULT '',           -- 关键细节补充
  tool_calls TEXT DEFAULT '[]',     -- [{name, args_digest, result_digest, ok}]
  status TEXT DEFAULT 'active',     -- active / cold / archived
  importance REAL DEFAULT 0.3,
  created_at REAL, expires_at REAL, -- 15 天
  moved_to_cold_at REAL
);
CREATE TABLE process_sources (      -- 组内原文全量
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL,
  seq INTEGER, role TEXT, content TEXT
);
```

### 4.2 语义条目通道（现有，规则修正）

- 抽取规则已改：执行成果/过程细节**不作为 fact/plan 抽取**（归过程通道）
- importance 锚定（已落地）：0.8-1.0 仅限目标/决策/用户长期偏好；0.5-0.7 重要事实约定；0.2-0.4 边缘；默认 0.3
- 冲突检测（新增）：写入时同主题高相似（阈值 0.95）→ **新为准，旧条转 superseded 归档**

### 4.3 状态卡/画像卡通道（双域分离）

- work 域轮次 → 增量更新工作区状态卡；life 域轮次 → 增量更新生活画像卡
- 纯 life 轮次 card 必须为 null（prompt+代码双重约束，已落地）
- **用户手动编辑与 LLM 自动更新的协作**：版本号递增（server 已有 version 字段），手动保存覆盖自动；自动更新在用户有未保存编辑时不写入（前端本地 draft 检测）

### 4.4 显式锚点通道

- `memory_save`：用户/模型显式"记住这个"——importance 0.8+，几乎不过期
- 独立于自动抽取，不参与批量衰减归档（保护）

---

## 5. 工作域机制（详细）

### 5.1 工作区状态卡（任务看板化改造）

**只读视图**（记忆面板顶部，默认）：

```
┌─ 工作区状态卡 ──────────────── [编辑] ─┐
│ 目标    ：……（单行）                    │
│ 当前方案：……（单行）                    │
│ 进行中  ：□ 事项1  □ 事项2  □ 事项3      │
│ 下一步  ：○ 步骤1  ○ 步骤2              │
│ 关键决定：2026-08-16 决定1              │
└──────────────────────────────────────┘
```

**编辑视图**（点「编辑」进入，看板式）：

```
┌─ 工作区状态卡 · 编辑 ────────── [保存] [取消] ─┐
│ 目标   [输入框，单行]                            │
│ 当前方案 [输入框，单行]                          │
│ ┌─ 进行中 ──────────┐  ┌─ 下一步 ──────────┐    │
│ │ [卡片1]  ✕  ✓    │  │ [卡片2]  ✕  ✓    │    │
│ │ [卡片3]  ✕  ✓    │  │ + 添加            │    │
│ │ + 添加           │  └───────────────────┘    │
│ └──────────────────┘                           │
│ 关键决定 [时间线列表，每项可编辑/删除，+ 添加]      │
└────────────────────────────────────────────────┘
```

**交互细节**：

| 操作 | 行为 |
|---|---|
| 卡片点击文字 | 内联变输入框可改 |
| 卡片 ✓ | 勾选完成：进行中卡片划掉并移到"已完成"区（本地状态，保存时从 in_progress 移除）；下一步卡片勾选 → 移入进行中 |
| 卡片 ✕ | 删除该条 |
| 列间移动 | 每张卡片带 ←/→ 按钮在"进行中"与"下一步"间移动（第一版用按钮，不做 HTML5 拖拽，后续可加） |
| + 添加 | 列底加空卡片进入编辑 |
| 保存 | 过滤空项 → POST /v1/cards/upsert（全量字段）→ version+1 → 刷新 |
| 取消 | 丢弃本地改动，回到只读视图 |
| 自动更新协作 | 用户未保存编辑时，LLM 自动更新跳过；已保存后自动更新照常（基于 server 最新版增量） |

### 5.2 工作域注入

工作对话：状态卡 + work 语义 Top-K + work 过程 Top-K（预算内）
生活对话：**不注入工作区状态卡**

---

## 6. 生活域机制（详细，全新设计）

### 6.1 生活画像卡（life 域的状态栏，可编辑）

```
┌─ 生活画像卡 ───────────────────── [编辑] ┐
│ 基础档案：生日 / 常驻城市 / 家庭成员（长期）   │
│ 当前生活状态：最近在忙的事 / 健康 / 情绪基调    │
│ 生活事件线（最近10条，可滚动）：              │
│   2026-08-10 和家人去旅行                     │
│   2026-08-15 牙医复查                         │
└────────────────────────────────────────────┘
```

数据模型：

```sql
CREATE TABLE persona_profiles (
  persona_id TEXT PRIMARY KEY,      -- 默认 'default'，多人格预留
  basic TEXT DEFAULT '{}',          -- {birthday, city, family: [...]}
  current_state TEXT DEFAULT '',    -- 最近在忙/健康/情绪（一句话）
  updated_at REAL
);
-- 生活事件线复用 documents（type=episode, domain=life），按时间倒序取最近 10 条
```

### 6.2 生活记忆类型与生命周期

| 类型 | TTL | 强化 | 说明 |
|---|---|---|---|
| preference 偏好 | 60 天 | 访问延长 | 饮食/音乐/生活方式喜好 |
| habit 习惯 | 长期 | 重复出现强化 | 作息/锻炼/阅读 |
| relational 关系 | 长期 | — | 家人/朋友/人际（敏感度高，注入谨慎） |
| life fact 个人信息 | 永久 | — | 生日/住址/病史，importance 0.9+ |
| episode 生活事件 | 7-30 天 | — | 旅行/就医/聚会，进事件线不进长期注入 |

### 6.3 生活域原子生命周期

- 偏好/习惯落 atoms（TTL+强化+衰减，现有机制）
- 生活原子到期 → 归档（不转冷库，直接 archived）
- 生活画像卡的 current_state 每周由整合任务刷新（LLM 摘要最近生活事件）

### 6.4 生活域注入

生活对话：画像卡（basic+current_state）+ life 偏好 Top-K + 近期生活事件 3 条
工作对话：**不注入画像卡**

### 6.5 域识别

- 自动：抽取时判定每条记忆 domain（现有字段）；注入时按**当前轮用户消息**的域倾向选择（新增：query 先过一个轻量域分类——用抽取同一模型，输出 {domain}）
- 手动：面板顶部加「工作 / 生活」模式切换（影响注入域权重与默认写入域，不影响已存记忆）

---

## 7. 召回与注入（详细算法）

### 7.1 注入组装（每轮 pre-step）

```
1. 域分类：用户最新消息 → {domain: work|life, conf}
2. 检索：query = 最新消息 + 最近6条消息片段（现有跨轮次扩展）
   - work 查询：状态卡常驻 + work 语义 Top-K + work 过程 Top-K
   - life 查询：画像卡常驻 + life 偏好 Top-K + 生活事件 3 条
3. 组装：按 a→b→c→d 顺序填充，累计 token 估算（字符数/2.2 近似），超预算即停
4. 缓存：组装结果进 state.memText（现有机制）
```

### 7.2 主动调用（深挖通道，不占预算）

| 工具 | 用途 | 触发场景（描述里写明） |
|---|---|---|
| memory_recall | 语义/过程/事件深挖 | 引用过去、看上次结果、回忆细节之前 |
| memory_briefing | 子任务简报 | 派发子代理前 |
| memory_forget（新增） | 主动剔除错误记忆 | 确认某条记忆已过时/错误 |
| memory_save | 显式锚点 | 用户明确要求记住 |

注入段尾部引导句（固定）：`如上下文不足，可用 memory_recall 查询更早的对话与执行细节；发现过时记忆可用 memory_forget 剔除。`

---

## 8. 错误记忆剔除（三条路径详细）

### 8.1 写入冲突检测（自动）

```
新记忆写入时：
  vector top1 相似度 ≥ 0.95 且 类型相同 → 冲突
  处理：新条正常写入（active）；旧条 status='superseded'
        （superseded 视同归档：不进检索/注入；面板"已取代"分区可查可恢复）
  若相似度在 0.85~0.95：走现有近重复合并（更新旧条）
```

### 8.2 模型主动剔除（memory_forget 工具）

```
参数：{query 或 memory_id, reason?}
行为：匹配到的记忆 → status='archived' + 记录 forget_reason
返回：剔除条数与内容摘要
日志：who/when/why 全记录
```

### 8.3 人工剔除

- 面板记忆行 ✕（现有）
- `/memory forget <关键词或id>`（新增命令，与工具同路径）

---

## 9. 敏感信息拦截

### 9.1 检测

```
正则模式（server 写入前 + 抽取 prompt 双端）：
  - API key: sk-..., ghp_..., github_pat_..., AKIA...（AWS）
  - token/secret: Bearer ..., xoxb-...（Slack）
  - 密码: password=..., passwd=..., 私钥头 -----BEGIN ... PRIVATE KEY-----
```

### 9.2 处理与提醒

```
命中 → 该字段脱敏写入（***REDACTED***）+ 记录 sensitive_hits 表
     → 插件日志 + 面板顶部黄条提醒："检测到 N 处敏感信息已自动脱敏（本会话）"
     → 不静默丢弃（保留记录位置与类型，内容不落盘）
```

---

## 10. 冷处理（过程记忆专属）

| 参数 | 默认 | 说明 |
|---|---|---|
| process_ttl_days | 15 | 过程记忆衰减天数（可配置） |
| process_cold_move | true | 到期移入冷库（false=直接归档） |
| cold 库行为 | — | 不进检索/注入；冷库页可查询/恢复/清空 |

每日维护任务：process_memories 到期（expires_at < now）→ status='cold'。冷库容量上限 5000 条，超出按最旧清空至 3000（可配置）。

---

## 11. 数据模型汇总（DDL）

见 §4.1（process_memories/process_sources）、§6.1（persona_profiles）；新增字段：

```sql
ALTER TABLE documents ADD COLUMN superseded_by INTEGER;   -- 被谁取代
ALTER TABLE documents ADD COLUMN forget_reason TEXT;      -- 剔除原因
ALTER TABLE atoms ADD COLUMN sensitive BOOLEAN DEFAULT 0;
CREATE TABLE sensitive_hits (id, kind, location, created_at);
```

---

## 12. API 清单（新增）

| 端点 | 说明 |
|---|---|
| `POST /v1/process/add` | 过程记忆组写入（summary/detail/tool_calls/sources） |
| `GET /v1/process/list` `?status=&session_id=` | 过程记忆查询（含 cold） |
| `PUT /v1/process/<id>/status` | 冷库恢复/归档 |
| `GET /v1/profile/<persona_id>` `PUT` | 生活画像卡读写 |
| `POST /v1/memories/forget` | 剔除（query 或 id + reason） |
| `GET /v1/memories/superseded` | 被取代记忆列表（面板"已取代"分区） |
| `POST /v1/maintenance/cold-move` | 冷处理手动触发 |
| 现有 cards/upsert 复用为状态卡编辑保存 | — |

---

## 13. 配置项清单（新增/修改）

| 键 | 默认 | 组 | 说明 |
|---|---|---|---|
| injection.budget_tokens | 4000 | injection | 注入预算，0 关闭，8000 上限 |
| injection.guide_enabled | true | injection | 注入尾部引导句开关 |
| process_ttl_days | 15 | process | 过程记忆衰减天数 |
| process_cold_move | true | process | 到期移冷库而非删除 |
| process_cold_cap | 5000 | process | 冷库上限（超出清至 3000） |
| process.enabled | true | process | 过程记忆通道开关 |
| profile.persona_id | default | scope | 生活画像人格 id |
| recall_engine.cold_search | false | recall_engine | 检索是否含冷库（默认不含） |
| 现有各组保持 | — | — | importance 锚定、域分离已由规则+代码落地 |

---

## 14. UI 改动清单（记忆面板重构）

| 区域 | 改动 |
|---|---|
| 状态卡区 | 只读+编辑双态；编辑态看板式（§5.1）；work/life 面板各显示对应卡 |
| 记忆分区 | 新增「过程记忆」分区（概要+原文展开）；「已取代」分区（superseded）；冷库入口 |
| 域切换 | 面板顶部「工作 / 生活」模式切换 |
| 敏感提醒 | 顶部黄条（命中敏感信息时） |
| 冷库管理 | 维护页加冷库 tab（列表/恢复/清空） |
| 配置卡 | 新增 process/injection.budget 等组自动跟随 schema |

---

## 15. 实施阶段（任务清单）

### P1 — 骨架（server + 插件）
- [ ] server：process_memories/process_sources 表 + /v1/process/* 端点
- [ ] server：冲突检测（superseded）+ /v1/memories/forget + superseded 列表
- [ ] server：敏感过滤（正则+脱敏+敏感命中表+提醒数据）
- [ ] server：冷处理（每日任务+cold-move 端点+冷库上限）
- [ ] server：persona_profiles 表 + /v1/profile/* 端点
- [ ] 插件：按一次回复分组捕获（工具调用+原文）→ /v1/process/add
- [ ] 插件：注入组装（预算截断+域感知+引导句）+ 域分类
- [ ] 插件：memory_forget 工具 + /memory forget 命令
- [ ] 验证：试验机全链路 + curl 冒烟

### P2 — UI
- [ ] client：状态卡看板化编辑（§5.1 全部交互）
- [ ] client：生活画像卡编辑（§6.1）
- [ ] client：过程记忆分区（概要+原文展开）
- [ ] client：已取代分区 + 冷库管理页 + 敏感提醒黄条 + 域切换
- [ ] 验证：web-check 浏览器断言（编辑/保存/拖移/域切换）

### P3 — 质量与发布
- [ ] importance 分布监控（抽取后统计日志）
- [ ] 过程覆盖率统计（每组回复是否都落过程记忆）
- [ ] 试验机回归 + GitHub 同步（publish 分支）

---

## 16. 验证方案

| 层 | 手段 |
|---|---|
| server | verify.sh 隔离实例冒烟（新增 process/profile/forget/sensitive 用例） |
| 插件 | plugin-check 模拟 apply（工具数、分组捕获逻辑单测式断言） |
| UI | web-check 浏览器断言（状态卡编辑保存、域切换、过程记忆分区渲染、敏感黄条） |
| 质量 | 日志抽查：importance 分布、过程组落库率 |

---

## 17. 风险与对策

| 风险 | 对策 |
|---|---|
| 过程记忆量大噪声多 | 概要建索引、原文按需回查、15 天冷处理、冷库上限 |
| 注入预算失控 | 硬截断+优先级+日志可观测 |
| 冲突误归档好记忆 | 阈值保守 0.95 + superseded 可恢复 |
| 敏感漏网 | 双端过滤 + 提醒可见 + 命中审计表 |
| 生活/工作误判 | 域分类低置信（conf<0.6）时两域各注入一半 |
| 用户编辑与 LLM 更新打架 | 版本号 + 未保存 draft 时跳过自动更新 |

---

## 18. 当前冻结状态

- 动态插件 deepm-1/pkg-2（双域分离+重要性锚定）已 define 未 run
- client.js 状态卡编辑初版 UI 已写 repo 源未部署（将按 §5.1 重做完整版）
- 方案确认后按 P1 开工
