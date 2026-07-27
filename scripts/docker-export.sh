#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法: ./scripts/docker-export.sh [架构] [版本] [输出目录]

架构:
  amd64 | x86 | x86_64       导出 Linux x86_64 镜像
  arm64 | arm | aarch64      导出 Linux ARM64 镜像
  all                        分别导出两种架构（默认）

版本默认为 latest，输出目录默认为 dist/docker。
镜像名必须与打包时一致，可通过 IMAGE_NAME 环境变量修改。

示例:
  ./scripts/docker-export.sh amd64 1.0.0
  ./scripts/docker-export.sh all 1.0.0 ./dist/docker
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

ARCH="${1:-all}"
VERSION="${2:-latest}"
OUTPUT_DIR="${3:-dist/docker}"
IMAGE_NAME="${IMAGE_NAME:-subflow}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_NAME="${IMAGE_NAME//\//-}"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker 命令。" >&2
  exit 1
fi

if ! command -v gzip >/dev/null 2>&1; then
  echo "错误: 未找到 gzip 命令。" >&2
  exit 1
fi

export_image() {
  local suffix="$1"
  local image="${IMAGE_NAME}:${VERSION}-${suffix}"
  local archive="${OUTPUT_DIR}/${ARCHIVE_NAME}-${VERSION}-linux-${suffix}.tar.gz"
  local temporary="${archive}.tmp"

  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    echo "错误: 本地镜像 ${image} 不存在，请先运行 docker-build.sh。" >&2
    exit 1
  fi

  echo "==> 导出 ${image}"
  docker save "${image}" | gzip -c > "${temporary}"
  mv "${temporary}" "${archive}"
  echo "==> 已生成 ${archive}"
}

cd "${PROJECT_ROOT}"
mkdir -p "${OUTPUT_DIR}"

case "${ARCH}" in
  amd64 | x86 | x86_64)
    export_image "amd64"
    ;;
  arm64 | arm | aarch64)
    export_image "arm64"
    ;;
  all)
    export_image "amd64"
    export_image "arm64"
    ;;
  *)
    echo "错误: 不支持的架构 '${ARCH}'，可选值为 amd64、arm64、all。" >&2
    usage >&2
    exit 2
    ;;
esac
