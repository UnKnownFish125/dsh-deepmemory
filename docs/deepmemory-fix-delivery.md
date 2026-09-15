# deepmemory 审核修复交付说明

**日期**：2026-09-15
**来源**：`docs/astra-plugin-review.md`（astra 两轮只读审核 S01–S16 + N01–N25，主 agent 逐条复核）
**状态**：批次 1/3 已生产生效；批次 2/4 代码就位**待重启 dsh-web**；批次 5 反向已消除、正向待决策

---

## 一、当前状态速览

| 批次 | 内容 | 状态 |
|---|---|---|
| 1 安全 | S01 备份删除路径穿越 | ✅ **生产已生效** |
| 2 记忆质量 | N01/N03/N05/N06/N14/N15 | ✅ 代码就位 —— **待重启 dsh-web** |
| 3 后端一致性 | S02/S03/S04/S08/S09a/S09b/S10/S12/S13/S14/S15/S16/S17（+`rule_candidates` 性能） | ✅ **生产已生效** |
| 4 客户端体验 | N04/N12/N13/N16/N20/N21/N22/N23/N24/N25 | ✅ 代码就位 —— **待重启 dsh-web** |
| 4（缺） | **N17** preset 配置按会话解析 | ⬜ 需专门窗口（核心链路重构） |
| 5 部署漂移 | S05 | 反向 ✅ 已消除；**正向（P1 上生产）待决策** |
| — | S06/S07（仅仓库 P1） | ✅ 已修并测试机验证（生产未部署该特性） |
| — | S18 `add_batch` 非原子 | 判定为**设计选择**，不修 |

**服务状态**：`dsh-web` / `dsh-memory-server` / `dsh-literature` / `dsh-test` / `dsh-test-memory` / `embedding-bgem3` 全 active
**生产关键值**：`dim.json = {"dim":1024,"fp":"api:bge-m3"}`、检索正常、11 份备份完好

---

## 二、已生效（生产运行中，无需操作）

### S01 备份删除路径穿越（🔴 最高危）
`DELETE /v1/backups/<name>` 只做 `basename` 即 `os.path.join(BACKUP_DIR, name)` + `shutil.rmtree`，未拒绝 `.`/`..` → `DELETE /v1/backups/..` 可删光整个 `DATA_DIR`，且经 `/mem-api` 代理可达（代理自动补 token、删 origin，鉴权与 origin 检查都拦不住）。
→ 白名单 `backup-YYYYMMDD-HHMMSS` + `realpath` 必须为 BACKUP_DIR 直接子目录；`restore_backup` 同样校验。

### S02 模型身份指纹
`dim.json` 只记维度，`get_index()` 仅比 `_index.d != dim` → 换成**同为 1024 维**的另一模型时索引不重建（查询用新模型、库里是旧向量，语义混库且无报错）。
→ `dim.json` 增加 `fp="provider:model"`，维度或指纹任一变化才重建；**旧 `dim.json` 无 fp 时补写而非重建**（避免升级瞬间 10 分钟级全量重建）；`set_config_values` 变更 embedding 时同时失效本地模型单例。

### S03 影子重建竞态
重建用固定临时文件 `INDEX_PATH + ".shadow"` → 并发重建互相覆盖；重建耗时可分钟级、期间写入落旧索引，函数只"描述"指纹、发布前不复验（丢增量静默）。
→ 唯一临时名（pid+uuid）+ `finally` 清理；发布后回读 DB 指纹比对，不一致返回 `stale:true` 并打 WARN。

### S04 FAISS 检索持锁
`vector_search` 的 `idx.search` 不在 `_index_lock` 内，与 add/remove 并发不安全。
→ search 全程持锁（`_index_lock` 是 RLock，与 `get_index()` 不冲突）。

### S08 权重/衰减配置不生效
`apply_weighting` 读入局部 `alpha/beta/gamma/decay_rate` 却使用**全局常量** → 配置页调参无效。
→ 改用局部变量。

### S09 固化接口与下标
- S09a：HTTP 传 `similarity=` 而形参是 `min_similar` → **稳定 500**
- S09b：`similar_ids` 是整数列表却被当字典取 `r["id"]` → TypeError

### S10 检索缓存键截断
`cache_key` 用 `query.strip()[:200]` → 前缀相同、后缀不同的查询互相命中，复用错误结果。
→ 完整 query 的 SHA-256。

### S12 归档/恢复与 FAISS 不同步
归档只摘 BM25、不摘 FAISS（死向量占 `k*3` 候选位）；恢复只补 BM25、不补向量（"归档→重建→恢复"后永久缺失语义召回）。
→ 新增 `_index_remove_ids`/`_index_add_texts`，接入 archive/decay/consolidate/restore。

### S13 衰减按总年龄重复扣减
`days` 用"距参考点的总年龄"，每跑一次再乘一次指数 → 遗忘速度随调度频率变化（30 天无访问累计指数 465 天而非 30 天）。
→ 按 `max(ref, 上次衰减时刻)` 的**增量**计算。

### S14 session 级密钥绕过屏蔽
`get_config_values` 剥掉 `deepmemory.` 前缀后按**全局键名**精确匹配 `SECRET_CONFIG_KEYS` → `session.<sid>.embedding.api_key` 不被屏蔽，可经 `GET /v1/config` 回显。
→ 新增 `_is_secret_config_key()` 识别 session 命名空间，两处统一使用。

### S15 L2→cos 重复平方
`IndexFlatL2` 的 scores **已是**平方距离，代码却写 `1-(dist*dist)/2` → 真 cos=0.8 被算成 0.92（默认 RRF 只用名次不受影响，旧 merge 模式阈值判断过宽）。
→ 改为 `1-dist/2`。

### S16 embeddings 谎报模型名
`/v1/embeddings` 响应 `model` 硬编码 `bge-small-zh-v1.5`（bge-m3 迁移后仍在骗调用方）。
→ 返回实际生效的 provider/model。

### S17 备份快照不一致
先 SQLite 在线备份、之后**另行**复制 FAISS（不同窗口，并发写入可产生不配套备份）。
→ DB 备份 + 索引复制 + 计数放进**同一 `_index_lock` 窗口**；manifest 记录 `fingerprint{count,max_id}`。

### （额外，astra 未报）`rule_candidates` O(n²) embedding
对**每一对**记忆单独调 `embed_texts` → api provider 下 O(n²) 次 HTTP；S09a 修好参数名后接口从"稳定 500"变成"永久卡住"。
→ 批量 embedding + 单次矩阵乘 + `max_scan` 上限：**120s 超时 → 5.3s**。

---

## 三、待重启 dsh-web 生效（代码与部署件均已就位，三处副本一致）

> 重启命令：`systemctl restart dsh-web`（**重启前确认无 running 会话** —— 首个访问会触发全量会话索引，表现为卡死数分钟）

### 批次 2（preset）
| 项 | 修复 |
|---|---|
| **N01** | 跳过 `source.kind!=='user'` 的**合成输入**。DSH 把 runtime context（含我们注入的记忆）构造成 `user/message`（只标 `source.kind='plugin'`），preset 只看消息类型不看来源 → 注入内容被当用户输入**再次抽取回库**，形成记忆自我强化/污染循环；同时覆盖 `lastUserText`、破坏回合内冻结 |
| **N03** | `lastUserText` 取值修正（原读 `data.message.content`，标准 UserMessage 的 content 在 `event.data` 上 → 恒为空串，操作意图通路不启动）；先入队 recent 再启动刷新（避免用上一轮文本检索） |
| **N05** | 工具检索/保存/briefing 补 `session_id`（`scope=session` 记忆"存了找不到"） |
| **N06** | 状态卡非 2xx 降级为"无卡继续"，不再连带丢弃**已检索成功**的记忆 |
| **N14** | compaction 摘要改读 `data.summary`（官方契约；原字段恒空 → 压缩摘要链静默断开） |
| **N15** | `type=rule` 并入「规则与偏好」分组（原被静默丢弃） |
| **N04** | 会话开关缓存加 5s TTL（原首次读取后**永久**缓存 → 界面关闭后 preset 仍在注入/捕获） |
| **N24** | 解析失败不再打印未验证的模型输出正文（可能把敏感内容落进 journal） |
| **N25** | 去掉 `dsh-tools` 硬编码绝对路径**静态** import（换机/升级会让整个 preset 加载失败）→ 多候选解析 + 失败可降级 |

### 批次 4（client / Host）
| 项 | 修复 |
|---|---|
| **N20** | `ListEditor` 提升到模块级（原定义在组件内 → 每次 render 新身份 → 编辑列表**每输入一个字符丢焦点**） |
| **N21** | `api()` 保留后端 `error/code/status`（原仅返回 `HTTP <status>`，授权过期/版本冲突在 UI 上无提示） |
| **N16** | 会话配置只提交**与载入值不同**的键（原把 defaults+overrides 合并结果全量写回 override → 改一项锁死所有默认值） |
| **N22** | 原文保留完整 `sources` 列表并标注来源数量（原只显示 `sources[0]`，多源证据 UI 不可达） |
| **N23** | 插入的 `<style>` 随卸载移除（原 HMR 累积重复样式表） |
| **N12** | Host 代理上游中断处理（`upRes` error/aborted → destroy 下游；`res` close → destroy upstream；error 分支区分 `headersSent`） |
| **N13** | Host 备用召回按 sessionId 解析真实工作区归属（原直接用 `config.workspace \|\| 'deepseek-harness'` → 其他工作区检索为空或串区） |

---

## 四、待决策 / 待专门窗口

| 项 | 说明 |
|---|---|
| **S05 正向** | 仓库的 **P1**（断言事件/确认/撤销机制，含已修的 S06/S07）是否上生产？上则需完整测试机 preflight + 一次 memory-server 重启 |
| **N17** | preset 的配置是**模块级变量**（进程级）→ 会话级覆盖会污染其它会话。正解是按会话解析（改动覆盖 assemble/抽取/工具注册）。**核心链路重构，必须有专门窗口**（红线 8 的教训：核心链路改坏 → 所有会话每回合报错） |
| 低优先排期项 | N19（任务卡幂等）、N07（Host `session.events` 0.1.5 兼容）、N08（写卡全量覆盖）、N09（抽取队列丢失）、N10（输出结构校验）、N11（Host 抽取绕过脱敏）、N18（配置/模型发现无统一 deadline） |
| GitHub 推送 | 本地网络 `github.com` 直连不可达，Gitea 已同步；需走代理时重推 |
| 🔐 凭据 | `harness-memory-archive` 的 `origin` remote URL 里**明文嵌有 Gitea 凭据**，建议改为无凭据 URL + credential helper 并轮换该凭据 |

---

## 五、补丁脚本清单（`/www/scripts/`，全部幂等、可重复执行）

| 脚本 | 目标 | 覆盖 |
|---|---|---|
| `patch_backup_name_validation.py` | server.py | S01 |
| `patch_server_batch3.py` | server.py | S04/S08/S09a/S09b/S16 |
| `patch_server_s02_fingerprint.py` | server.py | S02 |
| `patch_server_s12_vectors.py` | server.py | S12 |
| `patch_server_s15_s10_s14_s13.py` | server.py | S15/S10/S14/S13 |
| `patch_server_s03_s17.py` | server.py | S03/S17 |
| `patch_server_s06_s07_p1.py` + `patch_server_s06_hook.py` | server.py（仅含 P1 的副本） | S06/S07 |
| `patch_server_s05_llmchat_align.py` | server.py | S05 反向对齐 |
| `patch_rule_candidates_perf.py` | server.py | `rule_candidates` 性能 |
| `patch_preset_batch2.py` | plugin-v3.js | N01/N03/N05/N06/N14/N15 |
| `patch_preset_n04_enabled_ttl.py` | plugin-v3.js | N04 |
| `patch_preset_n25_tools_import.py` | plugin-v3.js | N25 |
| `patch_client_batch4.py` | client.js | N20/N21 |
| `patch_client_n16_n22.py` | client.js | N16/N22 |
| `patch_n23_n24_n12.py` | client.js / plugin-v3.js / index.js | N23/N24/N12 |
| `patch_host_n13_workspace.py` | index.js | N13 |

约定：对**不含目标代码的副本自动跳过**（如生产无 P1 → S06/S07 脚本跳过）；每处改动留 `.bak-*` 备份。

---

## 六、回滚方式

1. **单点回滚**：每处改动都有同目录 `.bak-<标记>-<时间戳>`（如 `server.py.bak-s03s17-*`、`plugin-v3.js.bak-batch2-*`、`client.js.bak-batch4-*`），`cp` 回去后重启对应服务即可。
2. **整体回滚**：`git` 历史 —— `b3bf223`（批次 1-4 + 报告）→ `b544de9`（sync 守卫）→ `3262010`（N16/N22）→ `4652808`（S03/S17）→ `0ac5c51`（S15/S10/S14/S13）→ `01d9a3d`（S06/S07）→ `93a78d6` → `2048339`（S05 对齐）→ `031b3ce`（N23/N24/N12）→ `ce80100`（N13）→ `bf378fc`（N25）。
3. **数据侧**：迁移前备份在 `/www/deepmemory-v063-deploy/memory-server/data/migrate-embedding/20260915-171315/`；literature 在 `/www/dsh-literature-deploy/backup-embed-20260915-173035/`；生产当前 11 份常规备份完好。

---

## 七、重启 dsh-web 后的验证清单

按顺序执行（任一失败 → 立即用 `.bak-*` 回滚 preset/client 并重启）：

```bash
# 1) 服务与端口
systemctl is-active dsh-web && ss -ltnp | grep 3081

# 2) preset 挂载断言（ok:true 且 groups 数不减）
curl -s -X POST http://127.0.0.1:3081/api/session.models \
  -H 'Content-Type: application/json' \
  -d '{"type":"client-request","rpcId":"p","method":"session.models","payload":{"args":{"sessionId":"<活跃会话>"}}}'

# 3) 发一条真实消息，确认 preset 链（记忆注入 + 无 journal 报错）
journalctl -u dsh-web --since '2 min ago' | grep -iE 'deepmemory|error' | tail -20

# 4) 记忆服务侧
curl -s -X POST http://127.0.0.1:6230/v1/memories/search \
  -H "Authorization: Bearer $(cat /www/deepmemory-v063-deploy/memory-server/data/api-token)" \
  -H 'content-type: application/json' -d '{"query":"记忆","k":2}'
cat /www/deepmemory-v063-deploy/memory-server/data/dim.json   # 期望 {"dim":1024,"fp":"api:bge-m3"}
```

**重点观察项**（对应本次修复）：
- N01 生效后：注入的记忆**不再**被当作新用户输入重复抽取（journal 中不会因注入内容触发抽取）
- N17 **未修**，会话级配置仍可能不生效 —— 属已知
- N14 生效后：压缩后 `topic_summaries` 应有新写入（此前恒空）

---

## 八、一键验收工具

```bash
/opt/AstrBot/venv/bin/python3 /www/scripts/verify_deepmemory_copies.py
```

核对四组副本一致性 + 关键修复标记齐全性 + 六个服务健康 + 生产记忆服务自检（检索 HTTP、`dim.json` 指纹）。全部通过时退出码 0。

**注意内建的两条正确预期**（避免误报）：
- `memory-server.py` 分**两组**比较：生产版**不含 P1**（assertion 事件机制），与含 P1 的测试机/两 clone **本来就不同**，不是不一致；
- 标记用的是短前缀（如 `N13：`），改注释文字不会让检查失效。

## 九、重启生效链路预检（已确认）

| 检查项 | 结果 |
|---|---|
| Host/client 加载方式 | profile `dsh.profile.bundles` 含 `dsh-deepmemory`、`dependencies` 有它 —— 由 profile bundle 加载，**改部署件即改生效文件** |
| 部署件 = 仓库内容 | `index.js` md5 完全相同（当时 `ce77166015c1`） |
| preset 加载路径 | `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`（agent.cordis.yml 的相对路径行） |

**附带结论**：生产 profile 的 bundles 含 `dsh-better-sidebar`、`dsh-video-preview`、`@huanlin/dsh-plugin-better-sidebar-plugin-office`、`dsh-pet-dfeiyu-mo`，而**测试机都没装** —— 这正是 `sync-test-env.sh` 必须做「可解析 + 声明 `dsh.bundle`」过滤的原因（2026-09-15 两次测试机起不来都源于此）。

## 十、进行中 / 未完成

| 项 | 状态 |
|---|---|
| N07 Host 读 0.1.5 已移除的 `session.events`（Host 5 轮状态卡 cadence 从未执行） | ✅ 已完成并提交（`patch_host_n07_session_api.py`）；⚠️ cadence 未做真实回合端到端验证 |
| N08 写卡全量覆盖 / N09 抽取先丢队列 | ✅ 已完成并提交（`patch_preset_n08_n09.py`，含 live 探针实证） |
| S05 正向（P1 上生产） | 决策材料就绪（`s05-p1-deployment-decision.md`）；**P1 状态机已由主 agent 用真实 HTTP E2E 验证** |
| N17 preset 配置按会话解析 | 方案就绪（`n17-session-config-plan.md`）；待专门窗口，建议先做 P0 |
| N19 任务卡幂等 | 未开始（剩余唯一排期项，需跨端改动） |

---

## 十一、部署拓扑与隐患（2026-09-16 核实，重要）

### 11.1 preset 有两个副本，只有一个在生产链路上
| 文件 | 行数 | 挂载者 |
|---|---|---|
| `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | 988（含全部补丁） | **`harness-memory-task`**（"任务工作模式"，`settings.yaml:16` 的 **default**）→ `agent.cordis.yml:249` 挂 `../_memory-plugin/plugin-v3.js` |
| `/www/dsh/home/.agent-presets/harness-memory/memory-plugin/plugin-v3.js` | 610（9/8，**0 补丁**） | `harness-memory`（"记忆增强模式"）→ `agent.cordis.yml:299` 挂 `./memory-plugin/plugin-v3.js` |

**结论**：本次 20 项修复落在**生产默认 preset 实际加载的文件**上 ✓。
**待决**：另一个 preset（记忆增强模式）用自带旧副本，二者差异 506 行（不同功能集，**不可直接覆盖**）；若该 preset 仍在使用，需单独评估同步。

### 11.2 测试机曾指向生产库（已修）
- `http://127.0.0.1:6240/v1/config` 的 `deepmemory.server_url` 原为 **`http://127.0.0.1:6230`（生产）** → 测试实例的 preset 会写生产数据。
- **已改为 `http://127.0.0.1:6240`** 并复核；生产侧 `server_url` 未被误改（仍 6230）。
- 影响范围：在此之前的"测试机验证"若涉及**数据写入**则不可信；本次的 P1 E2E 是 `curl` 直连 6240（改的是测试库、已清理），**未受影响**。

### 11.3 profile 符号链接指向（确认加载哪一份）
- `profiles/web/node_modules/dsh-deepmemory` 是指向 profile 内 `.pnpm/...schemastery@3.18.1/...` 的符号链接 = **本次修改的文件** ✓
- 另有 **两份只读旧副本**未被同步（不影响运行，仅作排障时勿被误导）：`.pnpm/...schemastery@3.18.2/.../index.js`（9/10 旧版）、`node_modules/.ignored/dsh-deepmemory/index.js`（9/1 旧版）。

### 11.4 `/mem-api/*` 是泛代理（P1 上生产后的新增暴露面）
Host 插件把 `/mem-api/*` 无条件转发到 memory-server：任意 method + path，**自动注入 token、去掉 Origin**（`index.js:717-736`）。P1 上线后 `POST /mem-api/v1/assertions/<id>/{promote,revoke}` 从 web 同源可达，且 `origin_id` 由调用方给定 → 持 token 者可自导自演"两个 origin 确认"触发 auto-adopt，或 revoke 使某条记忆从 `mode=current` 召回中消失。
**当前无消费方调用**（preset/Host/client 的 `assertion` 引用计数均为 0），属"上线后新增"而非现存漏洞。

---

## 十二、N01 过滤逻辑的独立验证（最高风险项，已排除）

N01 的修复是「跳过 `source.kind` 存在且 ≠ `'user'` 的输入」，目的在切断"注入的记忆被当作用户输入再抽取回库"的自污染循环。**但它有误伤风险**：若真实用户消息的 `source.kind` 不是 `'user'`，就会导致**记忆完全不抽取** —— 比原缺陷更严重。已独立验证：

| 消息来源 | 构造点 | `source` | 过滤后的行为 |
|---|---|---|---|
| **合成 runtime context**（assemble 时注入的记忆） | `dsh-agent-loop` 的 `project()`（`lib/index.js:340-352`） | `{ kind: "plugin", plugin: SOURCE }`（sections 非空时另带 `form:"snapshot", sections`） | ✅ **正确跳过**（这正是要切断的自污染路径） |
| **真实用户输入** | `dsh-acp/lib/index.js:833` | `{ kind: "user" }` | ✅ **正常处理**（`kind !== 'user'` 为 false） |
| 无 `source` 字段的消息 | — | 无 | ✅ **不过滤**（`_src` 为 null）—— 保守设计，宁可多抽也不误杀 |

判定条件：`if (_src && _src.kind && _src.kind !== 'user') return`
**结论：N01 不会误伤真实用户消息**；且对"没有 source"的消息采取放行策略，不存在"静默全不抽取"的失败模式。
