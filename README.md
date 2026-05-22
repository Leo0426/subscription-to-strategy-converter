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

- `minimal`：最小策略，核心组 Proxy / Auto / Fallback / DIRECT。
- `developer`：开发者策略，GitHub、npm、Docker、JetBrains、Microsoft、Apple 独立分流。
- `ai-tools`：AI 工具策略，Claude、OpenAI、Gemini、Perplexity、Cursor、GitHub Copilot 独立分流。
- `streaming`：流媒体策略，Netflix、YouTube、Disney、Spotify、Telegram 独立分流。
- `full`：全量策略，AI + Developer + Streaming + 地区自动筛选（HK / SG / JP / US）。
- `powerfullz`：基于 powerfullz/override-rules 静态 YAML 覆写，支持按需开关负载均衡、IPv6、Fake-IP 等。

本地模板：

- 服务会自动扫描 `community_templates/THEYAMLS/**/*.yaml`。
- 能被解析为 YAML 对象且包含 `proxy-groups` 的文件会出现在页面模板下拉框中。
- 本地模板 ID 形如 `local:community_templates/THEYAMLS/General_Config/666OS/OneTouch_Config.yaml`。
- 生成配置时会向本地模板注入订阅解析出的 `proxies`。
- `PROXY`、`AUTO`、`手动选择`、`全球手动`、`全部节点`、`节点选择` 等常见入口分组会自动补入节点名。

暂不自动加载：

- `community_templates/Overwrite/THEOPENCLASH/**/*.conf`：OpenClash 覆写模块格式，不是完整 YAML 配置。
- `community_templates/Overwrite/THENEWOPENCLASH/**/*.yaml`：新版 OpenClash `[YAML]` 块覆写片段，不适合作为完整订阅配置输出。
- `community_templates/Overwrite/THEINI/**/*.ini`：subconverter 配置格式，不是 Mihomo YAML。

模板列表 API：

```bash
curl http://127.0.0.1:8000/templates
```

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
- 上游规则来源包括：[SukkaW/Surge](https://github.com/SukkaW/Surge)、[217heidai/adblockfilters](https://github.com/217heidai/adblockfilters)、[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)

限制：

- powerfullz 官方更推荐 JS 动态覆写；本项目为了保持后端实现简单，当前只拉取静态 YAML。
- 静态 YAML 无法像 JS 覆写那样根据真实节点动态裁剪所有国家/地区分组，但 Mihomo 的 `include-all` 和 `filter` 会在运行时筛选节点。
- 如果开启 `landing`，而订阅中没有符合落地规则的节点，可能导致客户端无法正常启动；这是 powerfullz 原规则的使用注意事项。

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
- 增加 Mihomo 编译器 golden-output 测试。
- 增加模板参数化能力。
- 增加更严格的 SSRF 防护和重定向目标校验。
- 提供 Docker 镜像和部署示例。
- 多平台编译器（Surge、sing-box）达到与 Mihomo 同等的语义覆盖后移出实验状态。
