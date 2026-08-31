# deepmemory 升级计划（绝对路径版）

> 规范：**本文档所有命令/路径均为绝对路径**，禁止相对路径与 `find` 推断。
> 产生于环境漂移教训（harness / hardness / v063-deploy 混淆导致同步未生效、库被误查）。

---

## 0. 环境基线（真实路径，唯一事实源）

| 角色 | 绝对路径 | 服务 | 端口 | 备注 |
|---|---|---|---|---|
| **生产 memory-server（真实运行）** | `/www/deepmemory-v063-deploy/memory-server` | `dsh-memory-server.service` | 6230 | 库 `data/memory.db`（2026-08-31 基线 234 条，活库在增长——验证用迁移前后行数一致而非写死数值）；`v2_domain.py`/`server.py` 为部署副本 |
| **生产 core（DSH 引擎）** | `/opt/dsh-rc2-core` | `dsh-web.service` | 3081 | `@deepseek-ai/dsh` 版本 `0.1.1-rc.2` |
| **生产 web home** | `/www/dsh/home` | — | — | 会话/配置/`.agent-presets/_memory-plugin/plugin-v3.js` |
| **测试机 core** | `/opt/dsh-rc2-test-core` | `dsh-test.service` | 3091 | 从生产 core 复制恢复；版本 `0.1.1-rc.2` |
| **测试机 memory-server** | `/www/dsh-test-memory` | `dsh-test-memory.service` | 6240 | 独立库（新，0 条）；代码复制自 v063-deploy |
| **测试机 home** | `/www/dsh-test-home` | — | — | **当前为空**（`.agent-presets`/`profiles/web/node_modules/dsh-deepmemory` 均不存在），B0 需建 |
| **源码仓库（开发）** | `/www/deepseek harness workspace/harness-memory-archive` | — | — | 双远端：origin=Gitea `LazyFish/dsh-deepmemory`、github=`UnKnownFish125/dsh-deepmemory`；A 前需 commit 全部改动（plugin 终态已 69671c8） |
| **alpha（可选）** | `/www/dsh-test-alpha`（已删除，需重建） | `dsh-alpha-test.service` | 3092 | node ≥22.19（已在用 v22.23.2） |
| **node 运行时** | `/usr/local/node`（v22.23.2）；旧备份 `/usr/local/node-22.17.0-backup` 已不存在 | — | — | pnpm 软链 `/usr/local/node/bin/pnpm` |

**抄送路径（勿用，仅留痕）**：`/www/deepseek hardness workspace`（差一个 s）、`/www/deepseek harness workspace/memory-server`（旧部署副本，非运行）——**一律以 0 表为准**。

---

## 1. 升级目标（对齐已定方案）

1. **记忆四项修复落到真实生产**（v063-deploy 副本 + v063 库）
   - ① 绝对时间（created_local/accessed_local 进注入）
   - ② 会话归属（dialog_scoped 兜底 + 抽取提示词会话优先）
   - ③ 继承可判断（注入 detail 日志）
   - ④ 效力（importance 标签 + 偏好保底 ≥0.8 必入 + 提量 k=10-13）
2. **缓存修复**：注入段稳定（初始化生成、撤回每轮刷新——已做）；B+C 后续（摘要链 append-only + 注入后置）
3. **info registry 按需建库**（第 11 章：域→库、库内成表、按需求路由）
4. **L0 规则固化 skill**（"先测试机验证"等进 L0）——标记不删除
5. **alpha 实例**（0.1.2-alpha.1）重建对比（可选）

---

## 2. 分阶段执行计划（每步绝对路径 + 测试机先行 + 验证）

> **硬性前置：B 阶段全部通过后才可执行 A**（测试机先行，同步生产后行）。
> 阶段语义：B=测试机验证（先行），A=生产同步（后行），C=info registry，D=缓存方案，E=L0 固化，F=alpha。

### 阶段 B：测试机验证（先行，硬性前置）
- [ ] B0 **建目录+安装插件**（测试机 home 为空，必须补装）：
  - `mkdir -p /www/dsh-test-home/.agent-presets/_memory-plugin`
  - `mkdir -p /www/dsh-test-home/profiles/web/node_modules/dsh-deepmemory`
  - 从源码复制：`/www/deepseek harness workspace/harness-memory-archive/agent-preset/memory-plugin/plugin-v3.js` → `/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js`
  - 复制 web 插件：源码 `web-plugin/{client.js,index.js,package.json,dsh.patch.yml}` → `/www/dsh-test-home/profiles/web/node_modules/dsh-deepmemory/`
  - 转换 client：`/opt/AstrBot/venv/bin/python3 "/www/deepseek harness workspace/harness-memory-archive/scripts/fix-client-bundle.py" "/www/dsh-test-home/profiles/web/node_modules/dsh-deepmemory/client.js" dsh-deepmemory`
- [ ] B1 同步测试机 memory：`/www/deepseek harness workspace/harness-memory-archive/memory-server/{v2_domain.py,server.py,config_schema.json}` → `/www/dsh-test-memory/`
- [ ] B2 重启测试机：`systemctl restart dsh-test-memory.service dsh-test.service`
- [ ] B3 验证（测试机 6240 + 3091 浏览器）：
  - `TOKEN=$(cat /www/dsh-test-memory/data/api-token); curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:6240/v1/health`
  - 库列含 topic_id/event_time（信息 schema 命令，见 §3）
- [ ] B4 注入格式校验（实际实现）：
  - 字段为 `dialog_scoped`（plugin-v3.js:454）
  - 渲染格式为 `[i0.95/2026-08-31/ topic:…]`（非 [i=0.95/日期]）；grep 校验 `grep -c 'dialog_scoped'` 与 `grep -c '\[i0\.'` 均应 >0
  - grep 校验：`grep -c "dialog_scoped" /www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js`

### 阶段 A：生产同步（B 全通过后）
- [ ] A1 源码备注（v2_domain 幂等 ensure 已含 topic_id/event_time；server.py migration4 走 schema_version 安全重复）
- [ ] A2 同步生产 memory：`/www/deepseek harness workspace/harness-memory-archive/memory-server/{v2_domain.py,server.py,config_schema.json}` → `/www/deepmemory-v063-deploy/memory-server/`
- [ ] A3 同步生产插件：**源码已含生产终态（69671c8 commit，每轮刷新）**，`cp` → `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`（不会再回退）
- [ ] A4 重启：`systemctl restart dsh-memory-server.service dsh-web.service`
- [ ] A5 验证：
  - 库列命令复用 §3（无语法错误版）
  - **行数一致**：迁移前后 documents 行数一致（2026-08-31 基线 234；不写死数字，用"迁移前后相等"断言）

### 阶段 C：info registry 按需建库### 阶段 C：info registry 按需建库
- [ ] C1 新建 `/www/deepseek harness workspace/harness-memory-archive/memory-server/info_store.py`：`InfoStore(db_dir)` + `get_domain_db(domain)` 按需建库（`data/info/<domain>.db` + `entries` 表）+ `registry.db`（domains/keys 目录）
- [ ] C2 server.py 加路由：`/v1/info/<domain>/<key>`（GET 按域路由/POST 按需建库）/`/v1/info/keys`/`/v1/info/domains`
- [ ] C3 测试机验证（先）：6240 `/v1/info/env/server.ip` POST → 自动建 `env.db` → GET 命中；未登记域 404 提示
- [ ] C4 生产同步 + 重启 + 验证

### 阶段 D：缓存方案 B+C（摘要链 + 注入后置）
- [ ] D1 `topic_summaries(topic_id, seq, summary, start_time, end_time, prev_seq)` 表：**先测试机库 `/www/dsh-test-memory/data/memory.db` 建表并验证 → 再生产库**（建表跟随 D4 先测后产顺序，不得生产提前加）
- [ ] D2 摘要链 append-only 分块；`trigger_bytes=60000` 按长度触发（config_schema 已有）
- [ ] D3 注入段移到对话流（最新用户消息前）——`plugin-v3` 组装改动
- [ ] D4 测试机验证缓存命中（cacheReadTokens 提升）→ 生产

### 阶段 E：L0 规则固化（标记不删除）
- [ ] E1 判定：同约定抽取 ≥2-3 次/违反 → 标记 `rule_candidate`
- [ ] E2 固化 → skill 文件（带 provenance），**从注入池移除但保留 docs 标记**
- [ ] E3 测试机验证 → 生产

### 阶段 F：alpha 重建（可选）
- [ ] F1 `git clone --depth 1 --branch dsh-v0.1.2-alpha.1 https://github.com/deepseek-ai/deepseek-harness.git /www/dsh-test-alpha`（代理）
- [ ] F2 `env HOME=/root /usr/local/node/bin/pnpm build`（node 22.23 已满足）
- [ ] F3 `dsh-alpha-test.service`（3092）+ 对比验证

---

