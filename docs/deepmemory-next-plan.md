# deepmemory 下一步方案：命中率检查 + 嵌入迁移工具 + 生产同步

> 2026-09-01 · 基于契约 v0.2 与升级计划 v1（B 已完成、A 卡在生产同步、C/D/E 未做）
> 状态：方案（待确认执行）

## 0. 目标（三件事，按序执行）

1. **插件命中率检查脚本**（每次更新生产前必跑，防再击穿）——补上"升级计划缺的 gate"
2. **记忆库迁移工具**（更换 embedding 模型：local fastembed 换模型 / 切 api provider / 换维度）——派生维护工具
3. **生产同步 v063**（G1-G5 + 语义检索 + 插件稳定版）——在 ① 通过后执行（A 阶段补完）

---

## 1. 插件命中率检查（gate，更新生产前）

### 1.1 脚本
`agent-preset/scripts/check_cache_health.py`（绝对路径）
```
输入：会话目录 /www/dsh/home/sessions + 插件路径
输出：PASS/FAIL + 命中率报告
```

### 1.2 检查项（两条腿）
| 项 | 方法 | 通过标准 |
|---|---|---|
| **静态健康**（插件文本稳定性） | grep/解析 `plugin-v3.js`：无 `\[i0\.` 无 `topic:` 格式化、formatMemories 有 `sort(id)`、assemble 有 userCount 冻结 | 全部满足 |
| **动态健康**（实际命中率） | 解析最近会话 `assistant/message` 的 usage（cacheReadTokens/inputTokens），算最近 15 步命中率 | ≥90%，且除回合首请求外无一 miss |></br>

### 1.3 实现
- 复用 `/tmp/check_cache_rate.py`（已验证可跑，os.walk 找会话 + usage 解析）
- 阈值可配（默认 0.90）
- **存放**：`/www/deepseek harness workspace/harness-memory-archive/scripts/check_cache_health.py`
- 生产同步命令前：`python3 scripts/check_cache_health.py --plugin /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`
  → PASS 才允许 `cp 同步 + restart`

### 1.4 防击穿清单（脚本背后的人肉 gate，写入升级计划）
- 任何 plugin-v3 改动 → 先测试机验证命中率 → 生产
- 注入文本只允许：稳定 id 排序、无浮点/日期/topic 元数据、回合内冻结
- 若命中率 <90% → 回滚插件 + 查注入变化源

---

## 2. 记忆库迁移工具（更换 embedding 模型）

### 2.1 现状（复用点）
- `embed_texts()` 已支持 local(fastembed)/api 双 provider（server.py:215）
- `rebuild_indexes()`（server.py:1861）已：全库 SELECT active → embed_texts → 影子 FAISS → 原子替换 → BM25 重建；空库 warmup 定维；**维度变化由 faiss IndexIDMap(IndexFlatL2(d)) 自动处理**
- `dim.json` 记录当前维度（get_embed_dim/set_embed_dim）
- `memory.faiss` 索引文件；`config_schema.json` embedding 组（provider/local_model/api_base_url/api_model）

### 2.2 工具形态（二选一）
- **A. CLI 独立脚本**（推荐，离线安全）：`scripts/migrate_embedding.py`
  ```
  usage: migrate_embedding.py [--new-model BAAI/xxx] [--provider local|api]
                              [--api-base ...] [--api-model ...] [--db /path/memory.db]
                              [--faiss /path/memory.faiss] [--dry-run]
  ```
  流程：备份 faiss+dim.json → 写新 config（embedding.*）→ 加载新 embedder（warmup 定维）→ 全库重嵌（复用 _rebuild_indexes_internal 逻辑）→ 原子替换 → BM25 重建 → **验证**（抽样 5 条 query 向量搜索返回正确 id + 维度报告）→ 输出迁移报告
- **B. 维护路由**：`POST /v1/maintenance/reembed`（改 config + 触发 rebuild_indexes + 回验）
- **推荐 A**（不依赖 server 进程，可直接对生产/测试机库操作，迁移中服务可继续只读）

### 2.3 迁移安全
- 迁移前：备份 `memory.faiss` + `dim.json` + `memory.db`（已有 /v1/backups）
- 迁移后：验证向量搜索正确性（抽样 query → 期望 id 在 top-k）
- 维度变更：faiss 重建自动适应；BM25 不受影响
- 回滚：恢复备份 faiss/config 即可

### 2.4 测试机先行
- 测试机（/www/dsh-test-memory）先跑迁移工具（换一个候选模型，如 bge-small-en 或切 api provider）→ 验证搜索 → 再生产

---

## 3. 生产同步 v063（A 阶段补完）

### 3.1 前置 gate
- ① 命中率检查 PASS（§1）
- ② 迁移工具在测试机通过（§2.4）

### 3.2 同步内容
```
memory-server/{server.py, v2_domain.py, config_schema.json} → /www/deepmemory-v063-deploy/memory-server/
agent-preset/memory-plugin/plugin-v3.js → /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js（已是稳定版，确认无回退）
```

### 3.3 步骤（备份先行）
1. 备份生产：memory-server/ + memory.db（cp -r 到 .bak-时间戳；/v1/backups 已有 db 备份）
2. cp 同步 server.py/v2_domain.py/config_schema.json
3. 重启 dsh-memory-server.service + dsh-web.service
4. 库迁移自动跑（migration5：library 列 + document_links 表；memory_class 补 default）
5. 验证：
   - PRAGMA 列：library=True, document_links=True, topic_id=True
   - G5 实测：POST doc-link → GET for-doc → GET <id> doc_links（复用测试机命令）
   - 语义检索：POST /v1/embeddings + search 正常
   - 命中率检查 PASS（插件稳定版生效）
6. git 推送 Gitea（d870f6f + e6e212d + 待提交项）

### 3.4 回滚方案
- 恢复备份 server.py/v2_domain.py + memory.db → restart → 确认旧库列状态

---

## 4. 执行顺序（确认后）

| 步 | 内容 | 产出 |
|---|---|---|
| S1 | 写 check_cache_health.py + 跑一次（基线确认当前 PASS） | 脚本 + 命中率报告 |
| S2 | 写 migrate_embedding.py（复用 _rebuild 逻辑）+ 测试机验证 | 迁移工具 |
| S3 | 命中率检查 PASS 后 → 生产同步 v063（§3.3） | 生产 G1-G5 落地 |
| S4 | 推送 Gitea + 更新升级计划 checklist | 收尾 |

> S3 之前任何一步 FAIL → 停，先修（不盲目同步）
