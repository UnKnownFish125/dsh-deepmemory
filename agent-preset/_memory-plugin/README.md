# _memory-plugin（生产加载路径）

DSH 生产实际加载路径：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`（agent presets 安装时以 `_` 前缀实例化）。

## ⚠️ 2026-10-21（P0-2）起：单一事实源已收敛，本目录副本降级为 DEPRECATED

| 角色 | 路径 | 行数 | md5 | 状态 |
|---|---|---|---|---|
| **权威（live）** | `/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` | 1620 | `1b32cd85f855182e481f0ea827293afd` | ✅ 线上真实加载 |
| **仓库镜像** | `agent-preset/memory-plugin/plugin-v3.js` | 1630（10 行镜像声明 + 1620） | 正文与 live 逐字节一致 | ✅ 唯一仓库副本 |
| 旧副本（本目录） | `agent-preset/_memory-plugin/plugin-v3.js` | 849 + 10 行 DEPRECATED 声明 | 原 `ed28d8b2…` | ⚠️ DEPRECATED，仅因 `daily`/`task` preset 仍引用而保留 |

- **规则改为单向**：只允许 `live → agent-preset/memory-plugin/plugin-v3.js` 回灌；本目录不再维护同版本副本。
- 校验：`md5sum /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` 必须等于 `1b32cd85f855182e481f0ea827293afd`。
- 收口待办：`agent-preset/daily/agent.cordis.yml:92` 与 `agent-preset/task/agent.cordis.yml:176` 改为指向
  `../memory-plugin/plugin-v3.js` 后，把本目录的 `plugin-v3.js` 移入 `agent-preset/archive/`。
- 历史（已作废）：此前要求 `plugin-v3.js` 与上级 `memory-plugin/plugin-v3.js` **保持逐字节一致**（部署时复制）——
  该做法导致 1155/849/1620 三版长期并存、缺 `memory_call`/`child_memory`。
- 变更后仍须过 preflight：`node --check` + `node --input-type=module -e "await import(...)"`（ESM 语义校验，防 CJS 宽松模式漏检）。
- 历史事故：四击事故中 ②④（漏 order/非 async await）均为"检查工具语义≠运行时语义"导致——ESM 冒烟为强制门禁。
