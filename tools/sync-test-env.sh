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
  # 合并语义：生产 deps/bundles 为基线；测试机额外（测试先行验证中的插件）保留
  MERGED=$(/opt/AstrBot/venv/bin/python3 - <<PYEOF
import json
prod=json.load(open("$PROD_PK",encoding="utf-8"))
test=json.load(open("$TEST_PK",encoding="utf-8")) if __import__("os").path.exists("$TEST_PK") else prod
pd=dict(prod.get("dependencies",{})); td=dict(test.get("dependencies",{}))
for k,v in td.items():
    if k not in pd: pd[k]=v                      # 测试机独有（如测试先行插件）
pd.update(prod.get("dependencies",{}))
pb=list(prod.get("dsh",{}).get("profile",{}).get("bundles",[]))
tb=list(test.get("dsh",{}).get("profile",{}).get("bundles",[]))
for b in tb:
    if b not in pb: pb.append(b)                 # 测试机额外 bundle 保留
# 显式清单：.test-only-plugins（测试机独有——镜像保留，防覆盖丢失）
import re as _re
if __import__("pathlib").Path("/www/dsh-test-home/profiles/web/.test-only-plugins").exists():
    for line in open("/www/dsh-test-home/profiles/web/.test-only-plugins",encoding="utf-8"):
        line=line.strip()
        if not line or line.startswith("#"): continue
        if line not in pd: pd[line]="file:/www/dsh-packages/"+line+".tgz"
        if line not in pb: pb.append(line)
# 自愈：node_modules 已装但声明丢失（镜像覆盖过）的插件补回声明
import pathlib
nm=pathlib.Path("/www/dsh-test-home/profiles/web/node_modules")
for d in nm.iterdir():
    if d.is_dir() and str(d.name).startswith("dsh-") and d.name not in pd and (d/"package.json").exists():
        pd[d.name]="file:/www/dsh-packages/"+(d.name if not str(d.name).startswith("@") else d.name.split("/")[-1])+".tgz"
        if d.name not in pb: pb.append(d.name)
out={"name":prod.get("name","dsh-profile-web"),"private":True,"dependencies":pd,
     "dsh":{"profile":{"bundles":pb}}, **({k:v for k,v in prod.items() if k not in ("dependencies","dsh","name","private")})}
json.dump(out,open("$TEST_PK","w",encoding="utf-8"),ensure_ascii=False,indent=2)
print("deps:",sorted(pd.keys()))
PYEOF
)
  if diff -q "$PROD_PK" "$TEST_PK" >/dev/null 2>&1; then
    echo "  ✓ package.json 无差异"
  else
    echo "  ✓ package.json 已合并（生产基线 + 测试机独有保留）→ $MERGED"
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
