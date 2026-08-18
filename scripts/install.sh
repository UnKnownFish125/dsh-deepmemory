#!/usr/bin/env bash
# deepmemory 一键安装 / 升级脚本（幂等，可重复执行）
#
# 用法: sudo bash install.sh
# 完成三件事：
#   1. memory-server     -> 同步源码 + 安装 systemd unit + 重启 + 健康检查
#   2. web 插件 UI       -> 复制 dsh-deepmemory + bundles 注册 + client.js 自动转换
#   3. agent preset      -> 复制 harness-memory（新会话免动态插件，重启后自动加载）
# 最后输出 dsh web 重启清单（本脚本不自动重启 dsh web，安全起见由管理员执行）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

# ---------- 0. 更新前强制验证（空白试验机，全绿才放行） ----------
if [ -f "$ROOT/scripts/verify.sh" ]; then
  echo "== 试验机验证（临时 data/随机端口/模拟环境，不碰生产）=="
  if ! bash "$ROOT/scripts/verify.sh" "$ROOT"; then
    echo "试验机验证未通过，禁止进入生产。修复后重跑 install.sh。"
    exit 1
  fi
  echo
fi

# ---------- 环境探测（可覆盖） ----------
# dsh 的 home 默认是 ~/.dsh（root 用户即 /root/.dsh），DSH_HOME 变量可覆盖
DSH_HOME="${DSH_HOME:-${HOME}/.dsh}"
APP_DIR="${APP_DIR:-/www/deepseek hardness workspace}"
VENV_PY="${VENV_PY:-/opt/AstrBot/venv/bin/python3}"
WEB_PROFILE="${DSH_HOME}/profiles/web"
PLUGIN_DIR="${WEB_PROFILE}/node_modules/dsh-deepmemory"
PRESET_DIR="${DSH_HOME}/.agent-presets/harness-memory"
SERVER_DIR="${APP_DIR}/memory-server"
UNIT_FILE="/etc/systemd/system/dsh-memory-server.service"

say()  { printf '\033[1;32m[deepmemory-install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deepmemory-install]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[deepmemory-install]\033[0m %s\n' "$*"; exit 1; }

# ---------- 1. memory-server ----------
say "1/3 memory-server -> ${SERVER_DIR}"
mkdir -p "${APP_DIR}"
if [ ! -d "${SERVER_DIR}" ]; then
  cp -r "${ROOT}/memory-server" "${SERVER_DIR}"
else
  # 排除运行时数据：绝不覆盖已部署环境的 data/ 与 models/
  rsync -a --delete --exclude='data/' --exclude='models/' "${ROOT}/memory-server/" "${SERVER_DIR}/"
fi
mkdir -p "${SERVER_DIR}/data"

say "   安装 systemd unit"
cat > "${UNIT_FILE}" <<UNIT
[Unit]
Description=DSH Memory Server (semantic long-term memory backend)
After=network.target

[Service]
Type=simple
WorkingDirectory=${SERVER_DIR}
ExecStart=${VENV_PY} server.py
Restart=always
RestartSec=5
Environment=HF_ENDPOINT=https://hf-mirror.com
Environment=MEMORY_SERVER_PORT=6230

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable dsh-memory-server >/dev/null 2>&1 || true
systemctl restart dsh-memory-server
sleep 2

for i in 1 2 3 4 5; do
  if HEALTH=$(curl -s --max-time 5 http://127.0.0.1:6230/v1/health); then
    echo "${HEALTH}" | grep -q '"status": "ok"' && break
  fi
  sleep 2
done
echo "${HEALTH:-}" | grep -q '"status": "ok"' || die "memory-server 健康检查失败: ${HEALTH:-无响应}"
say "   memory-server OK: $(echo "${HEALTH}" | head -c 120)"

# ---------- 2. web 插件 UI ----------
say "2/3 web 插件 -> ${PLUGIN_DIR}"
mkdir -p "${PLUGIN_DIR}"
for f in client.js index.js package.json dsh.patch.yml; do
  if [ -f "${ROOT}/web-plugin/${f}" ]; then
    cp "${ROOT}/web-plugin/${f}" "${PLUGIN_DIR}/${f}"
  fi
done
# client.js 幂等转换为 __ModuleLoader__.load 格式（自动，无需手工）
if [ -f "${ROOT}/scripts/fix-client-bundle.py" ]; then
  "${VENV_PY}" "${ROOT}/scripts/fix-client-bundle.py" "${PLUGIN_DIR}/client.js" dsh-deepmemory
  LAYERS=$(grep -c '__ModuleLoader__.load({' "${PLUGIN_DIR}/client.js" || true)
  [ "${LAYERS}" = "1" ] || die "client.js 转换后包裹层数=${LAYERS}，需人工处理"
  say "   client.js 已转换为 __ModuleLoader__ 格式"
fi

# bundles 幂等注册
PKG_JSON="${WEB_PROFILE}/package.json"
if [ -f "${PKG_JSON}" ]; then
  "${VENV_PY}" - "${PKG_JSON}" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding='utf-8'))
prof = data.setdefault('dsh', {}).setdefault('profile', {})
bundles = prof.setdefault('bundles', [])
if 'dsh-deepmemory' not in bundles:
    bundles.append('dsh-deepmemory')
    json.dump(data, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('   bundles 已加入 dsh-deepmemory')
else:
    print('   bundles 已含 dsh-deepmemory（跳过）')
PY
else
  warn "   未找到 ${PKG_JSON}，跳过 bundles 注册"
fi

# ---------- 3. agent preset ----------
say "3/3 agent preset -> ${PRESET_DIR}"
mkdir -p "${DSH_HOME}/.agent-presets"
if [ -d "${PRESET_DIR}" ]; then
  rsync -a --delete "${ROOT}/agent-preset/" "${PRESET_DIR}/"
else
  cp -r "${ROOT}/agent-preset" "${PRESET_DIR}"
fi
say "   preset 已就位"

# ---------- 验证摘要 ----------
say "安装完成。当前状态:"
curl -s http://127.0.0.1:6230/v1/stats || true
echo
cat <<RESTART
==================================================================
 dsh web 重启清单（请手动执行，本脚本不代劳）:
   1. 备份会话: 见 /www/scripts/dsh-safe-restart.sh 或手动 cp
   2. 重启 dsh web（AstrBot 流程 / safe-restart 脚本）
   3. 验证: 打开对话页「记忆」tab -> 图谱/归档/维护/配置/中英切换
   4. preset「记忆增强模式」的新会话将自动加载记忆插件，
      无需再 define+run 动态插件（当前旧会话除外）。
==================================================================
RESTART
