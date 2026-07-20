<p align="center">
  <img src="app/static/assets/subflow-logo.png" alt="Subflow Logo" width="260" />
</p>

# Subflow · 策略订阅发布器

把一条已授权的机场订阅转换为基于 `community_templates/leo/leo.yaml` 的 Clash / Mihomo 与 Surge 长期订阅。

## 核心能力

- 极简单页：读取订阅、按具体服务选择出口、生成订阅链接。
- 双输入格式：读取 Clash/Mihomo YAML 或 Surge `[Proxy]` 配置，并统一归一化为节点列表。
- 单一基础模板：页面和公开转换接口只接受 `local:community_templates/leo/leo.yaml`。
- 服务级出口：Claude、OpenAI、GitHub、Netflix 等 15 个服务可独立选择 Leo 策略组或真实节点。
- 双客户端发布：同一个 Profile 同时返回 Clash/Mihomo YAML 与 Surge CONF 订阅地址。
- Profile 管理：一个 Profile 保存源订阅、服务出口与各目标最后成功的产物。
- Token 保护：订阅和编辑均需要 Profile token；前端只将 token 保存在创建它的本机浏览器。
- 陈旧产物回退：订阅源或远程规则暂时不可用时，返回最后成功产物并明确标记。

## 当前范围

| 项目 | 当前行为 |
|---|---|
| 上游订阅 | Clash/Mihomo YAML；Surge `[Proxy]` 中的 SS、Trojan、VMess、HTTP(S)、SOCKS5(-TLS) |
| 发布目标 | Clash / Mihomo（完整语义）与 Surge（兼容编译） |
| 默认定制 | 15 个具体服务默认跟随 Leo，仅修改的服务生成独立覆盖规则 |
| Surge 边界 | 支持的节点与规则源正常编译；不支持的协议或 MRS 规则源会跳过并通过 `X-Compile-Warnings` 汇总 |
| 其他目标 | Leo 页面和稳定订阅接口仍拒绝 sing-box |

## 快速启动

### Docker

```bash
docker compose up
```

### 本地开发

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run uvicorn app.main:app --reload
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。`/advanced` 保留为兼容入口，展示同一个统一界面。

## 单页使用方式

创建、编辑和已发布订阅列表共用一个主页，不发生页面跳转。工作台从上到下只有三个主要区域：连接订阅、选择规则、验证发布；已发布订阅列表固定在工作台下方，任何规则卡片都可在连接订阅前选择。

### 连接订阅

粘贴授权的机场订阅 URL。Subflow 先解析并展示节点数量，不会立即创建 Profile。

### 选择规则卡片

Leo 模板中的 21 个策略组、720 个远程规则源和 868 条规则作为固定基础。业务规则卡片可在此基础上追加场景策略，每张卡片包含：

- 目标 Proxy Group 和依赖策略组。
- 规则用途和规则数量。
- 可展开检查的完整域名规则。

五个场景预设作为批量选择快捷方式：

| 预设 | 用途 |
|---|---|
| 通用代理 | 自动选择、故障转移与国内直连 |
| AI / Claude | Claude、OpenAI、Gemini 等 AI 服务独立路由 |
| 流媒体 | Netflix、YouTube、Disney+、Spotify 等服务分流 |
| 开发者 | GitHub、Docker、npm、Microsoft 等开发服务分流 |
| 空白策略 | 仅保留最小可发布图，供专家从头编排 |

选择预设后仍可逐张添加或取消卡片。发布时，所选 RulePack 会组装成完整策略快照；以后 RulePack 或预设升级不会静默改变已有 Profile。

### 可选：展开高级出口定制

默认可直接跳过这一步，所有卡片沿用内置策略链。需要固定地区或协议时，系统提供一个排除“过期、剩余、流量”等噪声节点的“稳定节点”池，也可按地区、协议、包含词和排除词增加节点池。

每条服务路由依次声明：

```text
服务 → 主节点池 → 可选备用节点池 → 最终回退
```

全部 15 张规则卡片均可定制出口。未定制的卡片沿用自身策略链；定制后会覆盖该卡片的目标组成员，但保留具体规则。节点匹配数量与样例会在编辑时实时显示。

### 验证并发布

发布前会基于 Leo 模板构建并检查策略。验证失败不会创建 Profile。成功后返回：

需要直接控制规则顺序或 Rule Provider 时，从“配置检查器 → 专家编排”接管完整策略图。Node Selector 可按节点名称包含/排除正则和协议动态筛选当前订阅节点，并在策略组中通过 `selector:<id>` 引用；同一个 Profile 每次拉取上游订阅时都会重新计算，不依赖易变的节点名称。进入专家模式后，发布以完整 PolicySnapshot 为准，不再叠加默认服务路由编辑器的变更。

```text
/subscribe/<id>?token=…&target=clash
/subscribe/<id>?token=…&target=surge
```

Token 只在创建时返回。统一界面会把它存入当前浏览器的 `localStorage`，用于后续复制链接和授权编辑；Profile 列表 API 不回显 token 或源订阅 URL。

> 清除站点数据或换浏览器后，列表仍可看到脱敏后的 Profile，但只能“基于此新建”。当前没有 token 恢复接口；已保存到客户端的订阅 URL 不受影响。

## Profile 生命周期

| 操作 | 行为 |
|---|---|
| 创建 | 保存源订阅、所选 RulePack、可选 RouteIntent 与完整策略快照，返回一次性 token |
| 编辑 | 仅持有 token 的浏览器可读取草稿；更新后清空旧产物缓存 |
| 客户端拉取 | 实时拉取上游、重新计算 Node Selector，并将同一策略快照编译到对应目标 |
| 外部加载失败 | 订阅源或外部模板加载失败时，返回该目标自己的最后成功产物，并设置 `X-Subflow-Stale: true` |
| 转换或编译失败 | 直接返回错误，不使用缓存掩盖模板、策略或协议不兼容问题 |

## 项目结构

根目录中各目录和入口文件的职责见 [DIRECTORY.md](DIRECTORY.md)。

```text
app/
├── api/                         # FastAPI 路由
├── core/
│   ├── policy_workspace.py      # 策略工作区 IR
│   ├── template_engine.py       # Leo 模板加载与策略应用
│   ├── policy_presets.py        # 产品场景预设与策略快照起点
│   ├── rule_packs.py            # 规则卡片目录与策略组装
│   ├── intent_compiler.py       # 节点池与服务路由意图编译
│   ├── template_policy_transform.py # Claude 模板分析与子图变换
│   ├── profiles.py              # Profile 持久化与目标缓存
│   └── platforms/               # 实验性目标编译器（不进入 Leo 产品接口）
├── static/
│   ├── index.html               # 统一产品入口
│   ├── flow.js                  # 单页策略工作台与 Profile 管理
│   └── flow.css                 # 响应式视觉系统
└── models/
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SUBFLOW_DB_PATH` | `./data/subflow.db` | Profile SQLite 数据库 |

## 测试

```bash
uv run pytest
node --check app/static/flow.js
```
