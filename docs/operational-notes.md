# 运营注意事项（血的教训）

## 1. 禁止直接 kill dsh web 进程！

**根因事故**：`kill <dsh web PID>` 会在会话日志写入中途截断 zstd 帧，且历史曾出现 `role:system` 的 `user/message` 事件（早期记忆注入方式埋的雷），重启加载校验失败导致整段历史拒绝加载、web 崩溃。

**重启流程（必须走脚本）**：

```bash
# ① 修复当前活跃会话日志中的 role 雷（可选，正常时跳过）
#    注意：活跃会话文件必须在 dsh web 停止后才能安全重写！
# ② 安全重启（自动备份、校验、回滚）
bash /www/scripts/dsh-safe-restart.sh
```

若活跃会话日志有 role 异常（扫描命令）：
```bash
for f in /www/dsh/home/sessions/*/*/session.jsonl.zstd; do
  echo "$f => $(zstd -dc "$f" | python3 -c "
import sys, json
bad = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        e = json.loads(line)
        if e.get('type') == 'user/message' and e.get('data', {}).get('message', {}).get('role') != 'user': bad += 1
    except Exception: pass
print(bad)")"
done
```

修复（**仅 dsh web 停止后**）：
```bash
/opt/AstrBot/venv/bin/python3 /www/scripts/fix-session-roles.py <文件路径...>
```

## 2. 记忆注入的正确姿势

- ❌ **禁止**：pre-step 里把 `role: 'system'` 消息塞进消息序列（会在日志埋雷）
- ❌ **禁止**：把记忆块 prepend 到用户消息 content（污染用户消息文本）
- ✅ **正确**：`systemPrompt.section` 注册（order 50），pre-step 异步拉取存缓存，section 同步读缓存渲染

## 3. 关键路径

| 组件 | 路径 |
|---|---|
| memory-server | `/www/deepseek hardness workspace/memory-server/server.py`（systemd: dsh-memory-server, :6230） |
| web 插件 | `/www/dsh/home/profiles/web/node_modules/dsh-memory-ui/` |
| agent preset | `/www/dsh/home/.agent-presets/harness-memory.bak-0203/`（注意目录名） |
| 会话日志 | `/www/dsh/home/sessions/`（每行一个独立 zstd 帧，勿用普通 zstd 重压整文件） |
| 安全重启 | `/www/scripts/dsh-safe-restart.sh` |
| role 修复 | `/www/scripts/fix-session-roles.py` |

## 4. 日志文件格式

`session.jsonl.zstd` = **每行一个独立 zstd 帧**串接。操作时：
- 解压全部帧：`zstd -dc file`
- 重压缩必须逐行独立压缩（用 `fix-session-roles.py` 的 `compress_lines`），
  不可用 `zstd` CLI 整文件压缩（DSH 校验"first frame is exactly one header line"）
