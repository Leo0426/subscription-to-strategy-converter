# Traffic Policy Control Plane MVP PRD

## 问题陈述

当前项目已经能把用户授权访问的订阅转换成 Mihomo/Clash 可用配置，但用户真正痛苦的不是“生成 YAML”，而是代理策略长期维护失控：规则散落在 GitHub、provider 依赖不可见、策略组关系难理解、规则顺序和覆盖关系无法分析，最终导致配置变成不可审计、不可回滚、不可协作的文本堆。

本项目的新定位是 Traffic Policy Control Plane：以统一 IR 为核心，把订阅节点、规则 provider、规则、策略组和目标平台渲染拆开管理，让用户可以设计、组合、分析、模拟、编译和发布代理策略。

## MVP 目标

MVP 只支持 Mihomo，证明项目能从“订阅转换器”升级为“可分析的策略编排系统”。

MVP 成立标准：用户导入一个 Clash/Mihomo 订阅，选择或引用一组规则 provider，在可视化策略图中看到规则、策略组、节点和 provider 的依赖关系，运行基础规则分析和域名流量模拟，最后导出可用的 Mihomo YAML。

## 目标用户

- 主要用户：长期维护个人或小团队代理配置的高级用户、开发者、机场订阅使用者。
- 次要用户：规则集维护者、希望发布可复用策略模板的社区作者。

## 解决方案

系统以 Policy Workspace 为用户入口。一个 workspace 包含订阅节点、规则 provider、策略组、规则顺序、目标平台和编译输出。

用户不直接编辑大段 YAML，而是通过规则市场引用 provider，通过 Group Builder 组合策略组，通过 Visual Policy Graph 理解流量路径，通过 Analyzer 发现明显错误，通过 Traffic Simulator 验证域名会走向哪个策略组，最后由 Mihomo Renderer 编译输出 YAML。

## 用户故事

1. 作为一个个人代理配置维护者，我想引用 OpenAI、Claude、Netflix 等规则包，以便不用从多个 GitHub 仓库复制规则。
2. 作为一个高级用户，我想看到规则、provider、策略组和节点之间的 DAG，以便理解某条流量最终走向哪里。
3. 作为一个配置维护者，我想在导出前发现缺失 provider、重复规则、不可达规则和策略组循环，以便避免客户端启动或运行异常。
4. 作为一个订阅用户，我想输入 `chat.openai.com` 并看到命中链路，以便确认 OpenAI 流量会走预期策略组。
5. 作为一个 Mihomo 用户，我想把 workspace 编译成标准 YAML，以便继续使用现有客户端。

## 范围

### 范围内

- Mihomo 作为唯一 MVP 输出目标。
- 基于现有 Clash YAML 订阅解析能力，继续生成 ProxyNode IR。
- 扩展统一 Policy IR，覆盖 rule providers、rules、proxy groups、routing edges 和 compile target。
- Rule Provider Marketplace 的本地 catalog 版本，优先收纳已有模板和常见上游规则源。
- Visual Policy Graph 的只读 MVP：展示规则、provider、策略组、节点和内置 target 的关系。
- Rule Analyzer 的基础检查：missing provider、missing group target、duplicate rule、group cycle、unreachable group。
- Traffic Simulator 的基础域名匹配：DOMAIN、DOMAIN-SUFFIX、DOMAIN-KEYWORD、RULE-SET 元数据级模拟、MATCH fallback。
- Mihomo YAML Renderer，保留现有模板注入能力。
- 导出前展示 analyzer 结果和 simulator trace。

### 范围外

- Surge、sing-box、Quantumult X、Loon、OpenClash package 的完整编译支持。
- 多用户 workspace、RBAC、审核、审计和 GitOps 发布流水线。
- 运行时真实流量观测、Prometheus/Grafana、客户端 tracing。
- AI 规则生成、AI 规则解释、AI 冲突检测。
- 完整 DSL 和 AST 编辑器。
- 规则市场远程账号体系、下载统计、签名和私有 registry。

## 实现决策

- 主要 Module：
  - Source Layer：订阅加载、模板扫描、provider catalog 收集。
  - IR Layer：ProxyNode 之外新增 Policy、RuleProvider、PolicyRule、ProxyGroup、PolicyEdge。
  - Graph Layer：从 Policy IR 派生只读 DAG，前端可用同一 JSON 渲染。
  - Analysis Layer：对 Policy IR 运行确定性检查，输出 severity、code、message、path。
  - Simulation Layer：输入 host/IP，按 Mihomo 规则顺序输出 rule trace 和 target trace。
  - Compiler Layer：Policy IR 编译为 Mihomo dict，再通过现有 YAML renderer 输出。
- 关键 Interface：
  - `POST /workspace/preview`：把订阅、模板、provider 选择编译成 workspace preview。
  - `POST /analyze`：输入 policy IR，输出 analyzer findings。
  - `POST /simulate`：输入 policy IR 和 destination，输出匹配链路。
  - `POST /compile/mihomo`：输入 policy IR，输出 YAML。
- 数据或 API 契约：
  - IR 必须是 JSON serializable，前后端共享同一结构。
  - Analyzer finding 必须稳定包含 `code`，方便 UI 分类和测试断言。
  - Simulator trace 必须保留每一步的 rule index、rule type、target 和 decision。
- 架构约束：
  - 先用 Python 实现 MVP，复用现有 FastAPI、Pydantic、ruamel.yaml 和测试体系。
  - IR 与 renderer 解耦，避免把 Mihomo YAML 字段泄漏到所有模块。
  - Rule provider 内容可先不全量下载，MVP 可用 provider 元数据和规则名称做依赖分析。
  - 订阅安全边界沿用现有 URL 校验和不存储策略。
- 已有 ADR 对齐：
  - 当前仓库暂无 ADR；本 PRD 作为第一份产品架构输入。

## 测试决策

- 关键路径：
  - Clash YAML 订阅解析为 ProxyNode。
  - 模板和 provider catalog 合成为 Policy IR。
  - Policy IR 编译回 Mihomo YAML。
  - analyzer 对缺失 provider、缺失 target、重复规则、group cycle 给出稳定 finding。
  - simulator 对 domain suffix、exact domain、keyword 和 MATCH 给出预期 trace。
- 边界行为：
  - 空订阅、空规则、只有 DIRECT/MATCH 的最小策略。
  - rule 指向内置 target、策略组、缺失策略组时的不同结果。
  - 策略组互相引用形成环。
  - 同一个 provider 被多个模板引用时保持 canonical id。
- 异常行为：
  - 非法订阅 URL、无法解析的 YAML、目标平台不支持。
  - provider catalog 中存在无 URL、重复 URL 或 mirror URL。
- 可复用测试先例：
  - 复用现有 parser、dedup、convert API、singbox 测试风格。
  - 新增 analyzer、simulator、policy IR round-trip 测试。

## 风险与开放问题

- Rule Analyzer 的“死规则/覆盖规则”精度取决于规则 AST 深度；MVP 先做确定性可解释检查，避免假阳性过多。
- RULE-SET 真实内容是否下载会影响 simulator 精度；MVP 可先按 provider 名称和已知 metadata 模拟，再逐步加入缓存下载。
- 前端图规模扩大后 React Flow 是否足够，需要在上千条规则场景下压测；大图可后续切 Cytoscape/ELK。
- 现有项目已经有 sing-box 输出，MVP 定位仍应把 Mihomo 作为唯一保证质量的平台，sing-box 先标记为实验能力。
- 项目命名需要从 `subscription-to-strategy-converter` 演进到更匹配定位的名字，例如 FlowMesh、Proxy Studio、RouteFlow 或 TrafficOS。
