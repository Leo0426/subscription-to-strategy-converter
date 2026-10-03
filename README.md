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

## 开发

```sh
uv run pytest -q
uv run python scripts/sync-service-rules.py --check
```

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
- 上游暂时不可用可能返回上次成功产物，带 `X-Subflow-Stale: true`；无效来源、配置和鉴权错误直接报错。
- 手机故障需在手机核对版本及 Wi-Fi/蜂窝差异；工作台诊断只读取配置的控制器或本机 Surge。

</details>
