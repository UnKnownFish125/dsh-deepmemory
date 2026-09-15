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
