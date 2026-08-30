# Leo 策略模板

`leo.yaml` 是从原社区完整 YAML 及其 OpenClash 覆写版本归一化得到的单一策略模板，由 Subflow 分别编译为 Mihomo 和 Surge 配置。

## 使用

1. 在 Subflow 页面填写自己的订阅地址，不要直接在模板中保存订阅 URL。
2. Subflow 会把当前订阅节点写入全局自动选择和手动选择组；默认出口只额外维护香港低延迟池，避免同一批节点被多个地区组重复测速。
3. `AI自动` 只匹配新加坡节点并验证 ChatGPT 可达性；没有匹配节点时，该组及父级引用会被自动删除。需要指定其他地区时使用手动选择。
4. 根据运行环境决定是否启用 `tun.enable`；可用 `python -m app.core.rule_source_audit` 复查远程 RuleProvider。

## 测速说明

- Mihomo/OpenClash 只保留三套健康检查：全局自动、香港自动和 AI 新加坡。通用自动组使用 `https://cp.cloudflare.com/generate_204`，要求 HTTP 204，单节点等待上限为 5000 ms，连续两次实际连接失败会强制复测；组保持 lazy，未被使用时不会继续周期测速。`AI自动` 不嵌套其他测速组，并以 `https://chatgpt.com/cdn-cgi/trace` 验证 ChatGPT TLS 可达性；实际连接失败一次就会强制复测。没有新加坡节点时，`AI 服务` 回退到默认、全局自动和手动策略。
- OpenClash 的“URL-Test 地址修改”会覆写模板中的探针地址；若已开启，应改成同一个 Cloudflare 204 地址，或关闭覆写后重新生成运行配置。
- 当前 Surge 版本使用 `[General] proxy-test-url` 而不是策略组中的旧 `url=`。Subflow 的 Surge 产物统一使用 Apple 轻量探针，并设置 `test-timeout = 3`。
- `默认代理` 首选低延迟的香港池；Apple 与 Homebrew Formula API 保持直连优先。当前 8 个 RuleProvider 全部固定到 40 位提交，Mihomo 下载地址会改写到 canonical jsDelivr CDN 并明确固定为 `DIRECT`；Surge 产物也使用同一 CDN 上的原生 `.list`。冷启动不依赖尚未就绪的代理节点，也不再需要隐藏的规则更新组。`interval` 控制结果有效期，`tolerance` 控制切换阻尼，都不会缩短一次手动测速。轻量版删除了旧地区组和兼容别名；客户端若保存过这些组的选择，需要删除旧配置后重新导入。

## 合并原则

- 扫描完整配置：59 份。
- 原始规则：2103 条；当前模板收敛为 138 条，其中仅 8 条 `RULE-SET`。OpenAI 核心域名内联以保证 Surge 主链路；Telegram 使用一份同时覆盖域名与无域名 IP 流量的 classical 规则源。
- 初始远程规则源 716 个；内容审计先压缩到 161 个，本轮按 ADR 0011 的 intent-based consolidation 只保留 AI/Claude、GitHub、Apple、Google、Microsoft、YouTube 和 Telegram 八个核心来源。长尾服务使用 Mihomo 内置 GEOSITE/GEOIP 或最终默认代理，不再为每个小站点单独下载列表。
- 同名但定义冲突的社区策略组没有机械拼接，而是映射到统一的地区、服务和兜底策略组。
- 广告类规则映射到 `REJECT`，国内和网络基础规则映射到 `DIRECT`，其余规则映射到对应服务组。
- 规则保持 first-match 语义：启动所需直连规则和核心服务在前，内置广告拦截与服务兜底居中，广谱中国大陆/私网直连规则在具名服务的域名和 Provider 规则之后，最终流量固定回到 `默认代理`。

## 当前质量基线

- 2026-08-30 公开审计快照与模板 SHA 匹配：8 个来源全部可用，0 个格式无效，0 个完整重复组，0 个高重叠对。审计工具使用客户端等效 UA（`clash.meta/1.18.0 (subflow-rule-audit)`），避免把来源的 UA 白名单误判为不可用。
- 结构评分公式 v2（含供应链与冷启动维度）：99.99/100（A）。当前只有 2 个可信上游、0 个第三方代理中转、0 个不可固定版本来源；冷启动规则下载量为 115,548 B，较 161 源快照减少约 96.1%。
- 轻量回归预算：RuleProvider 不超过 8 个、规则不超过 140 条、模板不超过 12 KiB；固定 144 节点 SS 策略夹具最多 14 个组、3 个健康检查组、380 条组成员边、200 条潜在探针成员边，渲染结果不超过 34 KiB。34 KiB 保留了全局自动回退；同一夹具在精简前超过 84 KiB。真实节点若带 TLS/transport 等字段，输出字节数会更大，验收以结构预算与真实编译为准。
- 评分不替代语义准确率、覆盖率、长期新鲜度和内容漂移验证。
- 当前没有 MRS-only 依赖；7 个 classical YAML 核心来源可转换为 Surge 原生列表，`ai-4` 是 Mihomo 的 domain 裸列表，Surge 会明确跳过并给出 warning。OpenAI 四个核心后缀已内联，Telegram 的原生 Surge 列表覆盖域名与 IP；其他 Mihomo 专属规则仍以生成结果中的 warning 为准，公开接口会把模板标记为非完全兼容。

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
