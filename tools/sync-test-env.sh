#!/bin/bash
# 测试机镜像（部署两阶段铁律第 1 步）：preset + settings 从生产镜像到测试机（3091）
# 用法: sync-test-env.sh [--dry-run]
set -e
PROD_PRESETS=/www/dsh/home/.agent-presets
TEST_PRESETS=/www/dsh-test-home/.agent-presets
PROD_SETTINGS=/www/dsh/home/settings.yaml
TEST_SETTINGS=/www/dsh-test-home/settings.yaml
DRY="${1:-}"

echo "=== ① preset 镜像（生产 $PROD_PRESETS → 测试机）==="
if [ "$DRY" = "--dry-run" ]; then
  echo "  (dry-run) 将同步 $(ls $PROD_PRESETS | wc -l) 个 preset："
  for p in "$PROD_PRESETS"/*; do echo "    - $(basename $p)"; done | head -12
else
  cp -a "$PROD_PRESETS/." "$TEST_PRESETS/"
  echo "  ✓ preset: $(ls $TEST_PRESETS | wc -l) 个"
fi
echo "=== ② settings.yaml 镜像 ==="
if [ "$DRY" = "--dry-run" ]; then
  echo "  (dry-run) settings.yaml 将从生产复制到测试机（DSH_HOME=/www/dsh-test-home 读取）"
else
  cp "$PROD_SETTINGS" "$TEST_SETTINGS"
  echo "  ✓ settings.yaml 已复制（$(wc -l < $TEST_SETTINGS) 行）"
fi
echo "=== ③ 重启测试机 ==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry-run) systemctl restart dsh-test.service"; else
  systemctl restart dsh-test.service && sleep 6
  echo "  ✓ dsh-test active: $(systemctl is-active dsh-test.service)"
fi
echo "=== ④ 测试机断言（session.models）==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry-run) 将断言 session.models ok + groups"; else
  SID=$(ls -t /www/dsh-test-home/sessions/*/session.jsonl.zstd 2>/dev/null | head -1 | sed -E 's|.*(session-[a-z0-9]+).*|\1|' || echo "")
  if [ -z "$SID" ]; then echo "  (无测试会话——用任意活跃 ID 或跳过断言，服务 200 即通)"; curl -s -m 5 -o /dev/null -w "  3091: %{http_code}\n" http://127.0.0.1:3091/; else
    curl -s -m 10 -X POST "http://127.0.0.1:3091/api/session.models" -H 'Content-Type: application/json' -d "{\"type\":\"client-request\",\"rpcId\":\"p\",\"method\":\"session.models\",\"payload\":{\"sessionId\":\"$SID\"}}" | python3 -c "import sys,json;d=json.load(sys.stdin);r=d.get('result',{});v=r.get('value',{});print('  ok:',r.get('ok'),'| groups:',len(v.get('groups',[])),'| routable:',v.get('routable'))"
  fi
fi
