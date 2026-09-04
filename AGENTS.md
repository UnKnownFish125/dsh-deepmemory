
## Preflight（preset 插件/关键配置部署铁律）

修改 agent-preset 插件 JS 或 settings.yaml 后、重启 dsh-web 前，**必须依次通过**：
1. `cp <文件> /tmp/chk.mjs && node --check /tmp/chk.mjs`（JS 语法）
2. `node --input-type=module -e "await import('<绝对路径>')"`（ESM 加载冒烟——防 CJS 宽松漏检）
3. 重启后 `curl -s -X POST http://127.0.0.1:3081/api/session.models -H 'Content-Type: application/json' -d '{"type":"client-request","rpcId":"p","method":"session.models","payload":{"sessionId":"<活跃会话>"}}'` 断言 `ok:true` 且 groups 数量不减
4. 发一条测试消息验证 preset 链（deepmemory ready + 无 journal 报错）

任一失败：**禁止继续，立即回滚**（.bak-* 恢复）。
已知雷区：漏 `export const inject = ['tools']`、`systemPrompt.context()` 漏 order、assemble 吞 next 返回值、PyYAML round-trip settings.yaml。

## 部署两阶段铁律（测试机必过——防"忘了测试"）

**任何生产变更（插件/server/配置），顺序必须为：测试机(3091) → 生产(3081/6230)。**
1. **第 1 步（强制）**：`bash /www/deepseek\ harness\ workspace/harness-memory-archive/tools/sync-test-env.sh` 同步镜像（preset+settings+重启+断言）
2. **第 2 步（测试机验证）**：在测试机跑变更（功能/门禁/session.models 断言）；**测试通过才允许碰生产**
3. **第 3 步（生产）**：同步生产 + 重启 + session.models 断言（AGENTS.md preflight）
4. **禁止**：跳过测试机直接改生产（六连崩事故根因——漏 inject/漏 order/PyYAML/吞链返回值均缘于此）

> 记忆点：测试机 preset = 生产 preset 的全量镜像（sync-test-env.sh 保证）；测试失败=回滚测试机（.bak-*）。
