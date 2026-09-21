# Leo 策略模板

`leo.yaml` 是从原社区完整 YAML 及其 OpenClash 覆写版本归一化得到的单一策略模板，由 Subflow 编译为 Mihomo、Surge 配置，以及 Shadowrocket 的节点订阅和配套分流配置。客户端导入方法见[根目录说明](../../README.md#客户端输出)。

## 使用

1. 在 Subflow 页面填写自己的订阅地址，不要直接在模板中保存订阅 URL。
2. Subflow 会把当前订阅节点写入全局自动选择和手动选择组；默认出口只额外维护香港低延迟池，避免同一批节点被多个地区组重复测速。
3. `美国节点` 只匹配美国节点并保持手动选择；测速只使用通用 Cloudflare 204 探针更新连通性和延迟，不参与选路。没有匹配节点时，该组及父级引用会被自动删除；需要指定其他地区时使用全局手动选择。
4. 根据运行环境决定是否启用 `tun.enable`；可用 `python -m app.core.rule_source_audit` 复查远程 RuleProvider。

## 测速说明

- Mihomo/OpenClash 保留两套自动健康检查（全局自动、香港自动）和一套美国节点手动组检查。三组都使用 `https://cp.cloudflare.com/generate_204`，要求 HTTP 204，单节点等待上限为 5000 ms；`美国节点` 是 `select`，健康检查只更新可用性和延迟，绝不会按延迟自动切换。检查周期为 600 秒并保持 lazy：启动时会填充一次结果，之后仅在该组近期使用时继续检查。没有美国节点时仅保留手动选择，不会随全局测速自动换出口。
- OpenClash 的“URL-Test 地址修改”无需再为 AI 设置特殊地址；建议关闭覆写并直接使用模板的 Cloudflare 204 通用探针。若必须覆写，应保持地址与 HTTP 204 期望状态一致。
- Surge 兼容基线为 5.21+：原生 Surge 输入保留机场的 `[General] proxy-test-url` 和 `test-timeout`。Apple Provider 使用上游完整的 `Apple_All_No_Resolve.list`，避免基础列表漏掉域名规则。
- ChatGPT 的主站、WebSocket、静态资源、上传、`oaistatsig.com`、`cdn.openaimerge.com` 及官方列出的五个 WorkOS 登录/资源主机在模板中固定走 `AI 服务`，先于广义 Provider；依据是 [OpenAI 官方网络要求](https://help.openai.com/en/articles/9247338)（2026-09-12 核对）。WorkOS 与 imgix 仅按具体主机匹配，避免整个共享域名服务改走美国。2026-09-21 补充官方清单中的 `humb.apple.com`、Intercom、Stripe JS、两个 Sentry 主机、Datadog RUM 和 SendGrid 跟踪子域，防止被 Apple 或广告规则抢先处理。Stripe、Sentry、Datadog、Apple 均只例外匹配列出的主机；这些依赖中部分用于客服/遥测，放行不代表它们是每次对话失败的原因。
- OpenAI RulePack 与 RouteIntent 同样保留这些资源及 Cloudflare 挑战域名的出口。已有 Profile 若保存了旧的替换式 PolicySnapshot，需要重新从规则包生成并保存策略；只刷新旧快照的订阅不会自动合并新规则。
- ChatGPT 故障先核对 `AI 服务 → 美国节点` 的实际选择是否与可用原订阅为同一节点；Cloudflare 204 测速通过只说明探针可达，不证明 ChatGPT 可用。客户端保存的选择可能覆盖新配置首选；无美国节点时 `AI 服务` 只连接手动组，初始仍是订阅中的首个节点，必须手动选定实际可用节点。上述补全不改变 fake-ip，配置编译和路由回归也不替代同节点真实访问验证。
- 三个客户端均以机场原生连接配置为准，Leo 负责规则和必要策略组。Mihomo 保留完整公共设置和节点字段；原生 Surge 保留非分流段；Shadowrocket 原生订阅直接传递，完整原生配置只替换分流，只有节点时只补充分流配置。跨格式 Surge 无法等价表达节点专用 DNS 时返回 `unsupported_node_dns` 提示（不含订阅凭据）。兼容提示不证明 DNS 就是故障原因。
- `默认代理` 首选低延迟的香港池；Apple 与 Microsoft 均直连优先（两者都有国内数据中心，世纪互联 Azure/O365 的 `.cn` 端点经海外节点会被慢速或拒绝；Copilot 等已单独分流到 `AI 服务`）。Homebrew Formula API（`formulae.brew.sh`，托管在被 GFW 按 SNI 阻断的 GitHub Pages 段）固定走 `开发服务` 组。当前 8 个 RuleProvider 全部固定到 40 位提交，Mihomo 下载地址会改写到 canonical jsDelivr CDN 并明确固定为 `DIRECT`；Surge 产物也使用同一 CDN 上的原生 `.list`。冷启动不依赖尚未就绪的代理节点，也不再需要隐藏的规则更新组。`interval` 控制结果有效期，`tolerance` 控制切换阻尼，都不会缩短一次手动测速。轻量版删除了旧地区组和兼容别名；客户端若保存过这些组的选择，需要删除旧配置后重新导入。

## AI 使用与排错

- Claude 的 `anthropic.com`、`claude.ai`、`claude.com`、`claudeusercontent.com` 现在内联并先于远程来源和广告规则；覆盖 API、OAuth 刷新、下载、MCP、Chrome 桥接和 Artifact 内容。依据：[Claude Code 官方网络要求](https://code.claude.com/docs/en/network-config)（2026-09-21 核对）。共享 Google Storage、npm、GitHub 和 CDN 继续使用各自服务策略。
- 广义 `ai-4` 原本已有新的 Claude 域名，但旧服务目录/专用 Claude 源没有：指定 Claude 节点时，API 与新登录/内容域名可能仍使用不同出口。现在目录、RulePack、旧 Claude 覆写及独立模板一起同步；不能仅靠广义 AI 列表证明服务覆写完整。
- 建议在工作台分别给 ChatGPT / OpenAI 和 Claude 设置 **固定节点**，用实际可用性选择节点；通用 204 延迟不能识别服务端 403。默认 `AI 服务` 仅保留美国手动组和全局手动组。修改全局手动组会影响使用它的服务，固定 ServiceRoute 可避免这种联动。
- 更新部署后刷新客户端订阅，并核对实际选择；`store-selected` 可能保留旧选择。旧 PolicySnapshot 需在工作台预览升级后保存，现代 ServiceRoute 会读取最新目录。无需清除所有应用数据。
- 不再把 3478、5349、19302、10000、5350 端口一律直连，防止语音或其他应用绕过分流。具名 STUN 主机例外保留；无域名裸 IP 的语音仍按地理和最终策略处理，不能保证与 AI 域名的固定节点一致。节点不支持 UDP 时，应检查客户端/应用的 TCP 回退。
- `tun.enable` 默认为 false。终端里的 Claude Code 若未经过透明代理，需要显式连接客户端的 HTTP 代理端口（本机 Mihomo 默认 7890），然后重启该 Claude Code 会话：

  ```bash
  export HTTPS_PROXY=http://127.0.0.1:7890
  export HTTP_PROXY=http://127.0.0.1:7890
  export NO_PROXY=localhost,127.0.0.1,::1
  claude
  ```

  路由器部署时改成实际代理地址。已有小写 `https_proxy` / `http_proxy` 时也要核对，避免它们优先指向旧代理；不要把 AI 域名加入 `NO_PROXY`。Claude Code 官方不支持 SOCKS 代理；这里使用 mixed-port 的 HTTP 接口。
- 出现 `403` / `Access denied` 时，先用同一节点对照原订阅，并核对 `/diagnose` 可获取的运行时选择。若连通后仍被拒绝，应保留脱敏错误信息继续定位；本次规则测试不证明出口 IP 被服务接受。中途断流还需检查代理是否支持长连接/WebSocket，以及是否存在 TLS 检查或上游空闲超时。

## 合并原则

- 扫描完整配置：59 份。
- 原始规则：2103 条；当前模板收敛为 146 条，其中仅 8 条 `RULE-SET`。OpenAI 与 Claude 核心域名及登录依赖内联以保证客户端主链路；Telegram 使用一份同时覆盖域名与无域名 IP 流量的 classical 规则源。
- 初始远程规则源 716 个；内容审计先压缩到 161 个，本轮按 ADR 0011 的 intent-based consolidation 只保留 AI/Claude、GitHub、Apple、Google、Microsoft、YouTube 和 Telegram 八个核心来源。长尾服务使用 Mihomo 内置 GEOSITE/GEOIP 或最终默认代理，不再为每个小站点单独下载列表。
- 同名但定义冲突的社区策略组没有机械拼接，而是映射到统一的地区、服务和兜底策略组。
- 广告类规则映射到 `REJECT`，国内和网络基础规则映射到 `DIRECT`，其余规则映射到对应服务组。
- 规则保持 first-match 语义：启动所需直连规则和核心服务在前，内置广告拦截与服务兜底居中，广谱中国大陆/私网直连规则在具名服务的域名和 Provider 规则之后，最终流量固定回到 `默认代理`。
- 游戏、社交和 Google 推送等专用分类先于 `gfw/geolocation-!cn`，防止自定义服务出口被广谱代理集合覆盖。未分类流量统一交给末尾 `MATCH`；不再使用 `in_mixed` 入站名称或 `10000-65535` 高端口提前代理，国内高端口站点和局域网 NAS 可以继续匹配直连规则。

## 当前质量基线

- 2026-09-21 公开审计快照与模板 SHA 匹配：8 个来源全部可用，0 个格式无效，0 个完整重复组，0 个高重叠对。审计工具使用客户端等效 UA（`clash.meta/1.18.0 (subflow-rule-audit)`），避免把来源的 UA 白名单误判为不可用。
- 结构评分公式 v2（含供应链与冷启动维度）：99.99/100（A）。当前只有 2 个可信上游、0 个第三方代理中转、0 个不可固定版本来源；冷启动规则下载量为 115,603 B，较 161 源快照减少约 96.1%。
- 轻量回归预算：RuleProvider 不超过 8 个、规则不超过 150 条、模板不超过 13 KiB（调整依据见 [ADR 0015](../../docs/adr/0015-ai-session-routing-and-inline-budget.md)）；固定 144 节点 SS 策略夹具最多 14 个组、2 个自动选择组、1 个带连通性测试的手动组、380 条组成员边、200 条潜在探针成员边，渲染结果不超过 34 KiB。34 KiB 保留了全局自动回退；同一夹具在精简前超过 84 KiB。真实节点若带 TLS/transport 等字段，输出字节数会更大，验收以结构预算与真实编译为准。
- 评分不替代语义准确率、覆盖率、长期新鲜度和内容漂移验证。
- 当前没有 MRS-only 依赖；7 个 classical YAML 核心来源可转换为 Surge 原生列表，`ai-4` 是 Mihomo 的 domain 裸列表，Surge 会明确跳过并给出 warning。OpenAI 官方网络清单与 Claude 核心域名已内联，Telegram 的原生 Surge 列表覆盖域名与 IP；其他 Mihomo 专属规则仍以生成结果中的 warning 为准，公开接口会把模板标记为非完全兼容。
- 已知边界例外：固定版本的 Google/YouTube classical 上游分别夹带 5/3 条 IP 规则。审计快照会通过 `rule_type_counts` 公开它们；当前来源和外层 `RULE-SET` 均使用 `no-resolve`，可防止为匹配这些规则而主动解析域名，但原始 IP 或已解析请求仍可命中。这是对严格“共享基础设施不做 IP 层服务分流”边界的已知技术债，不是新 RuleSource 的准入先例；彻底移除需要发布经审计的 Mihomo/Surge 等义纯域名双版本。

## 规则源准入清单

新增或替换 `rule-providers` 条目前，逐项确认；不满足的项必须在提交说明中给出理由：

1. **上游可信**：优先使用已在模板中的上游（`MetaCubeX`、`blackmatrix7`、`DustinWin`、`ruleset.skk.moe` 等）。引入新上游即引入一个能向所有客户端注入规则的主体，需单独说明其维护状况。
2. **直连来源**：URL 指向源站或声明了上游的官方 CDN（jsDelivr `gh/<owner>@<ref>`）。不使用 `gh-proxy` 类第三方代理前置——中间人可改写规则内容；镜像加速需求用 `provider_egress` 的代理下载解决，而不是换 URL。
3. **可固定版本**：能 pin 到 commit SHA 或 tag 的优先 pin。只能追踪分支的（`refs/heads/`、`@master`）记为已知风险，依赖定期审计的内容摘要（`sha256`）对比发现漂移。
4. **成本合理**：单源体积与其被引用的规则数相称。审计报告中 `byte_count` 大而 `routes` 少的源是合并候选，不是新增样板。总量受两个预算约束：来源数 200（`policy_analyzer` 同源）、冷启动总下载 16 MiB。
5. **不与现有源高重叠**：加入前跑 `python -m app.core.rule_source_audit`，确认不出现在 `high_overlap_pairs`（≥95% 重叠）或 `duplicate_content_groups` 中；同语义内容用已有源，不加副本。
6. **目标一致**：新源路由到的策略组不得与已有源对同一条目产生 `REJECT <> DIRECT` 类冲突；冲突见审计报告 `ordered_entry_conflicts.risk_directions`。
7. **IP 规则边界**：`ipcidr` 源只允许用于确实存在无域名裸 IP 流量的服务（Telegram、Discord 语音），且规则必须带 `no-resolve`。共享基础设施服务（AI、Google、流媒体）禁止 IP 层分流——它们的前端 IP 承载多个无关服务，可解析的 IP 规则会把同一页面劈到两个出口（曾导致 YouTube 图片经 AI 节点加载失败）。地理/私网兜底（China IP、private IP → DIRECT）豁免。

删除来源分两条路径：availability-based 删除仍只接受跨多轮审计确认不可用的来源（`apply_verified_unusable_source_pruning`），单轮抓取失败不构成删除理由；intent-based consolidation 按 ADR 0011 的服务排名、关键域名回归与 Mihomo/Surge 双产物证据执行，即使来源仍可下载也可因重复或低价值而合并。

## 服务目录同步（5.0）

`services.json` 是产品服务覆盖、旧 RulePack 和 Leo 内嵌服务规则的共同来源。`template_inline` 仅标识需要写入独立 Leo YAML 的条目，`rule_providers` / `geosite_tags` 标识显式服务覆盖时应同步重定向的专用来源；共享 Google/AI 来源不会整体改道。

修改后运行 `uv run python scripts/sync-service-rules.py`，再重新发布规则源审计；测试中的 `--check` 会拦截漏同步。新 Profile 仅保存出口偏好，后续编译读取当前服务目录。旧 PolicySnapshot 保持原内容，需在工作台预览并保存升级。
