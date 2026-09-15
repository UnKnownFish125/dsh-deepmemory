# S05 决策：P1（断言事件机制）是否上生产

**日期**：2026-09-16（分析窗口 2026-09-15 23:5x ~ 2026-09-16 00:0x）
**性质**：**只读分析**。未修改任何既有文件、未重启任何服务、未写任何数据库。唯一新增文件 = 本文档。
**分析对象**：

| 角色 | 路径 | 版本 | md5 |
|---|---|---|---|
| 生产 | `/www/deepmemory-v063-deploy/memory-server/server.py` | 3672 行，**无 P1** | `481c10ec17d9c367421f2d1f0601043c` |
| 仓库 | `/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py` | 4005 行，**含 P1+S06/S07** | `70346164d319d63b09fb8772ecae51d3` |
| 测试机 | `/www/dsh-test-memory/server.py` | 4005 行，与仓库**逐字节相同** | `70346164d319d63b09fb8772ecae51d3` |
| 归档 clone | `/www/deepseek harness workspace/harness-memory-archive/memory-server/server.py` | 同上 | `70346164d319d63b09fb8772ecae51d3` |

辅助模块 `v2_domain.py` / `sensitive.py` / `info_store.py` 两侧**逐字节相同**（`diff -q` 无输出）。

---

## ① 结论与建议

### 结论：**有条件上**（迁移本身低风险，真正的决策点是新暴露面）

| 维度 | 判定 | 依据 |
|---|---|---|
| Schema 迁移风险 | **低** | 只加 1 张空表 + 2 个索引；在**真实生产数据的 RAM 副本**上实测 5 ms、零行变更、二次运行幂等（§③ 实测 A） |
| 数据丢失风险 | **极低** | 迁移语句全是 `CREATE ... IF NOT EXISTS`，结构上不可能改动行 |
| 启动失败风险 | **低** | 100 万级 DB 上加空表 <10 ms；失败会让服务起不来（§③ 第 4 点），因此切换前必须先备份 |
| 回滚风险 | **极低** | 已实测「无 P1 的生产代码跑在 v10 库上」启动成功、数据不变（§③ 实测 C） |
| 新暴露面风险 | **中** | P1 首次给生产带来 3 个**写端点**；经 dsh-web 的 `/mem-api/*` 泛代理可达，可改断言 status → 影响 `mode=current` 召回门禁（§⑦-2/3） |
| 功能收益 | **今天为零** | 没有任何消费方调用 `/v1/assertions`（§⑥ grep 证据） |

### 建议

**上**，但必须先满足 4 个条件：

- **C1（测试机 live E2E，强制）**：在测试机 6240 上做一次真实 HTTP 端到端写入验证（当前测试库 `assertion_events` = 0 行，说明该链路从未被真实调用过，只有路由存在性证据 + 我的内存态状态机验证）。
- **C2（记录/收敛暴露面）**：明确接受「持 bearer token 或经 `/mem-api` 的任何调用方可以 promote/revoke 任意断言」。若不能接受，先给 `/mem-api` 加路径白名单（属 dsh-web 改动，需遵守红线 2：会话运行中禁止重启 dsh-web）。
- **C3（强制）**：上线前为**当前生产 server.py 新建文件备份**——实测确认磁盘上**不存在**与 `481c10ec…` 匹配的副本（§④ 步骤 2）。
- **C4（收尾）**：部署后更新 `/www/scripts/verify_deepmemory_copies.py` 的副本分组与标记表（它现在硬编码「生产·无P1」），否则验收工具会报假不一致。

**收益定位**：上 P1 的收益是**消除 S05 正向代码漂移**（生产 = 测试机 = 两个 clone，单版本可比对），不是新功能。风险等级：**迁移低 / 端点中 → 满足条件后整体低**。

---

## ② P1 增量清单（表 / 函数 / 端点 + 行号）

`diff -u 生产 仓库` 结果：**9 个 hunk，+334 行 / −1 行**（唯一的删除行是 `llm_chat` 文档字符串，见下表最后一行）。即仓库版是生产版的**严格超集**——部署不会删掉生产的任何行为。

### 2.1 新增表（1 张 + 2 索引）

`run_migrations()` 内 `migrations` 字典新增 **key 10**（仓库 `server.py:548-568`）：

```sql
CREATE TABLE IF NOT EXISTS assertion_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assertion_id INT NOT NULL REFERENCES assertions(id),
  kind TEXT NOT NULL CHECK(kind IN ('support','contradict','confirm','promote','revoke')),
  actor TEXT NOT NULL, origin_id TEXT NOT NULL,
  source_id INT REFERENCES sources(id), target_event_id INTEGER,
  note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  UNIQUE(assertion_id, kind, origin_id)
);
CREATE INDEX IF NOT EXISTS idx_assertion_events_assertion ON assertion_events(assertion_id);
CREATE INDEX IF NOT EXISTS idx_assertion_events_kind      ON assertion_events(kind);
```

`SCHEMA`（新建库用的全量 DDL）**没有**加这张表——P1 表只走迁移路径（生产是既有库，因此不影响；新建库会在 `run_migrations()` 里补上）。

### 2.2 新增函数

| 函数 | 仓库行号 | 生产是否存在 | 作用 |
|---|---|---|---|
| `_purge_orphan_assertion_events` | 624-641 | ❌ | **S06 修复**：启动时幂等清理 `assertion_id` 已不存在的孤儿事件 |
| `_resolve_server_actor` | 747-753 | ❌ | 身份服务端解析：actor 恒为 `SYSTEM_ACTOR_ID`（忽略 body `user_id`/`confirmed`） |
| `_resolve_event_origin` | 756-766 | ❌ | origin 解析：`body.origin_id` 作为**独立来源鉴别器**（非权限凭证），缺省回落服务端主体 |
| `_count_confirm_origins_on` | 769-776 | ❌ | 事务内 `COUNT(DISTINCT origin_id) WHERE kind='confirm'` |
| `_confirm_origin_count` | 779-790 | ❌ | 已提交视角的独立确认数 |
| `_sync_rule_projection` | 793-815 | ❌ | adopted 的 rule 类断言 → `documents.rule_crystallized=1` 投影 |
| `_record_assertion_event` | 818-917 | ❌ | **P1 状态机**：`support/contradict/confirm/revoke`；≥2 不同 origin 的 confirm → 自动 `adopted`；S07 `BEGIN IMMEDIATE` + 状态 CAS |
| `_promote_assertion` | 920-975 | ❌ | 显式晋升（门禁：≥2 独立确认；revoked/superseded 不复活；幂等） |

### 2.3 存量函数改动

| 位置 | 仓库行号 | 改动 |
|---|---|---|
| 模块 docstring | 26-28 | +3 行：登记 3 个新端点 |
| `run_migrations()` | 610-613 | 迁移后调用 `_purge_orphan_assertion_events(conn)`（S06） |
| `delete_memory()` | 1892-1900 | **S06(a)**：删断言前先 `DELETE FROM assertion_events WHERE assertion_id IN (SELECT id FROM assertions WHERE memory_id=?)`（该函数在 FK 关闭窗口内删数据，不显式删事件会留孤儿） |
| `llm_chat()` | 1153-1154 / 1170 | 唯一被改写的既有行：文档字符串对齐生产运行态（S05 反向漂移已消除）；**逻辑无变化** |

### 2.4 新增 HTTP 端点（全部在 `do_POST`，`server.py:3849-3873`）

| 方法 + 路径 | 行号 | 行为 |
|---|---|---|
| `POST /v1/assertions/<id>/events` | 3855-3862 | body `{kind: support\|contradict\|confirm\|revoke, origin_id?, note?}`；kind 非法 → 400；断言不存在 → 404 |
| `POST /v1/assertions/<id>/promote` | 3863-3867 | 服务端身份 + 已 ≥2 独立确认 → `adopted`；不足 2 条 → 400（`ValueError` 被 `do_POST` 的 `except (ValueError, json.JSONDecodeError)` 映射为 400，`server.py:3897`） |
| `POST /v1/assertions/<id>/revoke` | 3868-3872 | `status → revoked` + 事件（`target_event_id` 指向最近 confirm/promote） |

**明确不存在**：`GET /v1/assertions`（列表/详情）、独立的断言鉴权、任何 UI/插件入口。生产侧 P0 只在内部创建断言（`_create_assertion_for_memory` 1604、`_assertion_current_allowed_batch` 707 门禁），**没有**任何 `/v1/assertions` 路由（`grep assertions` 在生产 server.py 里只命中表 DDL 与内部 SQL）。

---

## ③ schema 迁移分析（含实测证据）

### 3.1 迁移机制（两侧相同，除 key 10）

```
main() (server.py:3987)
 └─ _ensure_api_token()
 └─ init_db()                       # 666: executescript(SCHEMA) → run_migrations() → install_v2_schema()（try/except pass，673-680）
     └─ run_migrations()            # 481: 按 schema_version 表逐版本应用 migrations{2..N}，只有 current < version 才跑
         └─ INSERT OR IGNORE INTO schema_version(version) # 每版本一行
        install_v2_schema(conn)      # 605，每次都跑（v2_domain.py，两侧同一文件，幂等 DDL + _ensure_columns）
        _migrate_legacy_disputed_assertions(conn)   # 609，P0 存量标记，幂等
        _purge_orphan_assertion_events(conn)        # 613，P1 新增（S06）
 └─ get_index()                     # 加载 data/memory.faiss
 └─ rebuild_bm25()
 └─ ThreadingHTTPServer(("localhost", PORT)).serve_forever()
```

生产库实测状态（只读查询）：`schema_version` 含 **1..9**，`max(version)=9`；`data/memory.db` = 152 MB，`documents` 11305 / `assertions` 10828（`created_at` 均在增长，服务在写）。

### 3.2 首次运行 P1 会发生什么：**纯加表，5 毫秒，零行变更**

版本 10 的 3 条语句**全部**是 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`，结构上不含 `ALTER`（无加列/改列）、不含 `UPDATE`/`INSERT ... SELECT`（无回填）、不含 `DROP`/`RENAME`。

**实测 A（真实生产数据的 RAM 副本，零落盘）**
把生产 `memory.db` 用 sqlite3 **在线备份 API** 复制进 `file:…?mode=memory&cache=shared`，再用**从两个 server.py 中 AST 抽取的真实 `run_migrations()` 源码**分别执行：

```
prod DB copied to RAM in 0.1s, size=152.1 MB
before: schema_version max = 9 | tables = 21 | documents = 11236 | assertions = 10759
migration: schema v10 已应用
>>> run_migrations() #1 took 0.005 s
after#1: schema_version max = 10 | tables = 22
DATA UNCHANGED (docs+assertions): True
table count diffs: {'assertion_events': (None, 0), 'schema_version': (9, 10)}
assertion_events exists: True rows = 0
assertion_events indexes: ['idx_assertion_events_assertion', 'idx_assertion_events_kind']
>>> run_migrations() #2 (idempotency) took 0.003 s
after#2 identical to after#1: True | docs sum same: True
DOC SUM before/after: (11236, 63461729, 722587) → 完全相同
ASSERTIONS SUM before/after: (10759, 57883420, 708251) → 完全相同
```

→ 只增加 1 张 **0 行**的表 + 2 个索引，`schema_version` 9→10；`documents`/`assertions` 的行数、id 之和、内容长度之和**逐位相同**；重复运行完全幂等。

**实测 B（测试机 = 生产库的真实副本，含真机迁移痕迹）**
测试机 `data/memory.db` 与生产库的 `schema_version` 行 **1..9 的 `applied_at` 时间戳逐位相同**（`1786761787.8986626` … `1788602018.1316907`）→ 确证是生产库的文件级拷贝；其上已存在 `version 10`（`applied_at=1789463551.0012217`），`tables=23`（含 `assertion_events`），而 `documents=10643 / assertions=10186` **一行不少**，`assertion_events=0` 行。

→ 这是「在生产规模的库上真机跑过一次 P1 迁移且数据完好」的直接证据。

### 3.3 有没有一次性全量重建（重新 embedding / 重建 FAISS）的风险：**没有**

1. P1 的迁移语句不涉及向量列/索引文件。
2. 启动时的 `get_index()`（`server.py:995-1021`）只在「索引文件缺失」「维度不符」「模型指纹不符」三种情况下才 `_rebuild_indexes_internal()`（分钟级全量重建）。两侧 `data/dim.json` **均为** `{"dim": 1024, "fp": "api:bge-m3"}`，与当前配置一致 → 走「直接 `faiss.read_index`」分支，**不重建**。
3. 代码里没有任何「因 schema 版本变化而重新 embedding」的路径。
4. 本次重启不做数据迁移，`data/migrate-embedding/` 下 2 份模型级备份与本次无关。

**唯一与「重启耗时」相关的注意点**：`main()` 会 `rebuild_bm25()`（对存量语料重建 BM25，秒级）并在启动时加载 45 MB 的 FAISS；实测该服务日常重启是秒级，与本次 P1 无关。

### 3.4 迁移失败会不会阻止服务启动：**会**

`run_migrations()` 的异常**不被吞**（`init_db()` 里只有 `install_v2_schema` 那一次调用包在 `try/except: pass`，`server.py:673-680`；`run_migrations` **在它之前**执行且无 catch）。异常会沿 `main()` 抛出 → 进程在 `ThreadingHTTPServer(...)` **bind 之前**退出 → systemd `Restart=always` + `StartLimitIntervalSec=300` / `StartLimitBurst=5` → 5 次快速失败后 unit 进入 **failed**，记忆服务整体不可用（影响所有会话的记忆注入/检索）。

失败面与缓解：

| 失败面 | 触发条件 | 缓解 |
|---|---|---|
| `database is locked` / `SQLITE_BUSY` | 切换时旧进程仍持有库；或切换瞬间有长事务 | 用 `systemctl stop` → 换文件 → `start`，不要用 `restart` 抢文件（本服务连接是「每操作一条短连接」，空闲时无锁，风险本来就低） |
| 磁盘满 / 文件系统只读 | 加空表几乎不需要空间（本次 5 ms、0 行） | 上线前确认 `df` 余量 |
| 其它 `sqlite3.OperationalError` | 罕见（IF NOT EXISTS 不会撞已有对象） | 观察 `journalctl` 是否打印 `migration: schema v10 已应用`；未打印即失败 |

失败后**不需要恢复数据**（迁移不写数据行）：换回旧文件重启即可回到原状（§⑤）。

### 3.5 是否可逆 / 备份机制

- **迁移本身**：表建了就留在库里（SQLite 无「降级脚本」），但**对旧代码完全无害**——实测 C（无 P1 的生产代码跑在 v10 库上）：

```
prod(无P1) migrations keys: [2..9] | repo(P1): [2..10]
P1 升到 v10: max(version)= 10 | assertion_events= 0
降级（生产代码跑 v10 库）: 成功=True
数据 before=(11275, 10798) after=(11275, 10798) 不变=True
max(version)= 10 | 残留 assertion_events 表: True
```

（旧代码 `run_migrations` 看到 `max=10 > 所有 key` → 不执行任何迁移；`INSERT OR IGNORE` 的 `V2_SCHEMA_VERSION=5` 早已存在 → 无操作。多出来的空表 inert。）

- **数据备份**：`create_backup()`（`server.py:2633-2666`）= SQLite **在线备份 API** + FAISS 文件复制 + `manifest.json`（含 active 计数与 max_id 指纹），在 `_index_lock` 内一次完成（S17），**不需要停服务**；由 `POST /v1/backups/create`（`server.py:3650-3651`）触发；`GET /v1/backups/list`（3474）列出；`restore_backup`（2708）。
- 现有备份：`data/backups/` **11 份**（5 个 `backup-2026…` 目录 + 6 个早期 `*-memory.db/faiss` 文件，最新一份是 2026-09-03），另有 `data/migrate-embedding/20260915-163418|171315/`（`dim.json`+`memory.faiss` 备份）与 `data/memory.db.bak-*` 若干历史副本。
- ⚠️ **关键缺口**：**当前生产 `server.py`（md5 `481c10ec…`）在磁盘上没有任何 `.bak` 副本**——已对 `/www` 下全部 `server.py*` 做 md5 比对，无匹配。`data/backups/` 备份的是**数据库**，不含代码。所以 §④ 步骤 2 的代码备份是强制项，不能省。

---

## ④ 上生产步骤（可执行清单）

约定：`$D=/www/deepmemory-v063-deploy/memory-server`，`$R="/www/deepseek harness workspace/dsh-deepmemory/memory-server"`。
**不涉及 dsh-web 重启**（memory-server 是独立 systemd 单元，插件经 HTTP 6230 访问；`dsh-deepmemory` Host 插件的 `/mem-api` 代理同样只是转发）。
遵守 `dsh-deepmemory/AGENTS.md` 的**部署两阶段铁律**：测试机先行。

### 阶段 0：前置检查（只读，1 分钟）

```bash
# 0.1 生产当前基线（记录到变更记录里，供回滚比对）
md5sum "$D/server.py"                              # 期望 481c10ec17d9c367421f2d1f0601043c
wc -l "$D/server.py"                               # 期望 3672
md5sum "$R/server.py" /www/dsh-test-memory/server.py   # 期望两者都是 70346164d319d63b09fb8772ecae51d3
systemctl is-active dsh-memory-server dsh-test-memory
df -h /www | tail -1

# 0.2 生产库现状（只读）
/opt/AstrBot/venv/bin/python3 -c "
import sqlite3;c=sqlite3.connect('file:'+'$D/data/memory.db'+'?mode=ro',uri=True)
print('schema_version max =',c.execute('select max(version) from schema_version').fetchone()[0])
print('documents =',c.execute('select count(*) from documents').fetchone()[0])
print('assertions =',c.execute('select count(*) from assertions').fetchone()[0])
print('assertion_events 存在 =',bool(c.execute(\"select 1 from sqlite_master where name='assertion_events'\").fetchone()))"
# 期望：max=9 / documents≈11300 / assertions≈10830 / assertion_events 存在=False
```

### 阶段 1：测试机（6240）先行验证 —— **C1 强制**

测试机已经在跑**同一个 md5** 的 server.py，且库是生产库副本、已是 v10。要做的是**真实端到端写入验证**（当前 `assertion_events`=0，说明从未真实跑过）：

```bash
T=$(cat /www/dsh-test-memory/data/api-token)   # 不在终端回显 token

# 1.1 造一条一次性断言（测试库，挑一个真实 memory_id）
/opt/AstrBot/venv/bin/python3 - <<'EOF'
import sqlite3,time
c=sqlite3.connect("/www/dsh-test-memory/data/memory.db"); c.row_factory=sqlite3.Row
mid=c.execute("select id from documents order by id desc limit 1").fetchone()["id"]
aid=c.execute("insert into assertions (memory_id,subject_id,predicate,value_json,recorded_at,status)"
              " values (?,?,?,?,?,'unverified')",(mid,"s05-e2e","is_verified","{\"v\":true}",time.time())).lastrowid
c.commit(); print(aid); open("/tmp/s05_aid","w").write(str(aid))
EOF
AID=$(cat /tmp/s05_aid)

# 1.2 状态机（期望依次：adopted=false → duplicate → adopted=true）
curl -s -X POST http://127.0.0.1:6240/v1/assertions/$AID/events -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"kind":"confirm","origin_id":"e2e-A"}'; echo
curl -s -X POST http://127.0.0.1:6240/v1/assertions/$AID/events -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"kind":"confirm","origin_id":"e2e-A"}'; echo   # duplicate=true
curl -s -X POST http://127.0.0.1:6240/v1/assertions/$AID/events -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"kind":"confirm","origin_id":"e2e-B"}'; echo   # adopted=true

# 1.3 revoke → 再 confirm 必须 400（S07 防复活）
curl -s -X POST http://127.0.0.1:6240/v1/assertions/$AID/revoke -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{}'; echo
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6240/v1/assertions/$AID/events -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"kind":"confirm","origin_id":"e2e-C"}'   # 期望 400

# 1.4 清理一次性断言（P1 的 delete_memory 会连带删事件；也可直接 SQL 删测试行）
/opt/AstrBot/venv/bin/python3 -c "
import sqlite3;c=sqlite3.connect('/www/dsh-test-memory/data/memory.db')
c.execute('delete from assertion_events where assertion_id=?',($AID,))
c.execute('delete from assertions where id=?',($AID,)); c.commit()"

# 1.5 回归：检索与 401/404 语义不受影响
curl -s -X POST http://127.0.0.1:6240/v1/memories/search -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"query":"记忆","k":2}' | head -c 200; echo
```

**测试机全过才允许碰生产。** 任一失败 → 停止，不得继续。

### 阶段 2：生产备份（先备份，后切换）

```bash
# 2.1 数据库在线备份（不停服务；期望返回 {"name":"backup-YYYYmmdd-HHMMSS","documents":N}）
P=$(cat "$D/data/api-token")
curl -s -X POST http://127.0.0.1:6230/v1/backups/create -H "Authorization: Bearer $P"; echo
ls -la "$D/data/backups/" | tail -3
cat "$D/data/backups/backup-<新时间戳>/manifest.json"

# 2.2 ★ 代码备份（当前生产文件没有任何副本，必须新建）
cp -p "$D/server.py" "$D/server.py.bak-pre-p1-$(date +%Y%m%d-%H%M%S)"
md5sum "$D"/server.py.bak-pre-p1-*        # 必须等于 481c10ec17d9c367421f2d1f0601043c

# 2.3 附带备份 FAISS/dim（可选，双保险；1 MB 级）
cp -p "$D/data/memory.faiss" "$D/data/memory.faiss.bak-pre-p1-$(date +%Y%m%d-%H%M%S)"
cp -p "$D/data/dim.json"     "$D/data/dim.json.bak-pre-p1-$(date +%Y%m%d-%H%M%S)"
```

### 阶段 3：切换 + 重启（几秒）

```bash
systemctl stop dsh-memory-server                      # 先停，避免换文件时旧进程持锁
install -m 644 -o root -g root "$R/server.py" "$D/server.py"    # 保持原权限 root:root 644
md5sum "$D/server.py"                                 # 期望 70346164d319d63b09fb8772ecae51d3
systemctl start dsh-memory-server
sleep 3
systemctl is-active dsh-memory-server && ss -ltnp | grep 6230
```

### 阶段 4：验证（全部只读，**不写生产库**）

```bash
# 4.1 启动日志必须出现迁移行
journalctl -u dsh-memory-server --since '3 min ago' | grep -E 'migration|listening|Traceback|Error'
# 期望：migration: schema v10 已应用 / memory-server listening on http://localhost:6230 / 无 Traceback

# 4.2 库侧：v10 + 空表 + 数据量不减
/opt/AstrBot/venv/bin/python3 -c "
import sqlite3;c=sqlite3.connect('file:'+'$D/data/memory.db'+'?mode=ro',uri=True)
print('schema_version max =',c.execute('select max(version) from schema_version').fetchone()[0])      # 10
print('assertion_events 行数 =',c.execute('select count(*) from assertion_events').fetchone()[0])      # 0
print('documents =',c.execute('select count(*) from documents').fetchone()[0])                          # ≥ 阶段 0.2 的值
print('assertions =',c.execute('select count(*) from assertions').fetchone()[0])                        # ≥ 阶段 0.2 的值
print('索引 =',[r[0] for r in c.execute(\"select name from sqlite_master where type='index' and tbl_name='assertion_events' and name not like 'sqlite_%'\")])"

# 4.3 端点存在性（**零写入**：非法 kind 在触碰 DB 之前就被拒绝）
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6230/v1/assertions/1/events \
  -H "Authorization: Bearer $P" -H 'content-type: application/json' -d '{"kind":"bogus"}'   # 期望 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6230/v1/assertions/99999999/promote \
  -H "Authorization: Bearer $P" -H 'content-type: application/json' -d '{}'                 # 期望 404

# 4.4 检索回归（真实业务路径）
curl -s -X POST http://127.0.0.1:6230/v1/memories/search -H "Authorization: Bearer $P" \
  -H 'content-type: application/json' -d '{"query":"记忆","k":3}' | head -c 300; echo
cat "$D/data/dim.json"        # 期望 {"dim": 1024, "fp": "api:bge-m3"}（未变）
systemctl is-active dsh-memory-server

# 4.5 一键验收（★ 须先完成 C4：脚本里「生产·无P1」的分组/标记要改成含 P1）
/opt/AstrBot/venv/bin/python3 /www/scripts/verify_deepmemory_copies.py
```

### 阶段 5：收尾（C4）

- 更新 `/www/scripts/verify_deepmemory_copies.py`：
  - `GROUPS` 里把生产 server.py 从 `"memory-server.py(生产·无P1)"` 合并进 P1 组（`/www/scripts/verify_deepmemory_copies.py:34-42`）→ 四份 server.py md5 应完全一致；
  - `MARKERS` 的 `"memory-server.py(生产·无P1)"` 条目（`:55-58`）改为 P1 组，并可追加 `"S06"`/`"S07"` 标记计数（切换后生产文件 = 仓库文件，实测 `grep -c S06` = **3**、`grep -c S07` = **4**；注意脚本的标记表按「出现次数」比对，`N01：` 这类带全角冒号的短前缀写法要沿用）。
- 在 `docs/deepmemory-fix-delivery.md` 里把「S05 正向（P1 上生产）待决策」改为已上线，并记录本次 md5 与 `schema_version=10`。
- 更新 `harness-memory-archive` clone（已含 P1，无需动作；仅确认 md5 未变）。

---

## ⑤ 回滚方案

**回滚成本极低（几秒），且不需要恢复数据库。**

### 5.1 代码回滚（首选）

```bash
D=/www/deepmemory-v063-deploy/memory-server
systemctl stop dsh-memory-server
cp -p "$D"/server.py.bak-pre-p1-<时间戳> "$D/server.py"     # 阶段 2.2 的备份
md5sum "$D/server.py"                                        # 必须回到 481c10ec17d9c367421f2d1f0601043c
systemctl start dsh-memory-server
sleep 3 && systemctl is-active dsh-memory-server
journalctl -u dsh-memory-server --since '2 min ago' | grep -E 'listening|Traceback'
curl -s -X POST http://127.0.0.1:6230/v1/memories/search -H "Authorization: Bearer $(cat $D/data/api-token)" \
  -H 'content-type: application/json' -d '{"query":"记忆","k":2}' | head -c 120; echo
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6230/v1/assertions/1/events \
  -H "Authorization: Bearer $(cat $D/data/api-token)" -H 'content-type: application/json' -d '{"kind":"bogus"}'   # 期望回到 404
```

**为什么不需要回退 schema**：实测 C 已证明无 P1 的旧代码能正常跑在 `schema_version=10` 的库上（`max=10` 超过所有迁移 key → 不执行迁移；`assertion_events` 空表对旧代码不可见）。唯一残留是 `schema_version` 里多一行 `10` 和一张空表——**不影响任何行为**。若坚持回到 v9 状态，可在停服后手工执行（**非必需、有风险，默认不做**）：

```sql
-- 仅当确有必要时；先做库备份
DROP INDEX IF EXISTS idx_assertion_events_kind;
DROP INDEX IF EXISTS idx_assertion_events_assertion;
DROP TABLE IF EXISTS assertion_events;
DELETE FROM schema_version WHERE version = 10;
```

### 5.2 数据回滚（一般不需要）

P1 迁移不改数据行；只有**真实调用过写端点**后才可能产生数据变化（`assertion_events` 行、`assertions.status`、`documents.rule_crystallized`）。若确需回退到某个时间点：

```bash
# 查看可用备份
curl -s http://127.0.0.1:6230/v1/backups/list -H "Authorization: Bearer $(cat $D/data/api-token)" | head -c 400
# 恢复（会覆盖 data/memory.db；需停服务，恢复后把配套 memory.faiss 一起还原）
systemctl stop dsh-memory-server
ls -la "$D/data/backups/backup-<时间戳>/"          # 内含 memory.db / memory.faiss / manifest.json
cp -p "$D/data/backups/backup-<时间戳>/memory.db"   "$D/data/memory.db"
cp -p "$D/data/backups/backup-<时间戳>/memory.faiss" "$D/data/memory.faiss"
systemctl start dsh-memory-server
```

⚠️ `restore_backup` 路径从未被演练过（§⑦-6）：若走这条路，建议**先在测试机上演练一次**再对生产使用。

---

## ⑥ 不上生产的影响

### 6.1 功能层面：**今天为零**

| P1 能力 | 消费方 | 不上线后果 |
|---|---|---|
| `POST /v1/assertions/<id>/events`（support/contradict/confirm/revoke） | **无** | 无功能缺失 |
| `POST /v1/assertions/<id>/promote` | **无** | 无功能缺失 |
| ≥2 独立 origin 自动 `adopted` 状态机 | **无** | 生产全部断言停留在 `unverified`（分析窗口内多次实测 `select status,count(*) from assertions group by status` → 只有一行 `unverified`，10705→10840 随服务写入增长，无其它状态） |
| `_sync_rule_projection`（adopted → `documents.rule_crystallized`） | **无** | 规则固化仍走既有 E2/`rule_candidates` 通道，不受影响 |
| P2 冲突组裁决 | 未实现 | 无影响 |

**grep 证据（两次独立检索）**：

```
$ grep -ric "assertion" /www/dsh/home/.agent-presets/_memory-plugin/     # 全部 0（含 plugin-v3.js 及所有 .bak）
$ grep -ric "assertion" /www/dsh/home/profiles/web/node_modules/dsh-deepmemory/   # 全部 0（index.js / client.js）
$ grep -rl "/v1/assertions" /www --include=*.js --include=*.mjs --include=*.py --include=*.json --include=*.yaml --include=*.md
   → 只命中 memory-server 自身的 server.py（含 archive clone）与会话正文缓存 json（历史对话文本，非代码调用）
```

preset 插件（`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`）实际调用的端点仅：`/v1/config/session`、`/v1/maintenance/decay`、`/v1/memories/add|add_batch|search|injection-log`、`/v1/settings/*`、`/v1/stats`、`/v1/topic-summaries`、`/v1/v2/cards/*`、`/v1/v2/tasks`。**没有** `/v1/assertions`。

### 6.2 工程层面：代价是**持续的代码漂移**（这才是真正的损失）

| 不上线 → 持续存在 | 具体后果 |
|---|---|
| 生产 ≠ 仓库 ≠ 测试机（同一服务两个版本） | 每处改动都要「双版本维护」；`patch_server_*.py` 已需要「对不含目标代码的副本自动跳过」的特例（交付说明 §五） |
| 验收工具长期带特例 | `verify_deepmemory_copies.py` 必须永久保留「生产·无P1」分组与标记表（`:34-42`、`:55-58`），且四份 server.py 的 md5 一致性检查永远无法覆盖生产 |
| 生产无法验证 P1 相关修复 | S06/S07 的修复在生产**永远无法被验证**（没有 P1 就没有这条路径）；P1 若再有缺陷，只能靠测试机 + 手工推理 |
| 后续 P2/规则固化落地成本变大 | 任何依赖 `assertion_events` 的能力（冲突组裁决、规则固化闭环）都需先补 P1，届时「迁移间隔」更大、回归面更广 |
| 决策悬空 | 每轮交付说明都要重复一条「S05 正向待决策」 |

### 6.3 折中方案（若暂时不上）

把生产版正式宣告为「冻结的 P1-free 分支」，并把这 4 个 hunk 抽成幂等补丁脚本（例如 `/www/scripts/patch_server_p1_events.py`），使 P1 可随时以脚本形式落地、且仓库/测试机的后续改动不必再和生产逐字节对齐。代价是仍要维护两版本。

---

## ⑦ 不确定点

1. **测试机 live E2E 未做（C1 的由来）**：测试库 `assertion_events` = 0 行 → P1 的写链路从未被真实 HTTP 调用过。本次分析的状态机结论来自「**从两个 server.py AST 抽取的真实函数源码 + 内存 SQLite**」的 25 项断言（全过），以及**只读**HTTP 探测（`400`/`404`/`401`），**不含**真实写入的端到端验证。生产切换前的最后一次真机确认应在 6240 上完成。
2. **`/mem-api` 是泛代理 → 新暴露面**：`dsh-deepmemory/index.js:717-736` 把 `/mem-api/*` 的**任意 method + 任意路径**转发到 `localhost:6230`，并自动注入 bearer token、去掉浏览器 Origin。P1 上线后 `POST /mem-api/v1/assertions/<id>/{events,promote,revoke}` 从 web 同源可达。若不加限制，任何能访问 DSH Web（cookie 认证）的调用方都能改断言状态。
3. **`origin_id` 由调用方给定 → 「≥2 独立确认」不是强信任边界**：持 token（或经 `/mem-api`）者用两个不同的 `origin_id` 即可让断言**自动 adopted**；反之一次 `revoke` 会让该记忆的断言变 `revoked`，而 `_assertion_current_allowed_batch`（`server.py:707-740`，用于 `search_memories` 的 `mode=current` 门禁，调用点 `1424`）会把**该记忆从 current 召回中剔除**。即：可被用来「静默降低某条记忆的召回」。这是 P1 上生产**新增**的完整性风险（生产现存 10705 条断言全为 `unverified` 且 `conflict_group` 为空，因此当前全部可召回）。
4. **文档/口径不一致**：任务描述写「约 10675 条记忆、11 份备份」，实测 `documents` **11305**（00:01 快照，服务在持续写入；用户口径可能指 `status='active'` 或早先时点），备份确为 **11 份**（5 目录 + 6 文件）。未深究差异口径。
5. **`S05` 反向改动的等价性**：仓库版 `llm_chat` 的 docstring 断言「已与生产运行态对齐」，逻辑行两侧一致（diff 仅 1 行 docstring）——**行为等价已由 diff 证明**，但未实测该夜间批处理路径。
6. **备份恢复（`restore_backup`）从未演练**：`data/backups/` 里的目录结构（含 5 个 `backup-2026…` 目录与 6 个散落的 `*-memory.db/faiss`）与 `list_backups()` 的解析规则（只认带 `manifest.json` 的 `backup-YYYYMMDD-HHMMSS`）不完全一致；恢复路径存在未验证的假设。回滚首选「代码回滚」，不要依赖数据恢复。
7. **未评估**：P1 端点在并发压力下的行为只做了代码级审查（S07 的 `BEGIN IMMEDIATE` + `WHERE status=?` CAS）；未做多线程压测（内存态共享连接无法忠实模拟，需在测试机上做）。

---

## 附录 A：本次使用的只读证据命令（可复现）

```bash
# 代码差异（9 hunks / +334 −1）
diff -u /www/deepmemory-v063-deploy/memory-server/server.py \
        "/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py"
# 副本一致性
md5sum /www/deepmemory-v063-deploy/memory-server/server.py \
       "/www/deepseek harness workspace/dsh-deepmemory/memory-server/server.py" \
       /www/dsh-test-memory/server.py \
       "/www/deepseek harness workspace/harness-memory-archive/memory-server/server.py"
# 端点存在性（零写入探针）
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6240/v1/assertions/1/events \
  -H "Authorization: Bearer $(cat /www/dsh-test-memory/data/api-token)" \
  -H 'content-type: application/json' -d '{"kind":"bogus"}'                      # 测试机 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:6230/v1/assertions/1/events \
  -H "Authorization: Bearer $(cat /www/deepmemory-v063-deploy/memory-server/data/api-token)" \
  -H 'content-type: application/json' -d '{"kind":"bogus"}'                      # 生产 404（无该路由）
# 数据库（一律 mode=ro）
/opt/AstrBot/venv/bin/python3 -c "
import sqlite3
for lbl,p in (('PROD','/www/deepmemory-v063-deploy/memory-server/data/memory.db'),
              ('TEST','/www/dsh-test-memory/data/memory.db')):
    c=sqlite3.connect('file:%s?mode=ro'%p,uri=True)
    print(lbl,[tuple(r) for r in c.execute('select version,applied_at from schema_version order by version')])
    print('  tables',[r[0] for r in c.execute(\"select name from sqlite_master where type='table' order by name\")])"
```

**零写入证明**：全部探针完成后复查测试库 `assertion_events` 仍为 **0** 行、`documents`/`assertions` 计数不变；`sqlite3` 一律以 `file:…?mode=ro` 打开；两个内存库均以 `file:<name>?mode=memory&cache=shared` 创建（不落盘）；`PYTHONDONTWRITEBYTECODE=1` + `sys.dont_write_bytecode=True`（不因 import `v2_domain` 而写 `__pycache__`）。

## 附录 B：内存态 P1 状态机测试结果（25 项断言，全过）

| 组 | 结论 |
|---|---|
| 独立确认计数 | 第 1 条 confirm 不采纳；同 origin 重复 `duplicate=true` 不重新计数；第 2 个不同 origin → `adopted=true`、`confirm_origins=2` |
| 规则投影 | `adopted` 的 preference 文档 `rule_crystallized=1`；非 preference 文档不受影响 |
| revoke | `status=revoked`、`target_event_id` 指向最近 confirm；**revoked 后 confirm/promote 均抛 ValueError（不复活）** |
| promote 门禁 | 仅 1 条 confirm → 拒绝（`>=2`）；2 条 → `adopted`；重复 promote `duplicate=true` |
| S06 | 复现「复用断言 id 继承旧确认计数 = 2」；`_purge_orphan_assertion_events` 清掉 2 条孤儿；清理后计数归零、1 次确认不再被误判 adopted；重复清理幂等；混合场景只删孤儿 |
| 身份/输入 | 断言不存在 → `None`（→404）；非法 kind → ValueError（→400）；空 `origin_id` 回落服务端主体并同源去重；`actor` 恒为 `dsh-user`（伪造 `user_id`/`confirmed`/`actor` 均无效）；`_bump_semantic_gen` 每次成功写入恰好 +1（重复事件不递增） |

> 说明：`_record_assertion_event` / `_promote_assertion` 抛出的 `ValueError` 在 `do_POST` 的 `except (ValueError, json.JSONDecodeError)`（`server.py:3897`）里映射为 **HTTP 400**，因此「确认已撤销断言」「promote 不足 2 条」在线上表现为 400 而非 500；`int(aparts[2])` 的非数字 id 同样落这个分支（实测 `/v1/assertions/abc/events` → 400，非 500）。
