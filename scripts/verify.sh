#!/usr/bin/env bash
# deepmemory 更新前验证（空白试验机）：全部在临时目录/随机端口/模拟环境里跑，
# 不触碰生产的 data、端口、dsh 进程。任何一项 FAIL 都禁止进入生产。
#
# 验证三层：
#   1. memory-server: 语法 + 隔离实例（临时 data + 随机端口 + 复用生产模型）API 冒烟
#   2. web client.js: ESM -> __ModuleLoader__ 转换 + 节点级模拟渲染
#   3. preset plugin: 语法 + import + mock ctx 完整 apply + 三工具 defineTool 格式
#
# 用法: bash scripts/verify.sh [包根目录]   (默认本仓库根)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$(dirname "$HERE")}"
VENV_PY="${VENV_PY:-/opt/AstrBot/venv/bin/python3}"
NODE="${NODE:-node}"
FAIL=0

step()  { printf '[verify] %-42s' "$1"; }
ok()    { echo "OK"; }
bad()   { echo "FAIL"; FAIL=1; }

# ---------- 临时根：数据盘（绝不落系统盘 /tmp） ----------
# 系统盘空间紧张；试验机所有临时数据（含无头浏览器 profile、vite 缓存）都在数据盘。
VERIFY_TMP_ROOT="${VERIFY_TMP_ROOT:-/www/verify-tmp}"
mkdir -p "$VERIFY_TMP_ROOT"
export TMPDIR="$VERIFY_TMP_ROOT"   # playwright/chromium 的临时文件同样走数据盘

# ---------------- 1. memory-server ----------------
step "server.py 语法"
if "$VENV_PY" -m py_compile "$ROOT/memory-server/server.py" 2>/dev/null; then ok; else bad; fi

step "隔离实例冒烟 (临时data+随机端口)"
TMP=$(mktemp -d "$VERIFY_TMP_ROOT/server-XXXXXX")
cp "$ROOT/memory-server/"*.py "$ROOT/memory-server/"*.json "$TMP/" 2>/dev/null
[ -d "$ROOT/memory-server/models" ] && ln -s "$ROOT/memory-server/models" "$TMP/models"
PORT=$((20000 + RANDOM % 20000))
MEMORY_SERVER_PORT=$PORT "$VENV_PY" "$TMP/server.py" >"$TMP/run.log" 2>&1 &
SPID=$!
UP=0
for _ in $(seq 1 40); do
  sleep 1
  curl -s -m 3 "http://127.0.0.1:$PORT/v1/health" | grep -q '"status": "ok"' && { UP=1; break; }
done
if [ "$UP" != "1" ]; then bad; tail -5 "$TMP/run.log"; else
  # API 冒烟: 写入(带实体/关系) -> 检索 -> 图谱 -> 备份 -> 重建
  A=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/memories/add" -H 'Content-Type: application/json' \
    -d '{"content":"验证冒烟记忆：试验机写入路径","type":"fact","domain":"work","scope":"workspace","workspace_id":"verify","importance":0.6,"entities":[{"name":"试验机","kind":"tool"}],"relations":[{"source":"试验机","relation":"验证","target":"写入路径"}]}')
  S=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/memories/search" -H 'Content-Type: application/json' \
    -d '{"query":"试验机","k":3,"workspace_id":"verify"}')
  G=$(curl -s "http://127.0.0.1:$PORT/v1/graph")
  B=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/backups/create")
  R=$(curl -s -X POST "http://127.0.0.1:$PORT/v1/maintenance/rebuild" -H 'Content-Type: application/json' -d '{}')
  if echo "$A" | grep -q '"id"'; then echo -n "写入✓"; else echo -n "写入✗"; FAIL=1; fi
  if echo "$S" | grep -q '"count"'; then echo -n " 检索✓"; else echo -n " 检索✗"; FAIL=1; fi
  if echo "$G" | grep -q '"edges"'; then echo -n " 图谱✓"; else echo -n " 图谱✗"; FAIL=1; fi
  if echo "$B" | grep -q '"name"'; then echo -n " 备份✓"; else echo -n " 备份✗"; FAIL=1; fi
  if echo "$R" | grep -q '"rebuilt"'; then echo -n " 重建✓"; else echo -n " 重建✗"; FAIL=1; fi
  echo
fi
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
rm -rf "$TMP"

# ---------------- 2. web client.js ----------------
step "client.js 转换+渲染验证"
CTMP=$(mktemp -d "$VERIFY_TMP_ROOT/client-XXXXXX")
cp "$ROOT/web-plugin/client.js" "$CTMP/client.js"
"$VENV_PY" "$ROOT/scripts/fix-client-bundle.py" "$CTMP/client.js" dsh-deepmemory >/dev/null 2>&1 || { bad; rm -rf "$CTMP"; }
CLIENT_JS="$CTMP/client.js" "$NODE" "$HERE/verify/render-check.mjs" >/dev/null 2>&1 && ok || bad
rm -rf "$CTMP"

# ---------------- 3. preset plugin ----------------
step "preset 插件语法+apply+defineTool"
PLUGIN="$ROOT/agent-preset/memory-plugin/plugin-v3.js"
"$NODE" --input-type=module --check < "$PLUGIN" 2>/dev/null || bad
PLUGIN_PATH="$PLUGIN" "$NODE" "$HERE/verify/plugin-check.mjs" >/dev/null 2>&1 && ok || bad

# ---------------- 3.1 preset contracts (task/daily/blank) ----------------
step "preset 契约验证 (task/daily/blank)"
"$NODE" "$HERE/verify/preset-check.mjs" >/dev/null 2>&1 && ok || bad

# ---------------- 4. 浏览器模拟（临时 DSH_HOME + 随机端口 + 无头 Chromium） ----------------
step "浏览器模拟 (临时实例+无头Chromium)"
WEBPY="${WEBPY:-/opt/AstrBot/venv/bin/python3}"
BROWSER_OK=0
if [ -x "$WEBPY" ] && "$WEBPY" -c "import playwright" >/dev/null 2>&1; then
  # 真实启动一次无头浏览器来探测可用性（browsers 路径可能经 PLAYWRIGHT_BROWSERS_PATH 定制）
  if "$WEBPY" -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
try:
    b = p.chromium.launch(headless=True, args=['--no-sandbox'])
    b.close()
finally:
    p.stop()
" >/dev/null 2>&1; then BROWSER_OK=1; fi
fi
if [ "$BROWSER_OK" != "1" ]; then
  echo "SKIP (chromium 不可用: $WEBPY -m playwright install chromium)"
else
  VHOME=$(mktemp -d "$VERIFY_TMP_ROOT/dshweb-XXXXXX")
  REAL_HOME="${DSH_HOME:-${HOME}/.dsh}"
  mkdir -p "$VHOME/profiles/web" "$VHOME/sessions"
  # profiles/web: 配置文件真实复制，node_modules 顶层条目逐条软链（跨文件系统安全、零生产写入）
  for f in package.json cordis.patch.yml pnpm-workspace.yaml pnpm-lock.yaml; do
    [ -f "$REAL_HOME/profiles/web/$f" ] && cp "$REAL_HOME/profiles/web/$f" "$VHOME/profiles/web/"
  done
  mkdir -p "$VHOME/profiles/web/node_modules"
  for e in "$REAL_HOME"/profiles/web/node_modules/* "$REAL_HOME"/profiles/web/node_modules/.[!.]*; do
    [ -e "$e" ] && ln -s "$e" "$VHOME/profiles/web/node_modules/$(basename "$e")" 2>/dev/null || true
  done
  # 用待验证的 repo 版 dsh-deepmemory 覆盖（真实目录替换软链，不影响生产）
  rm -f "$VHOME/profiles/web/node_modules/dsh-deepmemory"
  mkdir -p "$VHOME/profiles/web/node_modules/dsh-deepmemory"
  cp "$ROOT/web-plugin/"* "$VHOME/profiles/web/node_modules/dsh-deepmemory/" 2>/dev/null
  "$VENV_PY" "$ROOT/scripts/fix-client-bundle.py" "$VHOME/profiles/web/node_modules/dsh-deepmemory/client.js" dsh-deepmemory >/dev/null 2>&1 || { bad; rm -rf "$VHOME"; }
  # 工作区注册表（供 New Session 使用），sessions 保持为空（新建空会话，不碰生产对话数据）
  mkdir -p "$VHOME/storages"
  [ -f "$REAL_HOME/storages/workspace.json" ] && cp "$REAL_HOME/storages/workspace.json" "$VHOME/storages/"
  [ -f "$REAL_HOME/settings.yaml" ] && cp "$REAL_HOME/settings.yaml" "$VHOME/"
  # 凭证：让验证会话能真实发一条消息（临时复制，验证完随 VHOME 删除）
  [ -f "$REAL_HOME/.credentials.yaml" ] && cp "$REAL_HOME/.credentials.yaml" "$VHOME/"
  WPORT=$((30000 + RANDOM % 15000))
  DSH_HOME="$VHOME" node /usr/local/bin/dsh web --port "$WPORT" >"$VHOME/web.log" 2>&1 &
  WPID=$!
  WUP=0
  for _ in $(seq 1 90); do
    sleep 1
    curl -s -m 3 -o /dev/null "http://127.0.0.1:$WPORT/" && { WUP=1; break; }
  done
  if [ "$WUP" != "1" ]; then
    bad; tail -8 "$VHOME/web.log"
  else
    VERIFY_BASE_URL="http://127.0.0.1:$WPORT" VERIFY_SHOT="$VHOME/shot.png" "$WEBPY" "$HERE/verify/web-check.py" && ok || bad
  fi
  kill "$WPID" 2>/dev/null; wait "$WPID" 2>/dev/null
  rm -rf "$VHOME"
fi

# ---------------- 汇总 ----------------
echo
if [ "$FAIL" = "0" ]; then
  echo "[verify] 全部通过 — 可以进入生产 (install.sh 会再次强制验证)"
  exit 0
else
  echo "[verify] 存在失败项 — 禁止更新生产，请修复后重跑"
  exit 1
fi
