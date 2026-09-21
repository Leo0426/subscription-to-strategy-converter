# Subflow 6.1 · 策略订阅工作台

把一条已授权的通用、Clash/Mihomo、Surge 或 Shadowrocket 订阅，结合分流规则发布为 **Clash / OpenClash、Surge 和 Shadowrocket 的长期策略订阅**。基于 [Leo 策略](community_templates/leo/leo.yaml)，在同一页完成节点读取、服务出口设置、兼容检查和订阅更新。

[快速开始](#快速开始) · [使用流程](#使用流程) · [客户端兼容](#客户端兼容) · [常见问题](#常见问题) · [6.1 升级说明](docs/releases/6.1.md)

## 能做什么

- **按服务选择出口**：为 ChatGPT、Claude、GitHub、YouTube 等服务设置固定节点、客户端自选或主备故障切换，未修改的服务跟随 Leo。
- **发布前检查**：展示配置结构错误，以及每个客户端的协议、规则和 DNS 兼容提示；阻断项修正后才能保存。
- **更新原订阅**：粘贴已有 Subflow 订阅链接，编辑同一个 Profile，保留原 ID、token 和客户端链接。
- **明确升级旧配置**：旧策略快照可以先预览规则变化和无法保留的出口，再应用到草稿并保存。
- **按需诊断服务**：查看服务域名的配置路径；连接客户端后，读取当前节点并发起探测。
- **看清订阅状态**：显示已保存版本、配置标识和生成时间，支持强制刷新；短时重复请求复用产物，并发刷新合并处理。

Subflow 管理配置与转换结果。节点连通、目标服务接受访问和完整登录成功，是需要分别验证的结果。

## 快速开始

### Docker Compose

需要 Git、Docker 和 Docker Compose。从源码构建适合当前主机架构的镜像：

```sh
git clone https://github.com/Leo0426/subscription-to-strategy-converter.git
cd subscription-to-strategy-converter
docker compose up -d --build
```

在部署主机上打开 [http://localhost:8000](http://localhost:8000)。数据库持久化到仓库的 `./data` 目录，容器重建后可继续使用原 Profile。

如果 OpenClash 在路由器上，或客户端在手机上，请通过它们能访问的服务器地址打开工作台，例如 `http://192.168.1.10:8000`。也可在 Compose 的 `environment` 中设置 `SUBFLOW_PUBLIC_BASE_URL`，固定生成链接的地址。

### Python 本地运行

需要 Python 3.12+ 和 uv。在仓库目录执行：

```sh
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。默认数据库为 `data/subflow.db`。

### 指定架构打包

```sh
./scripts/docker-build.sh amd64 6.1
./scripts/docker-export.sh amd64 6.1
```

生成本地镜像 `subflow:6.1-amd64`（`linux/amd64`），并导出到 `dist/docker/subflow-6.1-linux-amd64.tar.gz`。需要 ARM64 时，将两条命令的 `amd64` 改为 `arm64`。

镜像标签用于本地构建或归档导入，不代表已发布到公共镜像仓库。归档加载、已有部署升级与回退步骤见 [6.1 发布说明](docs/releases/6.1.md)。

## 使用流程

1. **选择客户端**：勾选实际使用的 Clash / OpenClash、Surge 或 Shadowrocket。
2. **读取节点**：粘贴原始订阅 URL，点击「读取节点」。
3. **设置服务出口**：按需要修改 ChatGPT 等服务，其他服务保持「跟随 Leo」。
4. **检查并保存**：点击「检查所选客户端」，处理阻断项并查看兼容提示，再生成链接。
5. **导入客户端**：使用对应客户端链接；以后在工作台打开该链接进行编辑，保存后让客户端刷新订阅。

修改来源、客户端或出口后，需要重新检查。新建 Profile 会生成新链接；更新已有 Profile 才会保留原链接。

| 出口行为 | 适合的用法 | 实际行为 |
| --- | --- | --- |
| 跟随 Leo | 使用基础策略 | 由 Leo 的服务策略决定出口 |
| 固定节点 | 保持服务使用同一出口 | 只保留指定节点；节点消失或协议不兼容会报错 |
| 交给客户端选择 | 在客户端管理一个策略组 | 委托给所选策略或节点，最终选择以客户端为准 |
| 主备故障切换 | 明确配置备用出口 | 指定两个节点；主节点通用连通性检查失败时切换备用 |

主备检查使用通用连通性地址，不能据此判断 ChatGPT 等服务是否可用。新 Profile 保存服务偏好，编译时读取当前 Leo 和服务目录；旧策略快照保持原语义，直到明确预览并保存升级。

## 客户端兼容

| 客户端 | 输出 | 导入方式 |
| --- | --- | --- |
| Clash / OpenClash（Mihomo / Clash.Meta 内核） | 机场连接配置 + Leo 分流 YAML | 添加 Clash / Mihomo 订阅链接 |
| Surge 5.21+ | 节点、策略组及兼容规则的 `.conf` | 添加 Surge 订阅链接 |
| Shadowrocket | 机场原生订阅原文 + 配套分流 `.conf` | 添加节点订阅；再从「配置 → 添加配置」导入并启用配套配置 |

Shadowrocket 需要同步刷新两个链接，以保持节点名称和策略组引用一致。节点订阅原文直接传递，包括未知参数、原始名称、Base64 编码、流量和备注信息；不经过节点序列化器。清单解析只读取规则引用需要的名称、协议和地址，不能识别或存在歧义时明确报错。

Surge 跨格式输出会跳过不支持的协议并显示提示。Surge 和 Shadowrocket 的分流输出会提示不支持的规则；MRS、GEOSITE 和逻辑规则不保证等价转换。Shadowrocket 原生节点不按我们的转换器协议列表过滤。旧 Clash 内核不在完整 Leo 配置的兼容范围内。

**三个客户端统一遵循“机场负责连接配置，Subflow 负责分流”。** Leo 提供规则和必要策略组；不再默认接管机场的 DNS、Hosts、监听端口、TUN 等公共设置。源配置未设置的项继续采用客户端默认值。

- **Clash / Mihomo**：以机场原生 YAML 为底稿，保留全部公共设置、节点字段、原始节点名及连接所需的机场策略组和 Provider；不对原生节点应用跨格式修正。生成的策略组或规则 Provider 与机场重名时，重命名生成项并同步修改引用。源 `mode` 为全局或直连时切换为规则模式并提示，以使分流生效。
- **Surge 原生输入 → Surge**：保留 `[General]`、`[Host]`、`[Proxy]` 及其他非分流段，包括专用 DNS、DoH、测速地址、超时、IPv6 和节点原始参数。只替换 `[Rule]` 并增加所需策略组；保留原组及代理链引用。内部规范化的节点选择映射回原名，有歧义的名称冲突明确报错。移除上游 `#!MANAGED-CONFIG` 指令，避免更新回机场原始规则；通过 Subflow 订阅链接刷新。
- **Shadowrocket**：以 Shadowrocket 身份请求机场，原生订阅保持原文。机场返回完整 INI 时，配套配置保留所有非分流段及原策略组，只替换规则并增加必要策略组。机场只返回节点时，配套配置仅含策略组和规则，不补造 `[General]`、DNS 或 Hosts，也不从 Mihomo 配置挑选字段重建。

同一 `/all/` 链接在浏览器和不同客户端中可能返回不同内容。Mihomo 和 Shadowrocket 分别读取自己的来源，订阅缓存按来源格式区分。Shadowrocket 原文中不插入 Subflow 注释；生成标识保留在响应头，避免破坏 Base64 订阅。

API 调用方应通过 `/render` 或订阅接口生成原生 Surge 输出。`/workspace/preview` 的策略 IR 不携带完整原生配置，因此直接传给 `/compile` 生成 Surge 会明确报错，避免再次丢失机场连接设置。

Mihomo 工作区预览显示实际保留的机场设置；无损可表达的工作区可继续编译，且保留兼容提示。若节点字段无法通过策略 IR 无损往返，或跨格式预览缺少完整来源上下文，`/compile` 会要求改用 `/render` 或订阅接口。日常工作台生成和刷新链接不受这个策略 IR 限制影响。

AnyTLS 已支持 Mihomo / Surge 来源，以及 Mihomo、Surge、Shadowrocket 输出。Surge 的 AnyTLS 最低要求为 iOS 5.17.0 / Mac 6.4.3；带 ALPN 时为 iOS 5.20.0 / Mac 6.7.0，带独立证书校验名称时为 iOS 5.21.0 / Mac 6.8.0。完整 Leo 仍遵循上表的策略兼容基线；检查结果会提示本次节点所需版本，不会自动检测客户端版本。字段映射、不能等价转换的选项和验证范围见 [AnyTLS 兼容说明](docs/anytls-compatibility.md)。

### 通用订阅与兼容适配器

Mihomo 默认以 Mihomo User-Agent 请求上游；Shadowrocket 及其配套配置使用 Shadowrocket User-Agent。原始 URL 参数保持不变。`SUBFLOW_SUBSCRIPTION_USER_AGENT` 可统一覆盖身份，`SUBFLOW_SHADOWROCKET_USER_AGENT` 可单独覆盖 Shadowrocket，且优先于统一设置。

Shadowrocket 原生 Base64 / URI 链接可以直接使用。若要将这些格式转换为 Mihomo 或 Surge，需要改用提供方对应格式的链接，或启用自建兼容适配器：

```sh
docker compose -f docker-compose.yml -f docker-compose.compatibility.yml up -d --build
```

这会额外启动 subconverter。Subflow 不自动改写订阅参数，也不自动使用公共转换站；适配器的协议范围取决于所部署的版本。

## 配置与数据

| 环境变量 | 用途 |
| --- | --- |
| `SUBFLOW_DB_PATH` | 数据库路径；本地默认 `data/subflow.db`，镜像内默认 `/app/data/subflow.db` |
| `SUBFLOW_PUBLIC_BASE_URL` | 客户端可达的 Subflow 地址，用于生成订阅链接 |
| `SUBFLOW_SUBSCRIPTION_USER_AGENT` | 覆盖请求上游订阅时的 User-Agent |
| `SUBFLOW_SHADOWROCKET_USER_AGENT` | 单独覆盖 Shadowrocket 原生订阅请求身份 |
| `SUBFLOW_SUBCONVERTER_URL` | 自建 subconverter 地址，启用后用于兼容订阅转换 |
| `SUBFLOW_CACHE_TTL` | 新鲜产物复用秒数，默认 30，范围 0–300；0 关闭复用 |
| `SUBFLOW_FETCH_TIMEOUT` | 来源读取及兼容转换的网络总时限，默认 20 秒，范围 0.05–60 |
| `SUBFLOW_MAX_SUBSCRIPTION_BYTES` | 解码后来源响应大小上限，默认 5 MiB，范围 1 KiB–20 MiB |
| `SUBFLOW_MIHOMO_CONTROLLER` | 可选：Mihomo / OpenClash 控制器地址 |
| `SUBFLOW_MIHOMO_SECRET` | 可选：控制器已有的认证密钥 |
| `SUBFLOW_SURGE_CLI` | 可选：本机 Surge CLI 路径；macOS 默认自动发现标准安装位置 |

Docker 部署时将需要的变量添加到服务的 `environment` 中，再重新创建容器。控制器地址必须从 Subflow 所在环境可达，容器内的 `localhost` 指向容器自身。Linux 镜像不能直接运行 Surge Mac CLI。

Profile 在数据库中保存订阅来源与策略偏好，订阅链接中的 token 也用于授权编辑。公开策略账本仅展示模板与审计信息。请按私有数据管理数据库、备份和带 token 的链接。

已有部署升级时保持原数据挂载和访问地址。本轮会自动补充 Profile 编辑计数和产物元数据列，保留 ID、token 和客户端链接；回退需要旧镜像及升级前数据库备份。缓存、迁移和故障边界见 [订阅刷新与设备排查](docs/subscription-refresh.md)。历史版本步骤见 [升级与回退](docs/releases/5.0.md#已有部署升级与回退)。

## 常见问题

**原订阅能访问 ChatGPT，转换后不行，是 fake-ip 吗？**

仅凭这个现象无法确定。先在两个配置中选择同一节点，再检查主站、登录和资源域名是否走相同出口。工作台可固定 ChatGPT 节点并显示服务路径；Surge 无法等价保留节点专用 DNS 时会提示。通用测速成功，也不代表这个节点能访问 ChatGPT。

**检查通过，为什么页面仍打不开？**

发布检查验证配置结构和兼容性。节点出口、客户端保存的选择、远程规则集加载及服务侧响应，还需要在实际环境中核实。「服务无法访问？」提供静态路径诊断；配置客户端连接并勾选实测后，才会读取状态和发起探测。探测结果不代表完整登录或对话成功。

**上游订阅暂时不可用，会怎样？**

已有 Profile 在外部依赖获取失败时，可返回同一已保存版本、同一客户端最后成功生成的产物，并用 `X-Subflow-Stale: true` 标记。更新 Profile 会清除旧产物，并阻止旧请求写回缓存。正常产物默认复用 30 秒，可在工作台强制刷新；服务器刷新完成后仍需让客户端更新订阅。

**电脑正常，手机 ChatGPT / Claude 不能用，怎么排查？**

先核对两端订阅首行的配置标识、生成时间和实际所选节点，再检查手机登录、API 与资源域名的请求记录。本机 Surge 诊断不代表手机状态。具体步骤见 [设备对照与诊断范围](docs/subscription-refresh.md#p2看清设备拿到什么请求走到哪里)。

**为什么客户端拿不到生成的链接？**

先检查地址：手机或路由器无法通过 `127.0.0.1` 访问另一台机器。使用服务器的可达地址，并核对端口及 `SUBFLOW_PUBLIC_BASE_URL`。

## 开发与维护

```sh
uv run pytest -q
uv run python scripts/sync-service-rules.py --check
```

[服务目录](community_templates/leo/services.json) 是产品服务规则和旧 RulePack 的共同来源。修改规则后运行 `uv run python scripts/sync-service-rules.py`，同步独立 Leo 模板，并按 [规则维护说明](community_templates/leo/README.md) 更新审计。

- [项目上下文与模块职责](CONTEXT.md)
- [订阅刷新、稳定性与设备排查](docs/subscription-refresh.md)
- [服务偏好与客户端验证决策](docs/adr/0014-intent-workbench-and-client-validation.md)
- [6.1 发布说明](docs/releases/6.1.md)
- [6.0 历史发布记录](docs/releases/6.0.md)
- [5.1 历史发布记录](docs/releases/5.1.md)
- [5.0 发布与验收记录](docs/releases/5.0.md)
- 本地交互式 API 文档：启动后访问 `/docs`；健康检查：`/health`。

6.1 的验收范围见发布说明，实际镜像和归档验证结果记录在随包提供的 `subflow-6.1-build.json` 中。测试覆盖生成内容和接口行为；真实客户端导入、ChatGPT / Claude 登录与对话需在使用环境中验收。历史版本的验收见对应发布记录。
