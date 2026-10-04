# Subflow 6.7.0 · 策略订阅工作台

把已有订阅结合 [Leo 分流规则](community_templates/leo/leo.yaml)，按服务选择出口，生成 Clash / OpenClash、Surge 和 Shadowrocket 的长期订阅。

## 启动

需要 Git、Docker 和 Docker Compose：

```sh
git clone https://github.com/Leo0426/subscription-to-strategy-converter.git
cd subscription-to-strategy-converter
docker compose up -d --build
```

打开 [http://localhost:8000](http://localhost:8000)，手机或路由器使用服务器的局域网地址。保留 `./data` 可保留已有订阅。

## 使用

1. 勾选客户端，粘贴原始订阅，点击「读取节点」。
2. 设置服务出口，其余保持「跟随 Leo」。
3. 检查所选客户端，处理错误和兼容提示，再生成链接。
4. 导入客户端；以后打开已有链接编辑，保存后让客户端刷新。

| 客户端 | 导入 |
| --- | --- |
| Clash / OpenClash（Mihomo） | Clash / Mihomo 订阅链接 |
| Surge iOS 5.21+ | Surge 订阅链接 |
| Shadowrocket | 节点订阅 +「配置 → 添加配置」配套配置；两个链接一起刷新 |

保留原生连接设置，跨格式限制在检查时提示；测速通过不代表服务可用。订阅 token 同时授权读取和编辑，请妥善保管。

OpenAI、Claude、Gemini 可分别指定固定节点或手选策略。三个服务均可使用 Google 登录并共用其身份域名；若希望这类登录与主站保持同一出口，请为它们选择同一固定节点，或共用同一手选策略组。检查会提示可能的出口差异。主备模式仅使用两个明确节点，通用探针成功仍需配合真实登录和对话验证。

更新规则后刷新订阅，在手机和电脑分别验证登录、连续对话、附件和所需的语音功能；手机还应比较 Wi-Fi 与蜂窝网络。手机 Surge 和路由器 OpenClash 同时工作时，也要核对最终出口，服务器端诊断不能替代设备上的请求记录。

## 开发

```sh
uv run pytest -q
uv run python scripts/sync-service-rules.py --check
```

检查结果会列出各客户端最终产物的规则与地理数据依赖；Shadowrocket 使用配套配置统计。清单不下载远程内容，默认数据库地址无法从配置确定时保留未解析状态。维护者可用公共合成 Leo 配置执行独立审计：

```sh
uv run python -m app.core.target_dependencies --inventory-only --output .scratch/target-inventory.json
uv run python -m app.core.target_dependencies --output .scratch/target-audit.json
```

审计区分文本格式验证、二进制获取和未验证项；下载成功不代表客户端能加载或规则语义正确。报告只使用公共模板，不读取已保存的私人订阅。审计失败返回退出码 1，仍有未核验项返回 2；两种情况都会保存报告。仅生成清单时返回 0。

固定版本 Mihomo 的合成 Profile 契约可单独运行：

```sh
uv run python scripts/validate-client-contract.py \
  --mihomo-binary /path/to/mihomo --expected-version v1.19.32 \
  --output-dir .scratch/client-contract
```

工具记录版本和 SHA256，分别报告解析与本地模拟出口的路由行为，验证规则顺序及规则集反例；使用临时数据库和内核进程。缺少二进制返回退出码 2，验证失败返回 1。它不验证手机、真实服务登录或所有服务偏好组合。

三项 AI 服务的固定、手选和两节点主备模式可用完整 Leo 订阅单独验证：

```sh
uv run python scripts/validate-ai-client-contract.py \
  --mihomo-binary /path/to/mihomo --expected-version v1.19.32 \
  --output-dir .scratch/ai-client-contract \
  --dependency-cache .scratch/ai-client-dependencies \
  --download-dependencies --timeout 120
```

首次下载固定公共规则与地理库并记录摘要，后续可去掉 `--download-dependencies` 离线重用。运行副本使用本地规则文件与合成探针，流量在本地出口终止；报告区分解析、路由和反例结果。这项验证不覆盖真实 TLS、流式会话、服务账号或 OpenClash 设备集成。

工作台诊断分别显示配置推断、客户端选择、规则解释、指定节点探测及配置一致性。运行态仅对应服务器配置的实例；Mihomo 根据实际 Rule/Global/Direct 模式读取选择，组选择不能证明请求命中规则。版本或模式不明、配置标识无法核对时，证据保持有限或未知。

Surge 国内域名兜底与升级步骤见 [6.7 发布说明](docs/releases/6.7.md)。

领域约束和规则维护见 [AGENTS.md](AGENTS.md)；启动后 `/docs` 查看 API，`/health` 查看状态。旧预设、规则包、意图、临时会话及无状态订阅接口已移除；已有策略快照可在工作台打开并升级。

<details>
<summary>本地运行、部署设置与排查</summary>

本地运行需要 Python 3.12+ 和 uv，在仓库目录执行：

```sh
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Docker 环境变量放在服务的 environment 中，修改后重新创建容器：

| 变量 | 用途 / 默认值 |
| --- | --- |
| `SUBFLOW_DB_PATH` | 本地 data/subflow.db；镜像内 /app/data/subflow.db |
| `SUBFLOW_PUBLIC_BASE_URL` | 客户端可达的服务器地址 |
| `SUBFLOW_SUBSCRIPTION_USER_AGENT` | 上游请求身份；`SUBFLOW_SHADOWROCKET_USER_AGENT` 单独覆盖 Shadowrocket |
| `SUBFLOW_SUBCONVERTER_URL` | 自建兼容适配器地址 |
| `SUBFLOW_CACHE_TTL` | 30 秒；0–300，0 关闭复用 |
| `SUBFLOW_FETCH_TIMEOUT` | 20 秒；0.05–60 |
| `SUBFLOW_MAX_SUBSCRIPTION_BYTES` | 5 MiB；1 KiB–20 MiB |
| `SUBFLOW_MIHOMO_CONTROLLER` / `SUBFLOW_MIHOMO_SECRET` | 可选控制器地址 / 密钥 |
| `SUBFLOW_SURGE_CLI` | 可选本机 CLI；macOS 自动发现，Linux 镜像无法直接运行 |

容器内 localhost 指向容器自身。
Shadowrocket 原生 Base64/URI 可直接使用；转其他格式优先使用机场对应链接，需要自建适配器时执行：

```sh
docker compose -f docker-compose.yml -f docker-compose.compatibility.yml up -d --build
```

适配器只转换节点，协议范围取决于部署版本；SUBCONVERTER_IMAGE 可固定 tag/digest。节点所需客户端版本以检查提示为准。

升级前备份数据目录及 SQLite WAL/SHM，保留挂载、端口和环境设置，再执行启动命令。旧数据库自动补齐所需列，已有链接保留；回退恢复旧备份和镜像。指定架构打包：

```sh
./scripts/docker-build.sh amd64 6.7.0
./scripts/docker-export.sh amd64 6.7.0
```

ARM64 将 amd64 改为 arm64；输出为本地镜像及 dist/docker 归档。

- 更新后先刷新已保存订阅，再让客户端下载并启用；对照工作台配置标识、实际节点和请求记录。
- 上游限流、超时或临时故障可能返回上次成功产物，带 `X-Subflow-Stale: true`；来源返回 401/403/404/410 时直接报错并丢弃该目标旧缓存，无效来源、配置和鉴权错误也不回退。
- 手机故障需在手机核对版本及 Wi-Fi/蜂窝差异；工作台诊断只读取配置的控制器或本机 Surge。

</details>
