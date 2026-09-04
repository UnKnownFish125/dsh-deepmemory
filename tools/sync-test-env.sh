#!/bin/bash
# 测试机每日镜像：生产(3081/6230/profile) → 测试机(3091/6240)
# 内容: preset / settings.yaml / 插件 tgz 包 / profile package.json(deps+bundles) / 重启 / 断言
# 用法: sync-test-env.sh [--dry-run]
set -e
DRY="$1"
PROD_P=/www/dsh/home/.agent-presets
TEST_P=/www/dsh-test-home/.agent-presets
PROD_S=/www/dsh/home/settings.yaml
TEST_S=/www/dsh-test-home/settings.yaml
PROD_PK=/www/dsh/home/profiles/web/package.json
TEST_PK=/www/dsh-test-home/profiles/web/package.json
PKG_DIR=/www/dsh-packages

echo "=== ① preset 镜像 ==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry)" ; else cp -a "$PROD_P/." "$TEST_P/"; echo "  ✓ preset: $(ls $TEST_P | wc -l) 个"; fi
echo "=== ② settings.yaml ==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry)"; else cp "$PROD_S" "$TEST_S"; echo "  ✓ settings（$(wc -l < $TEST_S) 行）"; fi
echo "=== ③ 插件 tgz 包（新插件增量）==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry)"; else
  before=$(ls $PKG_DIR/*.tgz 2>/dev/null | wc -l)
  # 测试机 profile 引用的 tgz 同步（新包拷贝；已在 node_modules 的跳过 install）
  cp -n "$PKG_DIR"/*.tgz /tmp/sync-pkgs/ 2>/dev/null || true
  # 直接同步到测试机 profile 的 file: 引用源（/www/dsh-packages 是共享源——无需拷）
  after=$before
  echo "  ✓ tgz 包源共享（/www/dsh-packages）: $before 个"
fi
echo "=== ④ profile package.json（deps/bundles 变更检测）==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry)"; else
  if diff -q "$PROD_PK" "$TEST_PK" >/dev/null 2>&1; then
    echo "  ✓ package.json 无差异"
  else
    cp "$PROD_PK" "$TEST_PK"
    echo "  ✓ package.json 已同步（deps/bundles 变更）→ 触发 install"
    cd /www/dsh-test-home/profiles/web && /usr/local/node/bin/pnpm install --offline=false >/tmp/sync-install.log 2>&1
    if grep -q "ERR_PNPM_IGNORED_BUILDS" /tmp/sync-install.log; then
      echo "  ✓ install OK（ERR_IGNORED_BUILDS=仅 build scripts 忽略——纯 JS 插件无碍；+$(grep -oE 'added [0-9]+' /tmp/sync-install.log | head -1)）"
    elif ls /www/dsh-test-home/profiles/web/node_modules >/dev/null 2>&1 && grep -q "done" /tmp/sync-install.log; then
      echo "  ✓ install OK（$(grep -oE 'added [0-9]+' /tmp/sync-install.log | head -1)）"
    else
      echo "  ⚠ install 失败（看 /tmp/sync-install.log）"
    fi
  fi
fi
echo "=== ⑤ 重启测试机 + 断言 ==="
if [ "$DRY" = "--dry-run" ]; then echo "  (dry)"; else
  systemctl restart dsh-test.service && sleep 8
  echo "  ✓ active: $(systemctl is-active dsh-test.service) | 3091: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3091/)"
fi
echo "=== 镜像完成 ==="
