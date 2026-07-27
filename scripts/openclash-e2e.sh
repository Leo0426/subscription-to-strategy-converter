#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法:
  SUBFLOW_E2E_SUBSCRIPTION_URL='https://example.com/sub' ./scripts/openclash-e2e.sh

从真实上游订阅创建长期 Profile，再用独立 Docker 容器模拟 OpenClash：
1. 构建并启动 Subflow；
2. 从另一个容器拉取生成后的长期订阅；
3. 用 Mihomo 检查配置并启动内核；
4. 对“自动选择”执行真实节点测速；
5. 通过生成配置代理访问 Google generate_204。

可选环境变量:
  SUBFLOW_E2E_APP_IMAGE       Subflow 测试镜像，默认 subflow:openclash-e2e
  SUBFLOW_E2E_MIHOMO_IMAGE    Mihomo 镜像，默认 metacubex/mihomo:latest
  SUBFLOW_E2E_CURL_IMAGE      curl 镜像，默认 curlimages/curl:8.12.1
  SUBFLOW_E2E_SKIP_BUILD      设为 1 时跳过 Subflow 镜像构建
  SUBFLOW_E2E_KEEP_ARTIFACTS  设为 1 时保留临时配置和容器用于排障
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SUBSCRIPTION_URL="${SUBFLOW_E2E_SUBSCRIPTION_URL:-}"
if [[ -z "${SUBSCRIPTION_URL}" ]]; then
  echo "错误: 必须设置 SUBFLOW_E2E_SUBSCRIPTION_URL。" >&2
  exit 2
fi

for command_name in docker curl python3; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "错误: 未找到 ${command_name} 命令。" >&2
    exit 1
  fi
done

APP_IMAGE="${SUBFLOW_E2E_APP_IMAGE:-subflow:openclash-e2e}"
MIHOMO_IMAGE="${SUBFLOW_E2E_MIHOMO_IMAGE:-metacubex/mihomo:latest}"
CURL_IMAGE="${SUBFLOW_E2E_CURL_IMAGE:-curlimages/curl:8.12.1}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="subflow-e2e-$$"
NETWORK="${RUN_ID}"
APP_CONTAINER="${RUN_ID}-app"
MIHOMO_CONTAINER="${RUN_ID}-mihomo"
ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/subflow-openclash-e2e.XXXXXX")"
chmod 700 "${ARTIFACT_DIR}"

cleanup() {
  if [[ "${SUBFLOW_E2E_KEEP_ARTIFACTS:-0}" == "1" ]]; then
    echo "保留排障现场: ${ARTIFACT_DIR}"
    echo "容器: ${APP_CONTAINER}, ${MIHOMO_CONTAINER}"
    return
  fi
  docker rm -f "${MIHOMO_CONTAINER}" "${APP_CONTAINER}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK}" >/dev/null 2>&1 || true
  rm -rf "${ARTIFACT_DIR}"
}
trap cleanup EXIT

cd "${PROJECT_ROOT}"

if [[ "${SUBFLOW_E2E_SKIP_BUILD:-0}" != "1" ]]; then
  echo "==> 构建 Subflow 镜像"
  docker build --tag "${APP_IMAGE}" .
fi

echo "==> 启动隔离测试网络与 Subflow"
docker network create "${NETWORK}" >/dev/null
docker run --detach --name "${APP_CONTAINER}" \
  --network "${NETWORK}" \
  --publish 127.0.0.1::8000 \
  --env SUBFLOW_DB_PATH=/app/data/subflow-e2e.db \
  --env "SUBFLOW_PUBLIC_BASE_URL=http://${APP_CONTAINER}:8000" \
  "${APP_IMAGE}" >/dev/null

APP_PORT="$(docker port "${APP_CONTAINER}" 8000/tcp | awk -F: 'NR == 1 {print $NF}')"
for _ in $(seq 1 30); do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:${APP_PORT}/health" >/dev/null; then
    break
  fi
  sleep 0.5
done
curl --silent --fail --max-time 2 "http://127.0.0.1:${APP_PORT}/health" >/dev/null

echo "==> 创建长期 Clash Profile"
REQUEST_JSON="$(
  SUBFLOW_URL_VALUE="${SUBSCRIPTION_URL}" python3 -c \
    'import json, os; print(json.dumps({"subscription_url": os.environ["SUBFLOW_URL_VALUE"], "target": "clash"}))'
)"
curl --silent --show-error --fail --max-time 90 \
  --header 'Content-Type: application/json' \
  --data-binary "${REQUEST_JSON}" \
  --output "${ARTIFACT_DIR}/profile.json" \
  "http://127.0.0.1:${APP_PORT}/profiles"

PROFILE_URL="$(
  python3 -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["subscribe_urls"]["clash"])' \
    "${ARTIFACT_DIR}/profile.json"
)"

echo "==> 从独立容器拉取生成订阅"
docker run --rm \
  --network "${NETWORK}" \
  --volume "${ARTIFACT_DIR}:/work" \
  "${CURL_IMAGE}" \
  --silent --show-error --fail --max-time 120 \
  --output /work/config.yaml \
  "${PROFILE_URL}"

echo "==> Mihomo 内核加载检查"
docker run --rm \
  --volume "${ARTIFACT_DIR}:/root/.config/mihomo" \
  "${MIHOMO_IMAGE}" \
  -t -f /root/.config/mihomo/config.yaml

echo "==> 启动 Mihomo 并执行节点与代理连通性检查"
docker run --detach --name "${MIHOMO_CONTAINER}" \
  --network "${NETWORK}" \
  --publish 127.0.0.1::7890 \
  --publish 127.0.0.1::9090 \
  --volume "${ARTIFACT_DIR}:/root/.config/mihomo" \
  "${MIHOMO_IMAGE}" \
  -ext-ctl 0.0.0.0:9090 \
  -f /root/.config/mihomo/config.yaml >/dev/null

MIXED_PORT="$(docker port "${MIHOMO_CONTAINER}" 7890/tcp | awk -F: 'NR == 1 {print $NF}')"
CONTROL_PORT="$(docker port "${MIHOMO_CONTAINER}" 9090/tcp | awk -F: 'NR == 1 {print $NF}')"
for _ in $(seq 1 60); do
  if curl --silent --fail --max-time 2 "http://127.0.0.1:${CONTROL_PORT}/version" >/dev/null; then
    break
  fi
  sleep 0.5
done
curl --silent --fail --max-time 2 "http://127.0.0.1:${CONTROL_PORT}/version" >/dev/null

curl --silent --show-error --fail --max-time 30 \
  --get \
  --data-urlencode 'url=https://www.gstatic.com/generate_204' \
  --data-urlencode 'timeout=5000' \
  --output "${ARTIFACT_DIR}/delays.json" \
  "http://127.0.0.1:${CONTROL_PORT}/group/%E8%87%AA%E5%8A%A8%E9%80%89%E6%8B%A9/delay"

AVAILABLE_NODES="$(
  python3 -c \
    'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(sum(isinstance(v, (int, float)) and v > 0 for v in data.values()))' \
    "${ARTIFACT_DIR}/delays.json"
)"
if [[ "${AVAILABLE_NODES}" -lt 1 ]]; then
  echo "错误: 自动选择组没有通过延迟测试的节点。" >&2
  exit 1
fi

GOOGLE_STATUS="000"
for _ in $(seq 1 60); do
  GOOGLE_STATUS="$(
    curl --silent --max-time 5 \
      --proxy "http://127.0.0.1:${MIXED_PORT}" \
      --output /dev/null \
      --write-out '%{http_code}' \
      https://www.google.com/generate_204 || true
  )"
  if [[ "${GOOGLE_STATUS}" == "204" ]]; then
    break
  fi
  sleep 0.5
done
if [[ "${GOOGLE_STATUS}" != "204" ]]; then
  echo "错误: 实际代理请求返回 HTTP ${GOOGLE_STATUS}，预期 204。" >&2
  docker logs --tail 80 "${MIHOMO_CONTAINER}" >&2
  exit 1
fi

echo "PASS: 长期订阅可拉取、Mihomo 可加载、${AVAILABLE_NODES} 个节点通过测速、实际代理返回 HTTP 204。"
