# Subflow · Leo 策略订阅生成器

![Subflow 公开策略账本与订阅生成器](docs/assets/subflow-public-ledger.png)

把一条已授权的通用（全协议）、Clash/Mihomo 或 Surge 节点订阅，转换成基于 [`leo.yaml`](community_templates/leo/leo.yaml) 的 Clash/Mihomo、Surge 与 Shadowrocket 长期订阅；页面只做三件事——读取节点、按服务覆盖出口、生成订阅，模板结构、规则来源、质量审计和平台边界则通过公开接口完整披露。

## 客户端输出

| 客户端 | 生成内容 | 导入方式 |
| --- | --- | --- |
| Clash 系列（Mihomo / Clash.Meta 内核） | 完整 Leo YAML 配置 | 导入 Clash/Mihomo 订阅链接 |
| Surge 5.21+ | 节点、策略组、兼容分流规则的 `.conf` | 导入 Surge 订阅链接 |
| Shadowrocket | 节点订阅 + 配套 Leo `.conf` | 首页添加节点订阅；再在「配置 → 添加配置」导入配套配置并启用 |

三类输出共用一个 Profile、一个机场来源和同一份策略选择。Shadowrocket 更新时需同步更新节点订阅和配套配置，节点名称才能与策略组保持一致；两个链接独立缓存，上游暂时不可用时只回退到各自最后成功的产物。API 在 `subscribe_urls` 返回三类节点/完整订阅地址，另在 `config_urls.shadowrocket` 返回配套配置地址。

Shadowrocket 通过客户端兼容的 Clash 节点 YAML 保留连接字段，当前转换器覆盖 SS、SSR、VMess、VLESS、Trojan、Hysteria、Hysteria2、TUIC、AnyTLS、HTTP、SOCKS5；VLESS Reality、Hysteria2 混淆和端口跳跃、TUIC、AnyTLS 已有字段保留回归。WireGuard 等尚未验证的输出协议暂不导出，部分失败通过 `X-Compile-Warnings` 报告；全部节点不可导出时返回错误。参考 [Sub-Store 的 Shadowrocket producer](https://github.com/sub-store-org/Sub-Store/blob/master/backend/src/core/proxy-utils/producers/shadowrocket.js) 与 [Shadowrocket 配置示例作者提供的原生格式](https://github.com/LOWERTOP/Shadowrocket/blob/main/lazy_group.conf)。

Surge 与 Shadowrocket 的规则兼容输出会跳过尚不支持的 GEOSITE、逻辑规则等，并报告 warning；不会把 Mihomo 专属配置原样当作客户端原生配置。旧 Clash 内核不在完整 Leo 配置兼容范围内。自动化验收覆盖生成内容与接口，不能替代实际客户端的导入和联网验证。

## 通用（全协议）订阅

直接粘贴原订阅 URL。Subflow 用 `clash.meta/1.19.30 mihomo/1.19.30 subflow/0.1` 请求 Mihomo 格式，支持按客户端自动识别的通用链接；保留原链接参数、重定向检查和节点连接字段。已有回归覆盖 VLESS Reality、Hysteria2、TUIC、AnyTLS 从读取到 Leo/Mihomo 产物的字段保留。最终可用协议仍取决于上游返回内容和目标客户端；Surge 不支持的协议会按现有机制报告。

如果服务商要求特定 User-Agent，可设置 `SUBFLOW_SUBSCRIPTION_USER_AGENT` 并重启服务；空值使用默认标识。格式协商依据可见 [XBoard 的客户端识别实现](https://github.com/cedar2025/Xboard/blob/master/app/Http/Controllers/V1/Client/ClientController.php) 和 [Clash.Meta 输出实现](https://github.com/cedar2025/Xboard/blob/master/app/Protocols/ClashMeta.php)。

固定返回 Base64/URI 列表的链接（例如某些 `flag=general` 链接）仍需兼容适配器，或改用服务商提供的 Clash/Mihomo 链接。Subflow 不自动改写这些参数，也不自动使用公共转换站。Docker 可启用仓库提供的适配器：

```sh
docker compose -f docker-compose.yml -f docker-compose.compatibility.yml up -d --build
```

非 Docker 部署设置 `SUBFLOW_SUBCONVERTER_URL` 为自建 subconverter 的地址。适配器支持的协议范围取决于所用版本，启用它不代表任意新协议都能转换。
