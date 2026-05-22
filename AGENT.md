## Agent skills

### Domain docs
单一上下文：根目录 `CONTEXT.md` + `docs/adr/`。见 `docs/agents/domain.md`。

开始探索前先读 `CONTEXT.md`。它包含领域词汇表、模块地图、模板列表、API 端点和关键不变量。

### Issue tracker
Issues 以 markdown 文件存放在 `.scratch/` 目录下，按功能分目录。见 `docs/agents/issue-tracker.md`。

### Triage labels
使用六个规范 ForgeFlow triage roles，label 字符串与 role 名称相同。见 `docs/agents/triage-labels.md`。

---

## 架构速查

### Workspace loop（核心产品流程）

```
订阅 URL
  → load_subscription()          # app/core/subscription.py
  → subconverter (外部)          # app/core/subconverter.py
  → ProxyNode IR list            # app/ir.py
  → apply_template()             # app/core/template_engine.py
  → config_to_workspace()        # app/core/policy_workspace.py
  → PolicyWorkspace
      ├── build_policy_graph()   # app/core/policy_graph.py
      ├── analyze_workspace()    # app/core/policy_analyzer.py
      └── simulate_destination() # app/core/policy_simulator.py
  → workspace_to_mihomo_config() # app/core/policy_workspace.py
  → render_yaml()                # app/core/renderer.py
  → Mihomo YAML
```

### 关键模块定位

| 要改什么 | 找哪里 |
|---------|--------|
| 添加/修改内置模板 | `app/core/template_engine.py` → `PRESET_TEMPLATES` |
| 修改 proxy 解析或序列化 | `app/core/parsers/clash.py` |
| 修改规则分析逻辑 | `app/core/policy_analyzer.py` |
| 修改流量模拟逻辑 | `app/core/policy_simulator.py` |
| 修改图构建 | `app/core/policy_graph.py` |
| 修改 API 端点 | `app/api/convert.py` |
| 修改 Mihomo 编译输出 | `app/core/policy_workspace.py` → `workspace_to_mihomo_config()` |
| 修改 Surge 编译输出 | `app/core/platforms/surge.py` |
| 修改 sing-box 编译输出 | `app/core/platforms/singbox.py` |
| IR 数据结构变更 | `app/ir.py` — 同步更新 `policy_workspace.py` 的序列化/反序列化 |

### 编码约定

- `ProxyNode` 是唯一的内部 proxy 表示；不允许跨模块边界传递原始 dict
- 模块边界：Source → IR → Graph/Analyzer/Simulator → Compiler，禁止跨层调用
- 实验性编译器（Surge、sing-box）遇到不支持的协议应报错，不能中断 workspace 循环
- 社区模板 ID 统一以 `local:` 前缀标识
- 会话存储（`app/core/sessions.py`）为内存临时存储，重启后失效
