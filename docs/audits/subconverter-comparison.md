# Subconverter 与 Subflow 实现审计

审计对象：

- 参考仓库：`/Users/leolu/Projects/github_projects/subconverter`
- 当前仓库：`subscription-to-strategy-converter`

参考快照为 Git commit `5b8d3af`，源码版本宏为 `v0.9.0`。结论针对该本地快照，不外推到未审计的新版本。

## 结论

两者不应做完整功能对齐。Subconverter 的深模块是“多协议输入与多客户端输出的兼容转换器”；Subflow 的深模块应是“策略组装、语义分析与可发布订阅”。可用方案是在输入边界复用 Subconverter，把它限制为可选的节点归一化 Adapter，而不是让其模板、规则和输出参数渗入产品核心。

本次已打通的最小闭环：

1. Clash/Mihomo YAML 与 Surge 配置仍由 Subflow 直接、安全地读取。
2. Base64 或 URI 节点订阅在显式配置 `SUBFLOW_SUBCONVERTER_URL` 后，回退到 Subconverter 的 node-only Clash 输出。
3. 所有输入随后统一进入 `ProxyNode → Leo Template → PolicyWorkspace → Mihomo/Surge`。
4. Clash 中暂未建模的节点参数被透传，避免 WireGuard、Hysteria 以及客户端新增字段被静默删除。

## 核心调用链

| 阶段 | Subconverter | Subflow | 取舍 |
|---|---|---|---|
| HTTP 入口 | `main.cpp` 注册 `/sub` | FastAPI `/preview`、`/render`、`/profiles`、`/subscribe` | Subflow 保留产品 API |
| 输入获取 | `addNodes()` 内部抓取并解析多个 URL | `fetch_subscription()` 先做 URL、DNS、重定向和 SSRF 检查 | 直接支持的输入优先走 Subflow |
| 节点解析 | `explodeSub()` 识别 Clash、Surge、Base64 与 URI | Clash/Surge parser；失败时可选调用 `/sub?target=clash&list=true` | 协议变化隔离在 Adapter 后 |
| 节点模型 | 大型 `Proxy` struct | 小型公共 `ProxyNode` IR + 未建模 Clash 字段透传 | IR 服务策略核心，透传服务兼容性 |
| 节点处理 | 过滤、重命名、Emoji、排序、脚本 | 规范化、去重、NodePool/Selector | 不恢复高认知成本的参数面板 |
| 策略生成 | INI/外部 base + ruleset + custom groups | Leo Template + RulePack/RouteIntent + PolicyWorkspace | Subflow 是唯一策略所有者 |
| 输出 | Clash、Surge、QuanX、Loon 等多目标 | Mihomo 质量基线；Surge 兼容目标 | 不复制多目标生成器矩阵 |

## 关键发现

### 1. P0：未建模节点字段曾被静默丢弃（已修复）

`clash_to_ir()` 只提取少量已知字段，`ir_to_clash_dict()` 再从头构造节点。WireGuard 密钥、`reserved`、`mtu`，以及 VLESS 的 `client-fingerprint`、`packet-encoding` 等字段会消失，生成文件结构合法但节点不可连接。

修复采用“结构化核心字段 + Clash passthrough”契约。已建模字段仍由 IR 管理，未建模字段局部保存在节点内部并在 Mihomo 输出时恢复。

### 2. P1：实际机场订阅兼容面不足（已修复为可选能力）

Subflow 原先只接受 Clash YAML 和 Surge `[Proxy]`。Subconverter 的 `explodeSub()` 还识别 Base64 节点列表、SS/SSR、VMess、Trojan、Hysteria2 等 URI。现在非 Clash/Surge 内容可以经可选 Adapter 归一化，但默认部署不增加外部依赖。

### 3. P1：Subconverter 不适合直接暴露为公网服务

参考实现的网络层关闭了 TLS peer/host 校验，且非 API mode 还注册通用抓取接口；其请求日志也可能包含带 token 的源订阅 URL。部署时必须只暴露在 Compose 内部网络，不映射 `25500` 到宿主机或公网，并限制日志访问。

### 4. P1：参考实现的现代协议覆盖也不是绝对基线

本地版本的 `ProxyType` 与 URI dispatcher 覆盖 SS/SSR、VMess、Trojan、WireGuard、Hysteria/Hysteria2 等，但没有形成 VLESS/TUIC 的同等 URI 支持承诺。因此：现代 Clash YAML 优先由 Subflow 直接读取；Adapter 只补充兼容性，不作为无损性的唯一保证。

### 5. P2：Subflow 的运行时成本主要在规则集，而非节点转换

Leo 当前包含 508 个 RuleProvider。即使节点转换正确，受限路由器仍可能在冷启动下载规则时超时。现有 analyzer/provider-egress 改动已给出数量、MRS 内核版本和 GitHub 下载出口提示，但后续仍应把“精简 RulePack 对应的实际 provider 集”作为独立性能工作处理。

## 架构深化候选

| 优先级 | 候选 | 状态 | 收益 | 代价/风险 |
|---|---|---|---|---|
| 1 | 可选 ProtocolCompatibility Adapter | 本次完成 | 小接口隔离协议变化；恢复 Base64/URI 输入 | 多一次上游抓取；源 URL 会传给可信 sidecar |
| 2 | Clash 未建模字段透传 | 本次完成 | 新协议字段不再因 IR 不认识而丢失 | 这些字段无法被策略层理解或校验 |
| 3 | RulePack 驱动 provider 裁剪 | 待办 | 显著降低启动 I/O 与失败面 | 必须证明规则依赖闭包正确 |
| 4 | Release/Revision/rollback 实体化 | 待办 | 发布可复现、可回滚 | 数据迁移和 UI 工作量较高 |

方法论映射：候选 1 对应 U2/U4/U5（Deep Module、隐藏变化、依赖抽象）；候选 2 对应 U1/U4（降低数据丢失风险、局部兼容）；没有恢复旧版 Subconverter 参数 UI，因为它会扩大 Interface 并把协议细节泄露到产品层。

## 验证边界

- 自动测试覆盖 Adapter opt-in、node-only 请求、错误上限、回退链路，以及 WireGuard/VLESS 字段往返。
- 未使用真实私有订阅执行端到端联网测试；该验证需要操作者提供已授权的 URL。
- `latest` 镜像便于首次运行但不可复现；稳定部署应通过 `SUBCONVERTER_IMAGE` 指定经过验证的固定 tag 或 digest。
