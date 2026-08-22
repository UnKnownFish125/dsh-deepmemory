#!/usr/bin/env bash
# 打包 deepmemory 发布包: dist/deepmemory-v<VERSION>.tar.gz
# 用法: bash scripts/package.sh [version]   (默认从 git tag 取版本)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "${ROOT}"

VERSION="${1:-$(git describe --tags --abbrev=0 2>/dev/null || echo 0.1.0)}"
VERSION="${VERSION#v}"
DIST="dist"
PKG="deepmemory-v${VERSION}"
TARBALL="${DIST}/${PKG}.tar.gz"

rm -rf "${PKG}"
rm -f "${TARBALL}"
mkdir -p "${DIST}" "${PKG}"

# 发布物: 安装脚本 + 三组件 + 文档（不含 data/.git/__pycache__）
cp -r scripts "${PKG}/scripts"
rm -f "${PKG}/scripts/fix-session-roles.py"
cp -r memory-server "${PKG}/memory-server"
rm -rf "${PKG}/memory-server/data" "${PKG}/memory-server/models" "${PKG}/memory-server/__pycache__"
cp -r web-plugin "${PKG}/web-plugin"
cp -r agent-preset "${PKG}/agent-preset"
find "${PKG}/agent-preset" -name '*.bak*' -delete 2>/dev/null || true
find "${PKG}" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${PKG}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
cp README.md "${PKG}/README.md"
chmod +x "${PKG}/scripts/install.sh" "${PKG}/scripts/package.sh" 2>/dev/null || true

cat > "${PKG}/VERSION" <<EOF
deepmemory v${VERSION}
打包时间: $(date '+%Y-%m-%d %H:%M:%S')
安装: sudo bash scripts/install.sh
EOF

tar -czf "${TARBALL}" "${PKG}"
rm -rf "${PKG}"
echo "已生成: ${TARBALL} ($(du -h "${TARBALL}" | cut -f1))"
