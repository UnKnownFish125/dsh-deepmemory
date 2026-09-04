
## Preflight（preset 插件/关键配置部署铁律）

修改 agent-preset 插件 JS 或 settings.yaml 后、重启 dsh-web 前，**必须依次通过**：
1. `cp <文件> /tmp/chk.mjs && node --check /tmp/chk.mjs`（JS 语法）
2. `node --input-type=module -e "await import('<绝对路径>')"`（ESM 加载冒烟——防 CJS 宽松漏检）
3. 重启后 `curl -s -X POST http://127.0.0.1:3081/api/session.models -H 'Content-Type: application/json' -d '{"type":"client-request","rpcId":"p","method":"session.models","payload":{"sessionId":"<活跃会话>"}}'` 断言 `ok:true` 且 groups 数量不减
4. 发一条测试消息验证 preset 链（deepmemory ready + 无 journal 报错）

任一失败：**禁止继续，立即回滚**（.bak-* 恢复）。
已知雷区：漏 `export const inject = ['tools']`、`systemPrompt.context()` 漏 order、assemble 吞 next 返回值、PyYAML round-trip settings.yaml。
