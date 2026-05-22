# Subflow Strategy Builder

输入用户已拥有访问权限的机场订阅地址，通过 [tindy2013/subconverter](https://github.com/tindy2013/subconverter) 归一化为 Clash YAML，再和策略模板合成为 `PolicyWorkspace`。用户可以分析规则、模拟流量、查看策略图，最后编译出可信的 Mihomo YAML。

本项目的长期目标不是做一个 Clash 配置生成器，而是演进为代理策略编排平台：围绕统一 Policy IR，支持规则 provider 收集、策略图可视化、规则分析、流量模拟、多平台编译和发布管理。当前阶段已明确收束为 Workspace-first Mihomo MVP，决策记录见 [ADR 0001](docs/adr/0001-workspace-first-mihomo-mvp.md)。

Mihomo 是当前唯一质量保证输出目标；Surge 和 sing-box 保留为实验编译器。MVP 产品边界见 [Traffic Policy Control Plane MVP PRD](docs/prd/traffic-policy-control-plane-mvp.md)，架构演进路线见 [Control Plane Roadmap](docs/architecture/control-plane-roadmap.md)。

本项目不绕过鉴权，不破解订阅内容，只处理调用方提供且可正常访问的订阅地址。

## 安装

```bash
uv sync
```

本项目依赖 subconverter 处理订阅格式转换。先启动 subconverter：

```bash
docker run --rm -p 25500:25500 tindy2013/subconverter:latest
```

默认后端会请求：

```text
http://127.0.0.1:25500/sub?target=clash&url=...
```

如果 subconverter 部署在其他地址，启动本项目时设置：

```bash
SUBCONVERTER_BASE_URL=http://127.0.0.1:25500 uv run uvicorn app.main:app --reload
```

## Workspace 输入

现在配置分成两层：

- `Subconverter 转换模板`：传给 `tindy2013/subconverter` 的 `/sub` 接口，用来控制源订阅如何被归一化为 Clash YAML。对应参数包括 `config`、`include`、`exclude`、`rename`、`emoji`、`udp`、`tfo`、`sort`、`append_type`、`scv` 等。
- `策略模板`：本项目已有的 Mihomo 策略编排模板，例如 powerfullz 或 `community_templates/**/*.yaml`，负责策略组、规则集、分流关系。

两层输入会合成为 `PolicyWorkspace`，再用于分析、模拟、可视化和 Mihomo 编译。

API 示例：

```bash
curl -X POST http://127.0.0.1:8000/convert \
  -H "Content-Type: application/json" \
  -d '{
    "subscription_url": "https://example.com/sub",
    "template": "powerfullz",
    "target": "mihomo",
    "subconverter": {
      "config": "https://example.com/profile.ini",
      "include": "香港|日本|美国",
      "exclude": "官网|流量|套餐|剩余",
      "rename": "^香港@HK",
      "emoji": true,
      "udp": true,
      "sort": true
    }
  }'
```

`config` 可以是远程 `http(s)` 地址，也可以是仓库内 `community_templates` 下的本地配置路径，例如：

```text
community_templates/Overwrite/THEINI/Ordinary/tindy2013/ehpo1_main.ini
```

如果使用 Docker 运行 subconverter，本地路径需要让 subconverter 容器也能访问；更稳的方式是使用远程 `config` URL。

## 启动

```bash
uv run fastapi dev app/main.py
```

或：

```bash
uv run uvicorn app.main:app --reload
```

启动后打开：

```text
http://127.0.0.1:8000/
```

页面功能：

- 输入订阅地址并预览节点。订阅会先交给 subconverter 转为 Clash YAML，因此可支持 subconverter 兼容的源格式。
- 选择内置模板并生成 Policy Workspace。
- 查看规则、策略组、节点和 provider 的策略图。
- 运行基础规则分析和域名流量模拟。
- 编译并导出 Mihomo YAML。
- 设置 Subconverter 转换模板、节点过滤、批量重命名和转换开关。
- 管理自定义代理分组策略。
- 复制转换后的订阅 URL，供 Mihomo / Clash 客户端使用。

## API 示例

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

预览节点：

```bash
curl -X POST http://127.0.0.1:8000/preview \
  -H "Content-Type: application/json" \
  -d '{
    "subscription_url": "https://example.com/sub",
    "template": "developer",
    "target": "mihomo"
  }'
```

生成 Mihomo YAML：

```bash
curl -X POST http://127.0.0.1:8000/convert \
  -H "Content-Type: application/json" \
  -d '{
    "subscription_url": "https://example.com/sub",
    "template": "developer",
    "target": "mihomo"
  }'
```

可直接给 Mihomo / Clash 使用的转换订阅地址：

```text
http://127.0.0.1:8000/subscribe?subscription_url=https%3A%2F%2Fexample.com%2Fsub&template=developer&target=mihomo
```

这个接口直接返回 YAML，适合填到客户端的订阅 URL 中。

如果页面中配置了自定义分组策略，订阅 URL 会额外携带 `strategy` 参数，用 JSON 编码分组配置。客户端每次刷新订阅时，服务会重新拉取原始订阅并应用同一套分组策略。

返回示例：

```json
{
  "target": "mihomo",
  "template": "developer",
  "node_count": 12,
  "config": "mixed-port: 7890\n..."
}
```

## 安全边界

- 只支持 `http` / `https` 订阅 URL。
- 拒绝本地和私有地址，包括 `localhost`、`127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`::1`。
- 调用 subconverter 前会先校验订阅地址的主机名和解析结果；subconverter 请求设置 30 秒超时，并对非 2xx 响应返回明确错误。
- 不存储订阅 URL、订阅内容或生成配置。

## 当前支持格式

- 源订阅格式：由 `tindy2013/subconverter` 负责转换，后端固定请求 `target=clash`。
- 后端处理格式：subconverter 返回的 Clash YAML，读取并保留 `proxies` 节点字段。

内置模板：

- `minimal`
- `developer`
- `powerfullz`
- `henrychiao-mrs`
- `loyalsoldier-whitelist`
- `loyalsoldier-blacklist`

本地模板：

- 服务会自动扫描 `app/THEYAMLS/**/*.yaml`。
- 能被解析为 YAML 对象且包含 `proxy-groups` 的文件会出现在页面模板下拉框中。
- 本地模板 ID 形如 `local:THEYAMLS/General_Config/666OS/OneTouch_Config.yaml`。
- 生成配置时会向本地模板注入订阅解析出的 `proxies`。
- `PROXY`、`AUTO`、`手动选择`、`全球手动`、`全部节点`、`节点选择` 等常见入口分组会自动补入节点名。

暂不自动加载：

- `app/Overwrite/THEOPENCLASH/**/*.conf`：这是 OpenClash 覆写模块格式，不是完整 YAML 配置。
- `app/Overwrite/THENEWOPENCLASH/**/*.yaml`：多数文件是新版 OpenClash `[YAML]` 块覆写片段，默认包含大量注释内容，不适合作为完整订阅配置直接输出。
- `app/Overwrite/THEINI/**/*.ini`：这是 subconverter 配置格式，不是 Mihomo YAML。

模板列表 API：

```bash
curl http://127.0.0.1:8000/templates
```

## 规则模板说明

本项目内置了基于 [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) 的 RULE-SET 模板，用于生成 Clash Premium / Mihomo 可用的 `rule-providers` 和 `rules`。

模板说明：

- `loyalsoldier-whitelist`：白名单模式。先匹配直连、拒绝、Apple、Google、代理、Telegram 等规则，未命中流量最终走 `PROXY`。
- `loyalsoldier-blacklist`：黑名单模式。只将 `tld-not-cn`、`gfw`、`telegramcidr` 等命中流量走 `PROXY`，未命中流量最终 `DIRECT`。
- `developer`：在白名单规则基础上增加 `AI` 和 `GitHub` 分流组，优先处理 OpenAI、ChatGPT、Anthropic、GitHub 相关域名。

规则文件使用 jsDelivr CDN 地址：

```text
https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/{rule}.txt
```

出处：

- 规则集项目：[Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules)
- 规则数据主要来源：[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat) 和 [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)
- Apple / Google 域名来源：[felixonmars/dnsmasq-china-list](https://github.com/felixonmars/dnsmasq-china-list)
- 中国大陆 IPv4 数据来源：[17mon/china_ip_list](https://github.com/17mon/china_ip_list)

注意：`Loyalsoldier/clash-rules` README 标明这些 RULE-SET 面向 Clash Premium 内核；Mihomo 兼容 Clash Premium 规则能力，因此本项目模板默认面向 `target=mihomo`。

## powerfullz 覆写集成

`powerfullz` 模板集成了 [powerfullz/override-rules](https://github.com/powerfullz/override-rules) 发布的 Mihomo/SubStore 覆写规则。该项目面向 Mihomo/SubStore，提供包含 AI、TikTok、Telegram、加密货币、静态资源、广告拦截、国家/地区节点分组等场景的覆写配置。

本项目当前集成方式：

- 使用其预生成的静态 YAML 覆写文件，而不是执行 JS 动态覆写脚本。
- 按页面开关生成对应 YAML 文件名并从 jsDelivr 拉取。
- 将用户订阅解析出的 `proxies` 注入到该覆写配置中。
- 保留 powerfullz 模板中的 `include-all`、`filter`、`rule-providers`、`rules`、`dns` 等配置。
- 页面中的自定义分组策略仍可叠加到 powerfullz 模板上。

支持的页面参数：

- `loadbalance`
- `landing`
- `ipv6`
- `full`
- `keepalive`
- `fakeip`
- `quic`

静态 YAML 地址格式：

```text
https://cdn.jsdelivr.net/gh/powerfullz/override-rules/yamls/config_lb-{0|1}_landing-{0|1}_ipv6-{0|1}_full-{0|1}_keepalive-{0|1}_fakeip-{0|1}_quic-{0|1}.yaml
```

出处：

- 覆写规则项目：[powerfullz/override-rules](https://github.com/powerfullz/override-rules)
- JS 覆写 CDN：[https://cdn.jsdelivr.net/gh/powerfullz/override-rules/convert.min.js](https://cdn.jsdelivr.net/gh/powerfullz/override-rules/convert.min.js)
- 静态 YAML CDN 示例：[https://cdn.jsdelivr.net/gh/powerfullz/override-rules/yamls/config_lb-0_landing-0_ipv6-0_full-1_keepalive-0_fakeip-1_quic-0.yaml](https://cdn.jsdelivr.net/gh/powerfullz/override-rules/yamls/config_lb-0_landing-0_ipv6-0_full-1_keepalive-0_fakeip-1_quic-0.yaml)
- 上游规则来源包括：[SukkaW/Surge](https://github.com/SukkaW/Surge)、[217heidai/adblockfilters](https://github.com/217heidai/adblockfilters)、[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)

限制：

- powerfullz 官方更推荐 JS 动态覆写；本项目为了保持后端实现简单，当前只拉取静态 YAML。
- 静态 YAML 无法像 JS 覆写那样根据真实节点动态裁剪所有国家/地区分组，但 Mihomo 的 `include-all` 和 `filter` 会在运行时筛选节点。
- 如果开启 `landing`，而订阅中没有符合落地规则的节点，可能导致客户端无法正常启动；这是 powerfullz 原规则的使用注意事项。

## HenryChiao MRS 规则集成

`henrychiao-mrs` 模板集成了 [HenryChiao/mihomo_yamls](https://github.com/HenryChiao/mihomo_yamls/tree/ruleset) 的 `ruleset` 分支规则集。该规则集面向 mihomo/clash.meta 和 Stash，提供按 `domain`、`ipcidr`、`classical` 拆分的规则文件，并包含 Mihomo 独有的 `.mrs` 二进制格式。

本项目当前集成方式：

- 只引用上游 `meta/domain/*.mrs` 与 `meta/ipcidr/*.mrs` URL，不转载、不内置规则文件内容。
- 为常见场景内置分流组：`AI`、`Apple`、`Microsoft`、`Google`、`Git`、`Streaming`、`Bilibili`、`Social`、`Crypto`、`Games`、`CDN`、`Speedtest`、`PayPal`。
- IP 类 RULE-SET 默认追加 `no-resolve`。
- 保留页面自定义分组策略能力，可继续叠加自定义代理组。

规则 URL 格式：

```text
https://raw.githubusercontent.com/HenryChiao/mihomo_yamls/refs/heads/ruleset/meta/{domain|ipcidr}/{rule}.mrs
```

出处：

- 规则集项目：[HenryChiao/mihomo_yamls ruleset 分支](https://github.com/HenryChiao/mihomo_yamls/tree/ruleset)
- 上游鸣谢中列出的规则来源包括：[blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)、[MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat)、[SukkaW/Surge](https://github.com/SukkaW/Surge)、[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)、[ACL4SSR/ACL4SSR](https://github.com/ACL4SSR/ACL4SSR) 等。

注意：上游 README 标注“禁止任何形式转载或发布至中国大陆地区”。本项目仅在本地生成配置时引用公开规则 URL，不打包、镜像或再分发规则文件；请遵守上游项目的使用限制。

## 自定义分组策略

页面中的「分组策略」用于在模板生成后追加或替换 `proxy-groups`：

- 支持分组类型：`select`、`url-test`、`fallback`、`load-balance`。
- 成员每行一个，可以填写节点名、其他分组名、`DIRECT`、`REJECT`。
- 成员留空时，服务会自动把全部订阅节点注入该分组。
- 如果自定义分组与模板中已有分组同名，会替换原分组。
- 新增的自定义分组会自动加入 `PROXY` 选择组前部，方便在客户端里手动选择。

## 测试

```bash
uv run pytest
```

## Roadmap

- 支持 Base64 URI 订阅解析。
- 支持更多 Mihomo 策略模板。
- 增加模板参数化能力。
- 增加更严格的 SSRF 防护和重定向目标校验。
- 提供 Docker 镜像和部署示例。
