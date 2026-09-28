# `preset-017/` — DSH 0.1.7 preset 声明式迁移 **参考产物**（只读快照）

本目录存放 2026-09-28 在**测试机**上完成「deepmemory 四个 agent preset 从 0.1.5
目录扫描格式 → 0.1.7 声明式注册」迁移后的**部署件完整快照**，仅作**参考/取证**用。

完整迁移说明见 [`../../docs/preset-017-migration.md`](../../docs/preset-017-migration.md)；
生成器见 [`../gen-preset-017.py`](../gen-preset-017.py)。

---

## 1. 文件

| 文件 | 说明 |
|---|---|
| `cordis.patch.yml.reference` | 测试机 `DSH_HOME/profiles/web/cordis.patch.yml` 的**逐字节完整快照**，**1099 行 / 56162 字节** |

来源路径：

```
/www/dsh-test-home/profiles/web/cordis.patch.yml
```

| 指纹 | 值 |
|---|---|
| 行数 | `1099` |
| 字节数 | `56162` |
| md5 | `611dae6f77a781ccb3847afbf34b731b` |
| sha256 | `00eaefc3d250ca867234d49479a86ea409062d432416c29e8e1d575ba330706f` |
| 前 865 行 sha256 | `f2f62820511eb586812e941dc1e9388df51d4dee552213941ce2fe97188cd834` |

> 前 865 行 sha256 是「追加 daily/blank 两块之前」的边界校验值：迁移最后一步**只追加**、
> 既有内容逐字节未动（与备份 `.bak-017adapt-dailyblank-20260928-221949` 一致）。

---

## 2. 对应的 core 版本与环境

| 项 | 值 |
|---|---|
| core | `/opt/dsh-rc2-test-core` = **DSH `0.1.7-rc.2`** |
| 服务 | `dsh-test.service`，端口 **:3091**（**测试机**） |
| `DSH_HOME` | `/www/dsh-test-home` |
| 迁移完成时间 | 2026-09-28 22:22 |
| 实测结果 | roster **8 条**（4 条 0.1.7 内置 + 4 条本次迁移）、建会话回显 preset、journal `[deepmemory] ready` ×3、真实回合注入 `[长期记忆召回]`、`[blank-template] plugin loaded` |

---

## 3. ⚠️ 不能直接拷到生产

**绝对不要把本文件复制到生产环境。** 三个理由，任一都足以致命：

1. **core 版本不匹配**：生产是 `/opt/dsh-rc2-core` = **`0.1.5-rc.1`**（`:3081`，
   `DSH_HOME=/www/dsh/home`）。0.1.5 用的是 `@deepseek-ai/dsh-agent-presets`（**复数**）
   **目录扫描**模型，它**不认识** `@deepseek-ai/dsh-agent-preset`（单数）+ `EntryGroup`
   这种声明式行。把含声明式 preset 行的 patch 放进 0.1.5，轻则该行被当作未知包解析失败，
   重则 include/patch 级致命错误导致 **dsh-web 崩溃循环**（参见仓库 `AGENTS.md` 的
   「桌宠/挂件事故」——同样是 patch 里一个条目引发 include 级致命错误）。
2. **路径全部硬编码测试机**：文件里所有本地插件 specifier 都是
   `/www/dsh-test-home/.agent-presets/...` 开头的**绝对路径**（0.1.7 要求，见下）。
   生产前缀是 `/www/dsh/home/.agent-presets/...`，直接拷过去**全部 404**。
3. **本机专属的 provider/工具配置混在一起**：第 1–256 行是**与 preset 迁移无关**的既有
   条目（`web-search-anysearch`、`reasoning-guard`、provider/model 路由等），它们反映的是
   测试机的状态，不是生产的。

生产的 0.1.7 升级必须走**同一套流程重跑**（生成 → 追加 → 重启 → 四项验证），而不是拷贝本文件。
清单见 `docs/preset-017-migration.md` 的「生产升级清单」一节。

---

## 4. 文件结构导读

```
行 1   – 256   ← 既有 profile patch（与本次 preset 迁移无关）
行 4   – 8     ← `- insert: web-search-anysearch`（既有）
行 10  – ...   ← `- insert: reasoning-guard`（既有）
行 ~20 – 256   ← provider / model 路由等既有配置

行 257 – 543   ← ① preset harness-memory-task   （order 50，完整记忆 + 任务看板/子agent/workflow/文献）
行 544         ← 空行分隔
行 545 – 865   ← ② preset harness-memory        （order 50，完整记忆，标准编码 Agent）
行 866         ← 空行分隔
行 867 – 994   ← ③ preset harness-memory-daily  （order 51，完整记忆但 preset_mode: daily）
行 995         ← 空行分隔
行 996 – 1099  ← ④ preset harness-memory-blank  （order 52，无记忆：persona + instructions + 空模板桩）
```

> 每个块以 5 行注释开头（`# ── agent preset <id> (DSH 0.1.7 declarative form) ──` …），
> 注释里记录了生成器路径与源 `agent.cordis.yml` 绝对路径。

四个块的**声明骨架**完全一致：

```yaml
- insert:
    - id: preset-<preset-id>
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: <preset-id>
        name: '<中文名>'
        description: '<中文描述>'
        order: <50|51|52>
        plugins:            # ← 原 0.1.5 agent.cordis.yml 的顶层清单，整体缩进 10 空格
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            ...
```

### 关键点：本地插件一律绝对路径

三个带记忆的 preset 都指向**同一份**共享插件：

```yaml
          - id: harness-memory
            name: '/www/dsh-test-home/.agent-presets/_memory-plugin/plugin-v3.js'
```

共 3 处（行 511 / 858 / 972）。**为什么必须绝对路径**：0.1.7 下 preset 子插件行的
`name` 以「**声明行所在的 profile 目录**」为 baseUrl 解析，而不是 preset 目录 ——
原 0.1.5 写法 `../_memory-plugin/plugin-v3.js` 会解析成
`profiles/web/../_memory-plugin/...` → 404。

---

## 5. 安全说明

- 本文件**不含任何凭据字面值**：只有 **环境变量名**（如 `apiKeyEnv: UUAPI_API_KEY`）
  与 provider 的 `baseURL`（`https://uuapi.shop/v1`、`https://api.siliconflow.cn/v1`、
  `https://open.bigmodel.cn/api/v1`）。**没有任何 token / key / password 值**。
- 即便如此，本文件仍是**内网部署快照**，包含内网服务端口（memory-server `:6230`、
  literature kb-server `:6262`）与本地绝对路径。**请勿外发/引用到公开场合**。
  本仓库 GitHub 端为公开仓库，若后续需要收紧，应把本文件移出公开镜像。

---

## 6. 溯源校验（可复现）

```bash
# 与测试机当前部署件比对（应完全一致；若测试机后续又改过，则此处会 diff）
diff -u "/www/dsh-test-home/profiles/web/cordis.patch.yml" \
        tools/preset-017/cordis.patch.yml.reference && echo IDENTICAL

# 用生成器重放 daily 块，验证产物确实由 tools/gen-preset-017.py 生成（可复现）
python3 tools/gen-preset-017.py \
    --preset-dir /www/dsh-test-home/.agent-presets/harness-memory-daily \
    --id harness-memory-daily --order 51 \
    --name '日常问答模式' \
    --description '轻量问答、复习、求教、日常状态卡和短期记忆，不包含任务看板、子 agent、workflow。' \
    --out /tmp/reg-daily.yml
sed -n '867,994p' tools/preset-017/cordis.patch.yml.reference > /tmp/ref-daily.yml
diff /tmp/ref-daily.yml /tmp/reg-daily.yml && echo "REPRODUCED ✅"   # 仅差块间空行
```

> 2026-09-28 入库时已实测：重放结果与快照中的 daily 块**逐字节一致**（只差块前的空行分隔符）。
