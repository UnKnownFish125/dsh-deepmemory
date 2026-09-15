# deepmemory 全插件代码审核 —— 第一轮（Python 后端）

**日期**：2026-09-15
**审核者**：`uuapi-astra / gpt-6-astra`（推理档 xhigh），只读审核，未修改文件、未请求生产写接口、未做破坏性测试
**基准**：`P` = 生产运行态 `/www/deepmemory-v063-deploy/memory-server/server.py`（3462 行）；`R` = 仓库 `/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py`（3718 行，已含分批补丁）
**复核**：主 agent 逐条抽查行号与代码 —— ✅已复核 / ⚠️未复核
**去重**：通用 SQL/FAISS 非原子、无路由级授权、topic `MAX(seq)+1`、附件吞错、迁移吞错、旧路由隔离等已在 `docs/codex-review.md` / `codex-review-2.md`，不计新增。

> **已撤回的误报**：曾报「生产 `_assertion_current_allowed_batch` 未定义 → 检索必 NameError 500」。实际该函数定义于 `P:588-623`，生产检索实测 200 正常，**不成立**。
> **降级剔除**：`llm_chat(provider)` 虽缺 URL 校验，但当前 HTTP 路径未发现外部可控 `provider`，**不报远程 SSRF**；embedding 侧已有地址解析检查且禁重定向，`MEMORY_ALLOW_PRIVATE_EMBEDDING=1` 属设计选择。

---

## 修复状态（2026-09-15 晚更新）

补丁脚本统一放在 `/www/scripts/`，均幂等、可重复执行，每处改动留 `.bak-*` 备份。

### ✅ 已上生产并验证（批次 1 + 批次 3）

| 条目 | 补丁脚本 | 生产验证证据 |
|---|---|---|
| S01 备份删除路径穿越 | `patch_backup_name_validation.py` | `..` `.` `%2e%2e` `../../etc` `backup-evil` 全 400；正常创建/删除 200；11 份备份与 data 完好 |
| S02 模型身份指纹 | `patch_server_s02_fingerprint.py` | `dim.json` 写入 `fp=api:bge-m3`；指纹一致不重建、**同维换模型触发重建**；旧库无 fp 时补写不重建 |
| S04 FAISS 检索持锁 | `patch_server_batch3.py` | 检索 200、无死锁（`_index_lock` 为 RLock） |
| S08 权重/衰减配置生效 | 同上 | 改用函数内读入的局部变量 |
| S09a 接口参数名 | 同上 | `rule-candidates` 稳定 500 → 200 |
| S09b `similar_ids` 整数下标 | 同上 | 同上 |
| S12 归档/恢复向量同步 | `patch_server_s12_vectors.py` | 归档 `ntotal` 10643→10642、恢复 →10643 |
| S16 embeddings 真实模型名 | `patch_server_batch3.py` | 响应 `model=bge-m3`、1024 维 |
| （额外）`rule_candidates` O(n²) embedding | `patch_rule_candidates_perf.py` | **120s 超时 → 5.3s 返回**（批量 embedding + 单次矩阵乘 + `max_scan`） |
| S15 L2→cos 重复平方 | `patch_server_s15_s10_s14_s13.py` | 测试机 `vector_search` 分数落回 (0,1] 合理区间（0.584~0.605） |
| S10 检索缓存键截断 200 字符 | 同上 | 改用完整 query 的 SHA-256（消除前缀碰撞导致的错误复用） |
| S14 session 级密钥绕过屏蔽 | 同上 | 测试机实测：写入 session 级 secret 后 `GET /v1/config` **不回显**、`/v1/settings/<key>` 返回 **404**，全局 `embedding.api_key` 仍屏蔽（探测数据已清理） |
| S13 衰减按总年龄重复扣减 | 同上 | 改为按 `max(ref, 上次衰减时刻)` 的增量计算（遗忘速度不再随调度频率变化） |

### ⏸️ 代码已就位，待重启 dsh-web 生效（批次 2 + 批次 4）

| 条目 | 补丁脚本 | 验证状态 |
|---|---|---|
| N01 合成输入过滤（切断记忆自污染循环） | `patch_preset_batch2.py` | 测试机四步 preflight 通过（语法、ESM 冒烟、建会话 `ok:true`、无 preset 报错） |
| N03 `lastUserText` 取值 + 入队时序 | 同上 | 同上 |
| N05 工具检索/保存/briefing 补 `session_id` | 同上 | 同上 |
| N06 无卡 404 降级，不丢弃已检索记忆 | 同上 | 同上 |
| N14 compaction 摘要改读 `data.summary` | 同上 | 同上 |
| N15 `rule` 并入规则分组 | 同上 | 同上 |
| N04 会话开关缓存 TTL | `patch_preset_n04_enabled_ttl.py` | `node --check` + ESM 冒烟 + 三处副本 md5 一致 |
| N20 `ListEditor` 提升到模块级（输入不再丢焦点） | `patch_client_batch4.py` | `node --check` + 三处一致 |
| N21 `api()` 保留后端 error/code/status | 同上 | 同上 |
| N16 会话配置只提交 dirty 键 | `patch_client_n16_n22.py` | `node --check` + 三处一致 |
| N22 原文标注来源数量、保留完整列表 | 同上 | 同上 |

### ⬜ 未做（按优先级排序，需专门窗口）

| 条目 | 为何缓做 |
|---|---|
| **N17 会话级配置多数不被 preset 消费** | 正解是把 preset 的**模块级配置变量**改为按会话解析（避免跨会话污染）。属核心链路重构，改动面覆盖 assemble/抽取/工具注册，草率改会重演「liangshen 事故」（核心链路被改坏 → 所有会话每回合报错）。**必须有完整上下文与专门验证窗口**。 |
| S06/S07（仅仓库 P1：断言 ID 复用继承旧确认、并发 revoke 后被复活） | 生产未部署 P1，属**合并 P1 前的强制前置项** |
| S03 影子重建竞态 / S05 部署漂移合并 / S17 备份快照 / S18 batch 非原子 | 按 ROI 排期 |
| N07 Host `session.events` 兼容 / N08 写卡全量覆盖 / N09 队列丢失 / N10 输出结构校验 / N11 Host 抽取绕过脱敏 / N12 流错误处理 / N13 Host workspace 解析 / N18 无 deadline / N19 任务卡重复 / N23 CSS 未清理 / N24 日志泄漏 / N25 绝对路径依赖 | 按 ROI 排期 |

### 流程事故与修复（非 astra 报告项）

- **`sync-test-env.sh` 会搞挂测试机**：合并逻辑把生产 bundles 里测试机未安装的包、以及无 `dsh.bundle` 声明的插件（`dsh-reasoning-guard` / `dsh-anysearch`）写进测试机 bundles → `dsh-app-boot` 报 `cannot resolve profile bundle` / `declares no dsh.bundle` → dsh-test 起不来。已加「可解析 + 声明 `dsh.bundle`」双重守卫 + 写盘前备份，实跑验证通过。
- **教训**：`@deepseek-ai/*` 基础 bundle 由安装根解析，**不在** profile 的 `node_modules` 下，不能按文件存在性判断。

---

## 🔴 严重

### S01 备份删除接受 `.` / `..`，可清空整套数据 ✅已复核
- **位置**：`P:3398-3404`、`R:3654-3660`；`restore_backup` `P:2240-2242` 同族
- `name=os.path.basename(...)` 不拒绝特殊目录 → `DELETE /v1/backups/..` 把 `DATA_DIR` 交给 `rmtree`；`.` 清空整个 `BACKUP_DIR`。影响含 DB、向量、`api-token`、规则
- **可达性（主 agent 补证）**：经 Host 代理 `/mem-api/*` 可达 —— 代理原样转发、自动补 Bearer、并 `delete headers.origin` 绕过下游 origin 检查
- **未触发**：7 天日志无 `DELETE /v1/backups`；data 与 11 份备份完整
- **修复**：只接受 `backup-YYYYMMDD-HHMMSS` 白名单 + `realpath` 必须是 `BACKUP_DIR` 直接子目录，拒绝符号链接；不要在生产线复现

## 🟠 高

### S02 Embedding 迁移只认维度，模型身份未与索引绑定 ✅已复核
- `P:1876-1894` 配置变更只清 `_index` 与 dim；`P:1028→237-249` 先查询并重写 dim；`P:626-646` 随后**只比较维度** → 同维 A→B 切换时重建被跳过，查询向量 B 与文档向量 A 混用
- `P:136-149` `_embed_model` 单例未被配置变更清空 → 改 `local_model` 仍返回旧模型
- **修复**：持久化 provider/model revision/dimension/generation 指纹；身份变化发布新 generation 并原子替换 index+metadata；模型锁内重置本地实例。**不能把「维度相同」当「向量空间相同」**

### S03 影子重建覆盖在线增量，并发重建共用临时文件 ⚠️未复核（部分）
- `P:2257-2291`：SELECT 快照 `2265-2269` 结束，embedding `2274` 可能很久，`2285-2287` 固定 `.shadow` 后替换；只有 `2289-2290` 清 `_index` 时才持锁；`fingerprint={count,max_id}` 仅返回不复验
- 重建期间新增/修改已写入旧 index → 发布旧快照丢增量；两个维护请求可覆盖同一 shadow
- **修复**：单飞重建 + 唯一临时路径 + 持久化变更 generation，发布前回放增量校验，index 与 metadata 同代发布

### S04 FAISS 实际 `search` 不持锁，可与 add/remove 并发进入 C++ 索引 ✅已复核
- `P:845-850`：`get_index()` 返回后锁已释放，`idx.search` 无保护；写入 `1205-1208`、删除 `1510-1513`、修改 `1547-1551` 修改同一对象
- 影响：结果错乱乃至进程崩溃风险（**未复现崩溃**，仅静态论证）
- **修复**：取引用、`ntotal`、`search` 统一放 `_index_lock` 内；长期用只读快照 + 单写线程

### S05 生产与仓库双向漂移，仓库审核通过 ≠ 生产已修 ✅已复核
- `R:477-497` 有 `assertion_events` 迁移、`723-853` P1 事件/确认/撤销、`3565-3589` HTTP 路由；`P:450-474` 迁移止于 v9、`P:3328-3354` 无 P1 路由 → 新 UI 调 P1 得 404
- 反向：`P:780-809` 已用 `DEEPSEEK_API_KEY` + `api.deepseek.com`，`R:1010-1040` 仍是 uuapi 引用与旧默认模型 → 直接发布仓库会**回退生产 LLM 路由**
- **修复**：单一不可变制品 + 运行路径/git SHA/schema/模型指纹清单；部署前双方 diff + 真实契约冒烟；**不要直接覆盖生产**

### S06 删除断言后 ID 复用，旧确认事件挂到新记忆 ⚠️未复核（仅仓库 P1）
- `R:457` `assertions.id INTEGER PRIMARY KEY` 可复用；`R:1727` 关 FK、`1730-1738` 删 assertions 但漏 `assertion_events`；`R:1463-1467` 新增不指定 ID
- 旧事件 `R:484` 仍引用该 ID，`R:674-681`/`824-826` 据其累计确认数 → 新断言可能继承旧主体的两次确认并被晋升
- **修复**：断言 tombstone 保留身份，或不可复用 ID 迁移 + 清理现有孤儿

### S07 `confirm`/`promote` 与 `revoke` 并发可复活已撤销断言 ⚠️未复核（仅仓库 P1）
- `R:744-753` 先 SELECT 校验，`764` 才首个 DML；并发 revoke 可在其间提交，原请求随后按旧 `a_status` 在 `780-783` 无条件 `UPDATE adopted`；promote `814-823→843` 同样
- **修复**：首个 SELECT 前 `BEGIN IMMEDIATE`；状态更新带 CAS + `rowcount` 校验；事件、状态、投影同事务；补交错并发测试

## 🟡 中

### S08 权重配置被读取但未使用 ✅已复核
- `P:945-948` 读局部 `alpha/beta/gamma/decay_rate`，`970` 用全局 `DECAY_RATE`、`972` 用全局 `ALPHA/BETA/GAMMA`（`85: ALPHA, BETA, GAMMA = 0.5, 0.25, 0.25`）→ 配置页调参成功而排序不变
- **修复**：改用局部变量 + 权重校验 + 回归断言

### S09 规则候选接口必然参数错误；自动固化另有整数下标错误 ✅已复核
- `P:2305` 定义 `rule_candidates(min_similar=...)`，`3194-3196` 却传 `similarity=` → 任何请求 TypeError 500
- 自动路径 `2127` 把 `similar_ids` 里的整数 r 当字典 `r['id']`，而 `2332` 生成的是整数列表 → 仅在启用自动固化且有候选时触发
- **修复**：HTTP 关键字改 `min_similar`；扁平化 `merged_ids=[mid for c in ... for mid in c['similar_ids']]`；补路由与调度测试

### S10 200 字符截断缓存键导致不同长查询互相命中 ⚠️未复核
- `P:1023` 键为 `query.strip()[:200]`，`1025-1027` 直接返回旧列表 → 同前缀不同后缀的查询复用错误结果；底层 embedding 本接收完整 query
- **修复**：键用完整规范化 query 或其 SHA-256

### S11 新分批只校验条数，不校验位置与向量结构 ✅已复核（本补丁自身）
- `P:227` 按 `item.index` 排序，`228` 仅比较 `len`，`233` 接受所有 embedding → 重复/缺失 index、非法或不同维度、NaN 均不拒绝
- 影响：可能错配文档向量，或到 FAISS 才失败（**未声称当前 llama 服务实际返回坏响应**）
- **修复**：index 必须精确覆盖 `range(len(chunk))`；逐向量 1D、统一 `expected_dim`、有限非零校验；整批成功再提交 metadata

### S12 归档/恢复与向量集合不同步 ✅已复核
- `P:2165-2181` 归档仅 `BM25.remove`，FAISS 残留占满 `1029` 的 `k*3` 候选（`955-958` 会过滤，故非"归档直接泄露"）
- **更确定的丢召回**：归档后重建（`2266-2268` 仅 SELECT active）清掉向量，再执行 `2184-2202` 恢复仅 `BM25.add`、不补 FAISS → **恢复的记忆永久缺失语义通路**，直到再次重建
- **修复**：生命周期事件统一执行索引移除/恢复；测试 `archive→rebuild→restore`

### S13 每次衰减重复按总年龄扣减，遗忘曲线随调度频率变化 ✅已复核
- `P:2028-2044` 从当前 importance 出发，每轮乘 `exp(-rate*days_since_reference)`，而该年龄不是距上次衰减的增量 → 连续 30 天无访问，累计指数为 1+…+30=465 天而非 30 天；改 6 小时一次会衰减更快
- **修复**：按 `last_decay_at`→当前的增量衰减，或从持久化基准一次计算；补「daily vs 6h 结果等价」测试

### S14 秘密过滤只认全局键，session 命名空间可泄漏 ✅已复核
- `P:1923-1928` 允许保存任意 session 键（含 `embedding.api_key`）；`1858-1873` 扫描 `deepmemory.%`，剥一次前缀后只按 `key in {'embedding.api_key'}` 屏蔽 → `session.<sid>.embedding.api_key` **不命中**
- 后果：`GET /v1/config`（及其他会话 defaults）会返回该值；`2803-2807` 通用 GET 同样只有全局精确匹配
- **修复**：禁止 session 级凭据覆盖，或规范化命名空间后按 schema secret 分类统一屏蔽；global defaults 排除所有 `session.*`

## 🟢 低

### S15 L2→cos 换算重复平方 ✅已复核
- `P:845-860` 索引为 `IndexFlatL2`，`scores` 已是**平方** L2 距离，`860` 却 `1-(dist*dist)/2`，应为 `1-dist/2`（真 cos=0.8 被算成 0.92）
- 默认 RRF 只用名次不受影响；启用旧 merge 模式时 `1131-1134` 阈值判断过宽

### S16 Embedding 响应谎报旧模型 ✅已复核
- `P:3147-3162` 恒为 `model='bge-small-zh-v1.5'`，不论实际 bge-m3/API provider（2026-09-15 迁移后名不副实）

---

## 附 A：版本漂移清单（主 agent 独立分析）

| 维度 | 生产（3462 行） | 仓库（3718 行） |
|---|---|---|
| 函数数量 | 89 | 96 |
| P1 断言函数 | 无（缺 7 个） | `_confirm_origin_count`、`_count_confirm_origins_on`、`_promote_assertion`、`_record_assertion_event`、`_resolve_event_origin`、`_resolve_server_actor`、`_sync_rule_projection` |
| `llm_chat` 默认网关 | `api.deepseek.com/v1` + `deepseek-v4-flash`（**较新**） | `uuapi.io`（旧值未回改） |
| 分批 embedding 补丁 | 已打 | 已打（两个 clone 2026-09-15 补齐一致） |

**性质**：双向分歧。合并策略：以仓库为基线（含 P1），把生产的官方网关改动 cherry-pick 回仓库；P1 是否上生产单独决策，且**合并 P1 前必须先修 S06/S07**。

## 附 B：Top 10 ROI（astra 排序）

1. S01 备份名白名单 + 直接子目录验证 ｜ 2. S09 规则固化参数/类型错误 + 路由测试 ｜ 3. S08 使用已读取的排序参数 ｜ 4. S10 完整 query 哈希 ｜ 5. S11/S16 批响应结构校验与真实 model 标识 ｜ 6. S02 模型指纹、local 实例失效 ｜ 7. S04 FAISS 读写统一锁 ｜ 8. S03 单飞重建 + generation 发布 ｜ 9. S12 归档→重建→恢复向量对账 ｜ 10. S05 单制品部署门禁（合并 P1 前先修 S06/S07）

---

# 第二轮：Node 三件套（Host / client / preset）

**基准**：`H`=`web-plugin/index.js`(709) ｜ `C`=`web-plugin/client.js`(1496) ｜ `P`=`agent-preset/memory-plugin/plugin-v3.js`(856)。部署件已交叉比对，未做逐字节 hash。
**约定**：每条标「已自证」（实现/调用方/契约静态核对）与「生产是否触发」；**未触发 ≠ 无缺陷**。

## 🟠 高

### N01 自己的 runtime context 被当作新用户输入 → 记忆反馈循环 ✅已复核
- `P:597-628` 不检查 `data.source`；`H:176-185/465-475` 只排除 `plugin=compact`
- DSH `agent-loop:336-353` 把 runtime 快照构造成 `source.kind='plugin'` 的 UserMessage，`:1028` 以 `user/message` 提交
- **后果**：上次注入的记忆再次作为"用户确认"被抽取；合成文本挤出真实对话上下文；回合内冻结失效；重复检索/抽取与污染互相放大
- **修复**：按 `source.kind==='user'` 或授权来源白名单计数/抽取；排除 runtime-context、技能/提示文件、工具转述；按原始 `message.id` 去重

### N02 context 注册后仍改 sections → 本次 assembly 拿旧注入 ✅已自证（时序相关）
- `P:519-526` 注册 `systemPrompt.context`；`533-554` 在 waterfall 内刷新；`558-563` 只改 `assembled.sections`
- DSH `system-prompt:344-347` 在 waterfall(:351) 前就调用 `context.text` 成串；`144-148` 仅读 `assembly.contexts`
- **仅在本次 waterfall 内 cache 变化时**（首次初始化、压缩后、开关切换、等待中的刷新）取旧值；**不是每轮必延后**
- **修复**：更新 `assembled.contexts` 同名条目、禁用时清除、保留 `await next()` 结果

### N03 当前用户消息的刷新触发太晚 + 取值字段错 ✅已复核
- `P:609-618` 先 refresh、`625-628` 才 `recent.push`；`384-391` 已从旧 recentQuery 生成 query；`agent-loop:889-901` claim 早于 `:1028` append
- **`P:613` 从 `event.data.message.content` 读 lastUserText，而 `P:600` 自己用 `event.data`** → 标准 UserMessage 下 lastUserText 恒为空串，`450-451` 操作意图通路通常不启动
- **修复**：先写 recent/lastUserText 再创建刷新；不要假设 `session/event:user` 早于 assembly

### N04 界面"关闭记忆"不能可靠停止捕获/抽取 ⚠️未复核
- `C:1146-1148` 只写后端 `session_enabled:<sid>`；`P:214-220` 首次读取后永久返回 `enabledCache`，无失效机制（初读失败还缓存 `true`）；`P:756-761` 关闭不取消在途 refresh/extract
- `H:446-452/478-504` 只检查另两类开关，未检查 `session_enabled`
- **修复**：统一每会话开关/版本 + 失效通知或有界 TTL；off 取消在途任务；捕获/注入/抽取同一门禁

### N05 工具/搜索请求遗漏 `session_id` → session 记忆"存了找不到" ⚠️未复核
- `P:817-825` 的 `memory_save` payload 无 `session_id`（却允许 `scope=session`）；`P:794`、`844`、`C:1117` 检索同样不发
- `server.py:993-1000` 对 `scope=session` 要求请求 `session_id` 非空且相等 → 归属为空 + 检索过滤掉
- **修复**：保存与三个检索入口一律传当前 sid；无法解析时拒绝 session 写入

### N06 可选状态卡 404 会把检索成功的记忆全部丢弃 ⚠️未复核
- `P:91-92` 默认 `INJECT_CARD=true`；`411-445` 已取得检索结果；`485-488` 卡接口非 2xx 即 `return false`；`496-498` 从不发布缓存
- 后果：新会话无卡或卡读取暂时失败时，记忆注入整体失效，且每次 assembly 重复整套查询
- **修复**：区分"无卡 404"与真错误，按空卡继续发布记忆

### N07 Host 仍读已移除的 events/inspect ⚠️未复核（兼容性/触发未自证）
- `H:231/515` 无保护遍历 `agent.session.events`，`248` 直接返回；`251` 调 `sessionPersistence.inspect`
- 0.1.5 运行时 Session 无 `events` getter（`:989` log、`:1107` `snapshotEvents`）；`SessionPersistence` 无 `inspect`
- **生产触发未自证**：Host 自身 `five-turn state card updated` 与 `failed` 6 小时均为 **0 次** → 该 cadence 路径未成功执行过；preset 的 `AI updated state card` 不能作为 Host 证据
- **修复**：改用 `snapshotEvents()`；持久化用 read handle 并 `finally close`；失败清理 `cadence` + 退避

### N08 抽取器称"增量更新"，写卡却全量覆盖 ⚠️未复核
- `P:113` 要求增量；`335` 只给当前 dialog；`685-695` GET 旧卡却只用其 version，payload 未出现的 `goal/current_plan` 写空串、数组写 `[]`，已有决定不合并 → 只输出 `next_steps` 会清空旧目标/方案/决定
- `H:140-144` 已采用"现有卡 + 完整合并"的另一套协议
- **修复**：选定唯一写卡责任方；向模型传现有卡或按字段 patch 合并

### N09 抽取先丢队列，失败/部分成功都无法恢复 ⚠️未复核
- `P:639-645` 在 LLM 调用前 `buckets.delete`，超时/解析失败直接 return；`674-678` 只要 HTTP 200 就把全部 items 计成功（而 `server.py:1581-1591` 在 200 的 `added` 数组里逐项返回 error）
- **修复**：pending/in-flight/acked 队列 + 逐项确认后删除 + 幂等键 + 有限退避

### N10 只 JSON.parse 不校验结构，能把辅助抽取升级为主回合 error ⚠️未复核
- `P:355-356` 接受任意 JSON；`647-648` 对 `result.memories` 只查 `length` 就 `.filter` → `{"memories":"x"}` 必抛；`agent-loop:976-991` 会把异常置 `turnEnds.kind=error`
- **修复**：schema 校验 + 错误隔离在插件任务内

### N11 Host 状态卡抽取绕过 preset 的去敏 ⚠️未复核
- `H:142-144` 把现有卡与**原始 dialog** 直接送 LLM，无脱敏；`P:335` 则显式 `redactSensitive(dialog)`
- 后果：对话含凭据时原值出网（后端存储脱敏无法撤销已发生的出网）
- **修复**：出网前共用敏感过滤器；固定抽取 provider

### N12 上游响应中断缺终止处理 ⚠️未复核（崩溃未动态自证）
- `H:54-63` 的 `IncomingMessage` 只监听 data/end；`681-684` 直接 `upRes.pipe(res)`；`693` 未把客户端 abort 联动到 upstream
- **修复**：用 `pipeline` + 统一 settle，处理 `aborted/error/close`；已发头时 destroy

### N13 Host 备用召回未使用已修的会话 workspace 解析 ⚠️未复核
- `H:193-195` 仅 `harness-memory*` 预设跳过 Host；`403-413` 却把 `config.workspace || 'deepseek-harness'` 当当前 workspace，而非从 `agent.id` 解析归属
- **修复**：共享同一归属函数 + 契约测试

## 🟡 中（要点）
- **N14 压缩摘要字段读错** ✅已复核：`P:570-585` 读 `event.data.message.content`，而 0.1.5 契约是 **`data.summary: ContentBlock[]`**（官方类型注释明写）→ `ctxt` 恒空、`586` 的 topic-summaries 永不写入，压缩后主脉络链缺失
- **N15** `P:102-104` 要求 `type=rule`，但 `269-271` 分组只含 preference/decision/goal/plan/fact/episode → rule 与其它类型同批返回时被静默丢弃
- **N16** `C:728/742` 把 effective config 全量 POST 成 session override → 改一项锁死所有默认值，reset 后再存又覆盖
- **N17** `C:742` 存 `session.<sid>.*`，但 `P:174-197` 只读全局 `/v1/settings/deepmemory.*`；`533-546` 只消费 `context_automation`/`completion_k` → 会话级开关多数不生效
- **N18** `P:174-198` 最多 21 个串行 HTTP（各 25s 超时）→ 最坏 ~525s；无 singleflight；`H:110-147` 两个 LLM 流无超时/取消
- **N19** `P:709-724` 每次输出任务都 POST 新任务 → 同一任务状态变更产生第二张卡
- **N20** `C:1170-1195` 在 `MemoryPanel` 内定义 `ListEditor` → 每次 render 新身份 → 输入重建丢焦点
- **N21** `C:191-199` 非 2xx 丢弃后端 error；`1058-1060` 敏感展开无条件 `needsAuth=false`
- **N22** `C:1064-1067` "原文"只取 `sources[0]` → 多源/分段后续证据 UI 不可达

## 🟢 低（要点）
- **N23** `C:211-214` 手插 `<style>` 无清理（HMR 累积）；图谱 wheel 监听已有清理，未误报
- **N24** `P:352/358` 直接打印未验证 LLM 输出前 300/240 字符 → 可能落 journal
- **N25** `P:18` 绝对导入 `/usr/local/node/lib/node_modules/.../dsh-tools` → 换机/升级会致 preset 注册前加载失败

## 已确认通过 / 设计选择（不计缺陷）
- `H:394` 已是 `ctx.settings.register`；`P:22` 有 `inject=['tools']`；`P:521` 有 `context.order`；`P:534` 保留 `next()` 返回值 —— 历史问题已修，不重复
- `Session.id` 运行时确为 `header.id` getter（`dsh-session:1008-1010`），`agent-loop:754-758` 直接保留 Session，`dsh-agent registry:476-478` **硬校验 `agent.id===agent.session.id`** → `agent.session.id` 可靠，用 `agent.id` 属简化而非修 bug
- `C:1476-1492` 的 `slots.inject` 返回 disposer 符合契约，`client-ui-renderer:1015-1073` 会经 `ctx.effect` 回收 → 不算 slot 泄漏
- 未发现可达 XSS（文本走 React 文本节点）；手写图谱布局复杂度、字符预算代替 token 预算属设计取舍；历史"Host 去 Origin + 自动补 token + 原样代理"不重复编号

## 纵深防御顺序（astra 建议，值得照做）
1. **后端在删除原语旁验证备份 ID**（首要、不可绕过的边界）
2. Host 对管理类 method+path 建白名单、拒绝 dot-segment/编码变体、保留身份审计
3. 浏览器 confirmation 只防误操作，**不承担安全授权**；不要只做前端 `encodeURIComponent`

## 第二轮 Top 10 ROI
1. N01 按来源过滤合成输入 + `message.id` 去重（切断反馈循环）
2. N02 正确更新 `assembly.contexts`
3. N03 修 `lastUserText` 取值与入队时序
4. N04 统一会话开关与缓存失效
5. N05 全部 save/search 入口补 `session_id`
6. N06 无卡 404 按空卡降级
7. N14/N15 压缩摘要字段与 rule 分组（改动极小）
8. N09/N10 模型输出 schema 校验 + 逐项 ack + 幂等重试
9. N08/N16/N17 统一状态卡更新协议与有效会话配置
10. N07/N12/N18 兼容标准 Session API + 流错误/取消/统一 deadline

## 附 C：流程约束（`dsh-deepmemory/AGENTS.md`）

- **部署两阶段铁律**：任何生产变更必须 `测试机(3091) → 生产(6230/3081)`；先跑 `harness-memory-archive/tools/sync-test-env.sh`
- **Preflight（改 preset JS / settings.yaml）**：`node --check` → ESM import 冒烟 → `session.models` 断言 `ok:true` → 发测试消息验证 preset 链；任一失败立即回滚
