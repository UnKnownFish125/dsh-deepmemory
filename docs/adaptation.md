# deepmemory 适配指南

deepmemory = 记忆后端（memory-server）+ Web 记忆面板（web 插件）+ Agent 记忆能力（preset 插件）。
本指南说明如何把 deepmemory 适配到**新的部署环境**。

## 一、配置中心（适配的核心）

所有环境参数存于 memory-server 的 settings 表（`GET/POST /v1/settings/<key>`），
插件启动时自动读取，缺省用内置默认值。**适配新环境 = 改配置，不改代码。**

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `deepmemory.server_url` | `http://localhost:6230` | 插件连接的记忆后端地址（可改为远程地址） |
| `deepmemory.workspace` | `deepseek-hardness` | 当前工作区标识（绑定 workspace 级记忆） |
| `deepmemory.extract_threshold` | `4` | 累积多少条消息触发一次自动抽取 |
| `deepmemory.recall_k` | `5` | 每轮注入的召回记忆条数 |
| `deepmemory.inject_order` | `50` | systemPrompt 中记忆 section 的排序（persona=0 之后、工具引导=100 之前） |

```bash
# 例：适配新环境（新工作区 + 远程后端）
curl -X POST http://localhost:6230/v1/settings/set -H 'Content-Type: application/json' \
  -d '{"key":"deepmemory.workspace","value":"my-project"}'
curl -X POST http://localhost:6230/v1/settings/set -H 'Content-Type: application/json' \
  -d '{"key":"deepmemory.server_url","value":"http://mem.example.com:6230"}'
```

## 二、组件与挂载点

| 组件 | 路径 | 生效时机 |
|---|---|---|
| 记忆后端 | `memory-server/server.py`（systemd: dsh-memory-server） | 即时（systemctl restart dsh-memory-server，不影响 Web） |
| Web 面板 | `profiles/web/node_modules/dsh-deepmemory/`，`profiles/web/package.json` 的 `dsh.profile.bundles` 加 `"dsh-deepmemory"` | **重启 dsh web** |
| Agent 记忆 | `${DSH_HOME}/.agent-presets/harness-memory/`（含 memory-plugin/） | **新建会话时选该 preset** |

## 三、新环境部署清单

1. 拷贝 `memory-server/`，安装依赖（fastembed/faiss-cpu/jieba/zstandard），
   设置 `HF_ENDPOINT=https://hf-mirror.com` 下载 embedding 模型（bge-small-zh-v1.5）
2. 注册 systemd 服务 `dsh-memory-server.service`（见 memory-server/README.md）
3. 拷贝 `web-plugin/` 到 `profiles/web/node_modules/dsh-deepmemory/`，bundles 加行
4. 拷贝 `agent-preset/` 到 `${DSH_HOME}/.agent-presets/harness-memory/`
5. 按第一节改配置（workspace/server_url 等）
6. 重启 dsh web（**必须走安全脚本**，见 docs/operational-notes.md）
7. 新会话选「记忆增强模式」；Web 任意会话的「记忆」tab 可用

## 四、环境适配注意

- **插件内部命名**：统一为 `deepmemory`；包名/目录名保持 `dsh-deepmemory`（挂载标识，勿改）
- **会话开关**：per-session 开关存 `session_enabled:<sessionId>`（settings 表）
- **嵌入模型**：bge-small-zh-v1.5（512 维）；更换模型需重建 FAISS 索引
- **端口**：memory-server 默认 6230，`MEMORY_SERVER_PORT` 环境变量可改
- **同源代理**：浏览器经 `/mem-api/*` 访问后端（web 插件 host 注册），无跨域问题

## 五、验证清单（部署后逐项确认）

```bash
curl -s http://localhost:6230/v1/health            # 后端健康
curl -s http://localhost:3081/mem-api/v1/health    # 同源代理
curl -s http://localhost:3081/ | grep -c dsh-deepmemory  # web 插件在 boot 中
```

- Web：对话页「记忆」tab 出现（第二位），面板可增删改查
- Agent：新会话选记忆 preset 后，首轮回复前有记忆注入日志
- 适配：改 `deepmemory.workspace` 后，新记忆写入新工作区
