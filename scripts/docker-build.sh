#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法: ./scripts/docker-build.sh [架构] [版本]

架构:
  amd64 | x86 | x86_64       构建 Linux x86_64 镜像
  arm64 | arm | aarch64      构建 Linux ARM64 镜像
  all                        分别构建两种架构（默认）

版本默认为 latest，镜像名可通过 IMAGE_NAME 环境变量修改。

示例:
  ./scripts/docker-build.sh amd64 1.0.0
  ./scripts/docker-build.sh arm64 1.0.0
  IMAGE_NAME=example/subflow ./scripts/docker-build.sh all 1.0.0
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

ARCH="${1:-all}"
VERSION="${2:-latest}"
IMAGE_NAME="${IMAGE_NAME:-subflow}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker 命令。" >&2
  exit 1
fi

build_image() {
  local platform="$1"
  local suffix="$2"
  local image="${IMAGE_NAME}:${VERSION}-${suffix}"

  echo "==> 构建 ${image} (${platform})"
  docker buildx build \
    --platform "${platform}" \
    --load \
    --tag "${image}" \
    .
  echo "==> 已生成 ${image}"
}

cd "${PROJECT_ROOT}"

case "${ARCH}" in
  amd64 | x86 | x86_64)
    build_image "linux/amd64" "amd64"
    ;;
  arm64 | arm | aarch64)
    build_image "linux/arm64" "arm64"
    ;;
  all)
    build_image "linux/amd64" "amd64"
    build_image "linux/arm64" "arm64"
    ;;
  *)
    echo "错误: 不支持的架构 '${ARCH}'，可选值为 amd64、arm64、all。" >&2
    usage >&2
    exit 2
    ;;
esac
