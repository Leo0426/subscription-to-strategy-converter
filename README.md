# Subflow · 代理策略工作区

把机场订阅变成可理解、可编辑、可编译的策略配置。

---

## 能做什么

**傻瓜模式（首页 `/`）** — 粘贴订阅链接，一键获取 Mihomo 订阅 URL，直接导入客户端。

**工作台（`/advanced`）** — 完整的可视化策略编辑器：

- 内置模板 + 108 个社区模板内联浏览
- 规则编排：拖拽增删规则，指定目标策略组
- 规则集目录：296 条，分 8 个分类（AI / 流媒体 / 社交通讯 / 广告拦截 / 国内直连 / 代理规则 / 网络基础 / 其他）
- 规则分析：自动检测缺失 provider、重复规则、策略组环
- 流量模拟：输入域名或 IP，追踪规则命中路径
- 编译导出：Mihomo YAML / Clash YAML / sing-box JSON / Surge conf
- Profile 持久化 + token 保护短链接，客户端直拉，断源自动回退

---

## 快速启动

### Docker（推荐）

```bash
git clone <repo>
docker compose up
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。  
Profile 数据库写入 `./data/subflow.db`（自动创建）。

### 本地开发

```bash
uv sync
uv run uvicorn app.main:app --reload
```

---

## 使用教程

### 傻瓜模式

1. 打开首页，在"机场订阅 URL"框粘贴你的订阅地址
2. 链接自动生成在下方，点击"复制"
3. 把这个链接填入 Mihomo / Clash Verge 的订阅管理

这是无状态的实时转换，节点信息不落库。

---

### 工作台：完整策略编辑

打开 `/advanced`。

**① 加载节点**  
在顶部"订阅 URL"框填入你的订阅地址，点击"测试订阅"确认节点可用，再点"生成配置"。

**② 选择模板**  
在"内置预设"下拉中选择起点：

| 模板 | 说明 |
|------|------|
| `minimal` | 仅节点，无规则，适合测试连通性 |
| `powerfullz` | 完整分流，含 AI / 流媒体 / 广告拦截等策略组 |
| `community:…` | 从社区浏览器搜索并应用 |

社区浏览器直接内联在"内置预设"模块下方，支持关键词搜索，点击预览策略组结构后一键应用。

**③ 编排规则**（可选）  
切换到"规则编排"面板：
- 右侧规则集目录按分类展示所有规则，点击即追加
- 为每条规则指定目标策略组
- "自定义规则"区支持直接输入原始规则行

**④ 验证**

- **规则分析**：列出缺失 provider、重复规则、策略组环等问题
- **流量模拟**：输入 `openai.com` 等域名，查看具体命中哪条规则、解析到哪个策略组

**⑤ 选择目标，导出**

| 格式 | 状态 |
|------|------|
| Mihomo YAML | ✅ 稳定 |
| Clash YAML | ✅ 稳定 |
| sing-box JSON | 🧪 实验性 |
| Surge conf | 🧪 实验性 |

右上角选择目标格式，配置实时出现在"配置预览"区，点击"复制"或"下载"。

---

### Profile：长期托管订阅

1. 在工作台编译好配置后，点击"保存为长期订阅"
2. Subflow 生成一个带 token 的短链接（`/subscribe/<id>?token=…`）
3. 把这个链接填入代理客户端的订阅管理

每次客户端拉取时 Subflow 自动重新编译最新配置。若上游订阅暂时不可用，自动返回最后一次成功产物。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUBFLOW_DB_PATH` | `./data/subflow.db` | Profile 数据库路径 |

---

## 项目结构

```
app/
├── api/          # FastAPI 路由（convert, community, system, health）
├── core/
│   ├── policy_workspace.py   # 策略工作区 IR
│   ├── policy_catalog.py     # 规则集目录（296 条，8 分类）
│   ├── platforms/            # sing-box / Surge 适配器
│   └── template_engine.py    # 模板渲染
├── static/       # 前端（index.html / advanced.html / app.js / main.js）
└── models/       # Pydantic 数据模型
community_templates/          # 社区贡献的配置文件（108 个）
tests/                        # pytest 测试套件
```

---

## 运行测试

```bash
uv run pytest
```
