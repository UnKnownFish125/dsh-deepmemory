# deepmemory 记忆系统架构方案（待审查）

> 本文档是 deepmemory（DSH 长期记忆插件）的完整改造方案，交外部评审（glm5.3）审查。
> 生成时间：记忆系统第 N 轮讨论。作者：主模型（基于实测数据与已有讨论）。

---

## 0. 背景与现状（实测数据）

### 0.1 每轮 LLM 输入构成（实测，DeepMemory 开发会话）
| 组成 | 大小 | tokens | 说明 |
|---|---|---|---|
| system prompt | 6,736 字符 | ≈1,684 | 角色/GUI/文件规则框架 + deepmemory 注入段(order 50) + 工具说明 |
| tools schema | 28,521 字符 | ≈7,130 | 28 个工具，占 ~25%（workflow 4,067/bash 3,290 最大） |
| 对话历史 | 30,133 字符累计 | ≈7,533 | user 50 条；assistant chunk 拼接；compaction 摘要 11,743 字符 |
| 当次用户消息 | 几十~几百 | ≈100 | |

### 0.2 现有实现
- **存储**：sqlite+faiss，documents 表（content/type/domain/scope/importance/workspace_id/session_id），atoms/entities/relations 图谱，state_cards（goal/current_plan/next_steps），tasks。
- **抽取**：turn-stopping 时 LLM 从对话提取 JSON（memories/card/tasks）。
- **注入**：`systemPrompt.section({name:'deepmemory', order:50})`，格式 `- [type/scope] content(截240)`，5 条（RECALL_K=5），query 是静态种子"当前会话目标、计划、决定、偏好和相关工作上下文"+最近6条120字符。
- **检索**：BM25+向量+图谱 RRF 融合；`final = alpha*relevance + beta*importance + gamma*recency`。

### 0.3 已知 4 个问题
1. **无绝对时间**——注入只显示 type/scope，无时间戳。
2. **会话级记忆被判 workspace**——模型抽 scope 时多数判 workspace（提示词引导偏粗）。
3. **无法验证继承**——注入无 id/来源/日志明细，模型看不到"这条我继承了吗"。
4. **效力差**——用户强约定（"先测试机验证"，importance=0.95）常失效：注入无 importance 标签，模型无法区分强弱。

### 0.4 硬约束：前缀缓存
- DeepSeek 有**前缀缓存**（context caching；实测 `cacheReadTokens=5440`）。
- 缓存按**从头顺序前缀匹配**：system[角色→GUI→deepmemory(order50)→工具说明(order100-115)] → 对话历史。
- **注入段在 order 50（工具 schema 之前）**，注入段每轮变 → 前缀在 50 处断裂 → **之后 ≈7,130 tok 工具 schema + 历史全部失效**。
- 结论：**凡进 system 前缀的都要稳定（会话内冻结）；会变的都后置到对话流**。

---

## 1. 总原则

> **前缀字节稳定性 > 单轮信息量。**
> - 进 system 的 = 会话内冻结（会话开始生成一次，之后字节不变）。
> - 会变的 = 后置到对话流尾部（其后再无内容，天然不破坏前缀）。
> - all 时间线/归属/来源 = 结构化数据（库/检索过滤），不是 system 文本。

---

## 2. 四层架构

| 层 | 谁写 | 谁读 | 触发 | 预算 | 解决 |
|---|---|---|---|---|---|
| **L0 规则层**（skill/约定固化） | 固化流程 + 人确认 | 每会话进 system，全程只读 | 违反计数≥N 或用户显式纠正 | ≤2K tok | ④ 强约定失效 |
| **L1 画像层**（项目简介/部件继承） | 会话结束/切换 workspace | 会话开始一次性注入，会话内冻结 | session start | 1-2K tok，滚动淘汰 | ② 归属 / ③ 继承 |
| **L2 摘要链**（对话压缩摘要） | 独立压缩器（与记忆抽取分离） | 替代被截断的旧历史 | **token 阈值，非轮数** | 每块 200-400 tok，链上限 ~3K | ① 时间线 |
| **L3 检索层**（sqlite+faiss） | turn-stop 抽取 | 对话流尾部按需注入 | 每轮 stop 写、每轮读 | 单轮注入 ≤800 tok | ④ 细节命中 |

**关键**：
- 所有层带 `time/session/workspace/provenance` 元数据 → 统一解决 ①无时间 ②归属 ③继承验证。
- **workspace 必须是 L3 检索的硬过滤条件**（不是相似度软排序兜底）。
- 中间态（会话目标/部件继承）→ L1 会话头冻结。

---

## 3. 缓存与上下文替换机制（本轮重点）

### 3.1 缓存该保哪段
- **可缓存段（每轮不动）**：system 框架 ≈1,700 tok + tools schema ≈7,130 tok + 会话级冻结记忆快照 ≈300 tok → 合计 ≈9,130 tok（22%）。
- **必然变化段**：对话历史（LLM 工作必须看新消息）——缓存失效**合理且不可避免**。
- **最亏的现状**：注入段每轮变 → 把稳定的 tools(7,130) 缓存也带崩。**变一小段、损一大段**。

### 3.2 注入段最优位置
| 位置 | 缓存影响 | 判定 |
|---|---|---|
| system 前缀 order50（现状） | 变→tools+历史全崩 | ❌ 最差 |
| **混入对话历史**（最新用户消息之前） | 随历史更新，**tools 缓存完全保住** | ✅ 最优 |
| 用户消息前缀（某轮后） | 前缀可保留到该点；其后变 | 次优 |

**推荐**：深记忆段放**对话历史内、最新用户消息之前**（`[memory context]` 块）：
- tools+system 前缀稳定 → 缓存命中 ≈9,130 tok
- 记忆更新只影响其后的对话历史（本来每轮就变）
- 记忆随上下文**每轮替换**（恢复实时性），且不击穿 tools 缓存

### 3.3 上下文替换（官方 compaction + 深记忆补全）
- 官方 compaction（thresholdRatio=0.8 按压力，retainRatio≈16% 保尾部）后布局：`[冻结前缀][summary][尾部原文K轮]`。
- 深记忆补全**只替换被剪的中间历史**（summary 之后、尾部之前加记忆段）。
- **不与官方 summary 重复**：官方 summary=对话压缩（替历史）；深记忆=长期知识（补知识）——互补。
- 替换时机：`compaction/summary` 事件触发时更新记忆段（低频；在历史区，不破前缀缓存）。

### 3.4 工具 schema 是否移出 system
- 可以：重型工具（workflow 4,067/bash 3,290 等）按需插入；但 tools 按需变 → tools 缓存也变（命中"部分工具组合"缓存）。
- **折中**：核心工具（read/edit/bash/glob/…）固定在前缀（缓存命中）；重型工具（workflow/subagent/ralph）尾部按需。可腾 ≈3,000 tok。

### 3.5 推荐每轮输入布局
```
┌ 冻结段（缓存命中·稳定）────────────────────────────┐
│ system 框架 + [会话级冻结记忆快照·会话开始生成] ≈2,000 tok │
│ core tools（read/edit/bash/glob/subagent…） ≈4,000 tok │
├ 替换段（每轮变·缓存自然失效）─────────────────────────┤
│ 官方 summary（compaction 后）                  ≈300 tok │
│ [深记忆段·按当前上下文更新] ≈300-800 tok  ← 新增位置！   │
│ 对话历史尾部（K 轮原文）                     ≈3k-8k tok │
│ 当次用户消息                                   ≈100 tok │
└───────────────────────────────────────────────────────┘
```

---

## 4. 已实施（L1 前置，待审查确认）

| 改动 | 状态 |
|---|---|
| formatMemories：分类组织（[规则与偏好]/[决定与目标]/[事实与事件]）+ 每条带 i=权重/时间/topic + 偏好保底(≥0.8 必入) + 提量(k=10-13) | ✅ 代码已改 |
| 抽取 prompt：模型打 topic（记忆带 topic_id）；documents 加 topic_id/event_time 列 | ✅ 已加列（生产手动 migrate 补齐） |
| 注入召回 query 改用真实上下文（最近 6 条全文） | ✅ 已改（但仅初始化时一次性——缓存友好） |
| 撤回"每轮刷新"（恢复初始化刷新） | ✅ 防止击穿缓存 |
| config_schema：compression 组（enabled/min_turns→trigger_bytes 按长度/summary_model/inject_summary/retain_head） | ✅ 已加 |

**说明**：④ 偏好保底已做（formatMemories 内 type==preference 或 importance≥0.8 强制加入）；但**注入段仍在 system(order50)**——见 3.2 需要**移动到对话历史**（本轮未做，待审查定）。

---

## 5. 规则→skill 固化机制（L0，方案未实现）

- **判定**：同一约定被抽取 ≥2-3 次，或出现过违反 → 固化候选。
- **固化即迁移**：写入 skill 时带 provenance（来源会话/时间/违反计数/验证状态），**从 L3 注入池移除**该条（防双写漂移）；检索时对已固化主题降权（防旧记忆推翻规则）。
- **治理**：每条规则唯一 owner 文件 + 版本号；低频审计淘汰失效项。
- **继承顺序固定**：L0 规则 → L1 画像 → L3 检索，形成**可验证的继承链**。

---

## 6. 独立压缩管线（L2 摘要链，方案未实现）

- **不依赖 DSH 官方 compaction 内容**（官方=完全压缩压历史，改源文件风险大）；deepmemory 自主生成摘要链。
- 触发：**按长度**（`trigger_bytes=60000` 累计字符阈值；官方已按压力 0.8，我们同样按长度而非轮数）。
- 数据结构：**append-only 分块**：`[稳定system][tools][S1][S2][S3][近K轮原文]`——新块只追加在冻结块后，旧块永不改写（前缀命中已冻结块，缓存不破）；超限时会话边界整体重压缩一次。
- 摘要模型可配（`summary_model` 留空自动选）。

---

## 7. Topic 定义与管理（方案未实现）

- **模型打 topic**（抽取时给每条记忆打主题标签，如"任务看板开发"）。
- topic 与 workspace/session 关系：
  - 主链 = topic（跨会话聚合，同主题续接简介链）
  - 辅 = session_id（溯源/衰减）
  - workspace = L3 检索硬过滤
- 同 topic 跨会话聚合成一条"项目简介"（L1/L2 载体）。

---

## 8. 迁移路径（现有 188 条历史记忆）

1. **不加删**：现有 documents 保留（别名兼容 topic_id/event_time 空 = 未分类）。
2. **回填**：脚本按 key_facts/content 聚类打 topic 标签；event_time 取 created_at。
3. **激活**：新架构只处理新写入 + 回填后打 tag 的记忆；未打 tag 的走旧检索路径（不丢不混乱）。
4. **渐进**：先 L1 列/注入格式（已改）→ 再 L2 摘要链（增量）→ 最后 L0 固化（依赖计数/provenance）。

---

## 9. 落地顺序建议（glm 已给）

1. **先 B：append-only 摘要链 + C：注入后置**（缓存收益立刻兑现）
2. **再 A：元数据统一**（time/session/workspace/provenance）
3. **最后 D：规则固化流程**（依赖前面计数与 provenance）

---

## 10. 待审查确认的关键点

1. 3.2 注入段**移到对话历史**（最新用户消息前）——是否采纳？还是保持 system 冻结快照？
2. 3.4 工具 schema **核心固定/重工具按需**——是否值得（缓存收益 vs 复杂度）？
3. L2 摘要链 **append-only 分块**——数据落地（sqlite 表结构 proposal：`topic_summaries(topic_id, seq, summary, start_time, end_time, prev_seq)`）。
4. L0 规则固化 **迁移即移除 + 降权**——是否会导致规则记忆"消失"（用户以为丢了）？保守改为"标记不删除"？
5. 注入量：单轮 ≤800 tok（glm 建议）vs 我目前 10-13 条（每条 240 截断 ≈ 2.4k 字符 ≈ 600 tok）——**预算内是否 OK**？

---

## 11. 环境事实登记表（info registry，按需自建库）——新增到计划

> 用户新增需求：记录不常变动的环境事实（当前工作区、服务器 IP、项目路径等），方便随时查询。
> **已改为按需创建数据库**：不固定单一表，按需求（域/项目/场景）**动态建库、库内成表、按需求路由访问**。

### 11.1 架构（按需库）

```
data/
├── info/                          ← 环境事实库目录
│   ├── env.db                     ← 服务器/工作区/基础设施域（按需创建）
│   │   └── entries(key PK, value, updated_at, provenance)
│   ├── project-livetaskboard.db   ← 按需：某个项目域（登记时自动建）
│   │   └── entries(...)
│   └── project-deepmemory.db      ← 按需：另一个项目
├── registry.db                    ← 注册中心（小的 meta：记录已有哪些库/域/key 目录）
│   └── domains(domain, db_path, created_at) / keys(domain, key, category, updated_at)
└── 记忆库（documents，不变）
```

**按需规则**：
- **建库**：首次登记某域（POST /v1/info/<domain>/<key> 或查询触发）→ 若该域库不存在 → 自动 CREATE `data/info/<domain>.db` + `entries` 表 + 注册到 registry.db
- **成表**：每个域库内一张 `entries` 表（key-value + 时间 + 来源），域 = 按需拆分单位
- **访问**：`GET /v1/info/<domain>/<key>` → 打开对应域库（路由到正确 db/表）；`GET /v1/info/keys` → 从 registry.db 读目录（不打开全部库）

### 11.2 API（按需求路由）
| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/v1/info/<domain>/<key>` | 打开 domain.db 查 key（精确；无则 404 提示可登记） |
| GET | `/v1/info/<domain>` | 列出该域全部条目 |
| GET | `/v1/info/keys` | 注册中心目录（已有哪些域/库/键） |
| POST | `/v1/info/<domain>/<key>` | **按需建库**（域库不存在→自动建）+ upsert 条目（带 provenance） |
| GET | `/v1/info/domains` | 已建库列表 |

### 11.3 域规划（首次按需登记时创建）
- `env`：server.ip / server.hostname / workspace.id / workspace.path
- `project-<name>`：path / remote / type / status（一个项目一个库，可按需新建）
- `infra`：端口/服务/systemd 单元等（按需）
- `custom`：自定义（用户自由域）

### 11.4 与记忆的关系
- 按需库 = **确定性事实层**（精确查询、不衰减、不依赖语义检索）
- 语义记忆（L3）= 知识/偏好层；项目登记时可反哺语义记忆，但按需库是**权威源**（防双写漂移）

### 11.5 落地时机
- 随记忆方案 L1/L2/L3 之后（独立小功能，可先行验证）——建议**先做**（改动小、立即有用：后续方案实施要经常查项目路径/工作区/服务器 IP）。
