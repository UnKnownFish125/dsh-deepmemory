# deepmemory 本体 codex 审核报告（2026-09 codex-auto-review/uuapi）

> 范围：memory-server 仅本体（server.py/v2_domain.py/info_store/sensitive/kb-query）——372K tokens 深度审
> **已复现 3 个 bug**：① v2 import 明文写普通表 ② hard purge 因 immutable revision 触发 IntegrityError ③ 软 purge blocked task 触发 CHECK 约束

## 🔴 高优先级（10）
1. **鉴权可选+无路由级授权**（server.py:2374）——token 存在才校验；敏感/审计/配置/删除/维护共用弱校验 → 强制 token+权限分级（只读/写/管理/敏感）
2. **workspace/session 隔离被旧路由绕过**（/v1/memories/list/overview/graph/source/audit...；scope_allows 未知 scope 默认放行）→ 统一身份 middleware + 未知 scope 拒绝
3. **session key 无保护**（创建/轮换不需身份；export/purge/import 不校验 body.key）→ 全链路校验 key+workspace
4. **v2 写路径绕过脱敏**（v2_domain:936 import/1238 lifecycle；revision before/after 存明文）→ 统一脱敏/vault；revision 只存脱敏或加密
5. **敏感审批可自授予**（server.py:2886 信请求体 user_id/confirmed）→ 服务端生成身份+校验 owner session
6. **hard purge 必败 + 软 purge 破坏状态**（v2_domain:1062 immutable delete trigger；blocked→completed 违 CHECK）→ tombstone/级联；合法事件
7. **相似记忆合并无隔离**（server.py:983 全局 FAISS top-1）→ 先按 scope/ws/session/library/persona 过滤再合并
8. **SQLite/FAISS 非原子**（先 SQL 后索引；中途异常=抖动）→ 索引 generation/outbox/启动校验重建
9. **备份/恢复无强鉴权+可能泄密**（server.py:1980 复制完整 SQLite 含 protected_sources 明文）→ 加密+单独授权+恢复原子化
10. **单进程全职责**（HTTP/SQLite/FAISS/嵌入/LLM/夜间于一体——server.py:3187）→ 拆分 API/索引 worker/维护 worker

## 🟠 中优先级（11）
- 请求线程无上限（嵌入 60s/LLM 120s 占线程）→ 有界线程池/队列/熔断
- 每请求跑迁移/DDL（server.py:2442 V2Store.migrate()）→ 仅启动跑；迁移失败阻止启动
- 迁移失败静默吞（server.py:470 pass）→ 记录+停服务+repair 命令
- 搜索缓存无失效/无锁（45s 缓存在 server.py:888）→ 版本号 key+写操作失效+锁
- 检索 N+1（每条敏感都查 protected_sources server.py:919）→ 批量聚合
- 归档/衰减无统一 v2 revision（旧接口直改 status/archive_library 绕过单跳）→ 统一走 v2 + actor/reason/审计
- 删除留孤儿（delete_memory 不清理 sources/protected/links/archives/revisions；无 foreign_keys）→ PRAGMA foreign_keys=ON+级联/审计
- 附件错误吞掉（server.py:1355 pass；batch 非事务）→ 逐项错误+trace_id；事务/outbox
- 敏感检测规则有限（sensitive.py:52 正则为主）→ 真实格式测试集+误报/漏报回归
- InfoStore 无租户边界/并发（info_store.py:43 共享域库）→ workspace/session 维度或明确全局+单独授权+WAL
- 并发竞态（max_active_tasks 先查后写；topic MAX(seq)+1）→ 事务内原子计数+唯一约束

## 🟡 低优先级（3）
- 日志/可测试性（print/裸 except/无 trace_id；紧耦合）→ 结构化 logger+可注入组件+测试
- **注入日志存未脱敏全文**（server.py:3004 full；夜间摘要记忆原文进 LLM prompt）→ 脱敏+边界（**我们最近加的 full——重点**）
- API 双入口（旧 /v1/* 与 v2 同改 documents；配置 key 任意写）→ 收敛 domain API+schema allowlist

## 附：codex 记录
- 模型 codex-auto-review（uuapi）/372K tokens/exit 0
