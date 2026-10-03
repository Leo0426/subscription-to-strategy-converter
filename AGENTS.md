# 仓库协作约定

使用与部署写在 [README](README.md)，协作和技术边界写在本文件。可从配置、API 或代码查到的细节不另建文档；修改后运行相应验证，文档变更检查引用。方案与下列约束冲突时明确说明。

## 本地任务

- `.scratch/<feature>/PRD.md` 保存需求，`issues/<NN>-<slug>.md` 保存工单，从 01 编号；Skill 要求发布到 issue tracker 时写入这里，按用户给出的路径或编号读取。
- 工单头部记录 Status 和 Labels。类别为 bug/enhancement；状态为 needs-triage、needs-info、ready-for-agent、ready-for-human、wontfix、resolved。
- 新工单 needs-triage，补齐信息回到 needs-triage，只有 ready-for-agent 可领取；完成后 resolved，wontfix/resolved 为终态。评论追加在「评论」下。
- 标题、测试和设计使用领域术语，工单说明所解决的配置维护、规则、兼容、依赖或诊断问题。真实来源、token、节点凭据和数据库只留在本地数据目录。

Wayfinding 使用 MAP.md（wayfinder:map），子任务类型为 wayfinder:research/prototype/grilling/task。用 Assignee 认领、Blocked-by 记录依赖；按编号领取无阻塞且未分配的开放任务，完成时记录 Resolution 并更新 MAP 的 Decisions so far。

## 产品与领域

授权来源 → 节点/原生配置 → Leo + 服务偏好 → PolicyWorkspace 检查/编译 → Profile 订阅。产品是个人自托管的单页工作台，Mihomo 为语义基线，Surge iOS/Shadowrocket 为兼容目标；协议适配器只负责显式启用的 node-only 输入转换。

| 术语 | 含义 |
| --- | --- |
| ProxyNode / NodeSelector | 节点 IR / 对当前清单的命名查询，引用为 selector:<id>。 |
| Profile / ServiceRoute | 一个来源和多客户端产物 / fixed 单出口、manual 手选、fallback 两个明确节点；缺省跟随 Leo。 |
| PolicyWorkspace / PolicyWorkbench | 策略分析编译 IR / 来源、编辑、检查、发布和账本的一页界面。 |
| ProxyGroup / RuleProvider / RuleSource | 节点策略组 / 外部集合引用 / 带格式、版本和摘要的规则输入。 |
| PolicySnapshot | 已保存的完整 SelectedPolicy；工作台升级需要显式重建。 |

其余模型以 app/ir.py 和 app/models/ 为准；新增服务行为扩展 ServiceRoute，不增加另一套 Profile 或发布流程。

## 实现边界

- **来源设置**：机场拥有 DNS、Hosts、端口、TUN、节点字段与原名；保留原生缺省和非分流段，转换不修改来源对象。Clash 未建模字段私有透传；跨格式无法等价表达时明确提示，不扩大设置作用域或自动改变 TLS 验证。
- **依赖与名称**：Mihomo 保留连接和新策略的可达传递依赖，先裁剪再处理生成项重名。动态外部节点可能隐藏依赖时保守保留并提示，不新增下载推断。Surge 保留原组/别名，生成重名项及引用同步改名，移除来源 managed-update 指令；名称歧义报错。
- **Shadowrocket**：以目标身份读取来源，节点原文不重写、不按跨格式协议过滤。完整 INI 保留非分流段，节点来源只生成规则/组；元数据放响应头。节点/配置链接同 token、独立产物、一起刷新。
- **模板与编译**：公共模板只接受 local:community_templates/leo/leo.yaml，社区只读使用 community:leo/leo.yaml。Mihomo 经 PolicyWorkspace 编译，预览只物化来源一次；再编译保留 provider egress/警告。IR 缺少原生上下文或节点往返有损时要求 render/订阅接口。
- **规则语义**：未改规则保留 raw，修改后用结构化字段；RULE-SET match/provider 一致，含逻辑嵌套的引用全部可解析。MATCH/FINAL 终止匹配，后续规则报告不可达。精确域名/窄后缀先于宽后缀，具名服务先于广谱集合，China/private 直连后才是最终代理；通用端口/入站规则不得抢先。
- **服务覆写**：只重定向服务拥有的 Provider/GEOSITE，保留无关规则和来源。现代偏好读当前目录；旧快照显式预览升级，旧 Claude 不识别策略或直接转换不兼容时失败。旧 merge 追加、replace 全量替换。
- **兼容处理**：surge 明确指 iOS；不支持的规则/节点跳过并提示，分析与编译共用能力集。RULE-SET 用已审计的完整原生 URL，未知映射报错。不可用组清理引用，直指规则变 REJECT；固定出口引用不兼容节点、未知/空 NodeSelector 阻断。
- **AI 与探针**：AI 保持手动出口，fallback 只切换两个明确节点。通用测速不证明服务接受出口或授权跨节点切换；Mihomo 用 HEAD 验证 URL/expected-status。Surge 协议偏好只过滤 url-test 的节点，保留嵌套组和手动节点。共享 CDN 仅按精确服务主机例外处理。
- **INI 兜底**：无法表达 GEOSITE,cn 时允许中国 GEOIP 解析域名，Surge FINAL 加 dns-failed。不可加载的广义 AI 来源在原位置用目录域名替代、去重，保持优先级。
- **Profile 与缓存**：SQLite 保存当前意图和每目标最后成功产物，token 只存哈希，不建 Release/ProfileRevision 历史。写入检查所有声明目标及默认目标，硬错误阻断，策略环拒绝；预览不写库，编辑保留链接。保存递增 generation、清缓存并阻止旧请求写回；新鲜缓存须同代次/目标/编译标识，始终鉴权。
- **失败边界**：仅外部依赖失败允许同 generation/目标的旧产物回退，标记 stale 并保留真实标识/时间；无效来源、解析/编译或鉴权失败不回退。强刷跳过新鲜复用但可共享进行中任务。
- **网络与诊断**：公开来源的 URL/DNS 校验绑定实际连接，重试/重定向重检，保留 Host/TLS/来源 Cookie 作用域。原参数不改写，Shadowrocket 专用 UA 优先。Runtime 只使用服务器配置的控制器/CLI，读取选择/规则并探测，不改选择、不接受浏览器任意端点或凭据，也不证明手机状态或完整登录成功。

## 规则维护

[services.json](community_templates/leo/services.json) 是服务覆盖和探针的唯一事实源；改动后同步 Leo、验证并更新审计：

```sh
uv run python scripts/sync-service-rules.py
uv run python scripts/sync-service-rules.py --check
uv run python -m app.core.rule_source_audit --publish
```

- 可信上游以 [rule_consolidation.py](app/core/rule_consolidation.py) 为准，使用源站或官方 CDN，优先固定版本，不使用第三方代理前置。下载出口由 provider_egress.py 统一判定。新源审计语法/格式、重复或 ≥95% 高重叠、REJECT/DIRECT 冲突与成本；HTTP 200/评分不能证明规则正确。
- IP 服务规则只用于无域名裸 IP 流量，带 no-resolve；共享基础设施不做 IP 服务分流，地理/私网 DIRECT 豁免。Google/YouTube 既有 IP 条目属技术债，两层 no-resolve 不能成为新增先例。
- 来源合并按双目标可用性、可信度、覆盖、成本排名；保留代表域名目标，Mihomo/Surge 均编译，更新审计并按可回退批次落地（原 ADR 0011）。按可用性删除需要多轮失败；增加同服务来源须说明理由。
- 通用预算：200 来源、16 MiB 冷启动。Leo：9 Provider（国内域名兜底见 docs/adr/0017-surge-ios-domestic-domain-fallback.md）、185 规则、16 KiB 模板；固定 144 节点 SS 夹具：17 组、6 健康检查组、425 成员边、235 探针成员、37 KiB 输出。超预算须明确决策和冷启动证据。
