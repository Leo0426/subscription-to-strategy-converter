# 根目录说明

本文件说明仓库根目录各目录和入口文件的职责，便于维护、排查和自动化协作。

| 路径 | 职责 | 是否应提交 |
|---|---|---|
| `app/` | FastAPI 应用、策略领域逻辑、模型和前端静态资源。 | 是 |
| `community_templates/` | 单一 Leo 模板、维护说明和可公开查询的规则源审计快照。 | 是 |
| `docs/` | ADR、Agent 领域上下文和工程决策记录。 | 是 |
| `tests/` | API、编译、策略组装及界面契约测试。 | 是 |
| `data/` | 本地运行时数据目录；默认包含 Profile SQLite 数据库。仅本机使用，不应提交真实订阅或 token。 | 否 |
| `.claude/` | 本地 Agent/工具配置。 | 视内容而定 |
| `.idea/` | IDE 本地配置。 | 否 |
| `.venv/` | `uv` 创建的本地 Python 虚拟环境。 | 否 |
| `.pytest_cache/` | pytest 本地缓存。 | 否 |
| `AGENTS.md` | 面向 Codex 等 Agent 的仓库协作约束和项目背景。 | 是 |
| `CLAUDE.md` | Claude Agent 的仓库协作入口。 | 是 |
| `CONTEXT.md` | 项目领域术语、核心不变量和 ADR 索引。 | 是 |
| `README.md` | 用户入口：能力范围、启动方式和工作台使用说明。 | 是 |
| `pyproject.toml` | Python 项目元数据、依赖和 pytest 配置。 | 是 |
| `uv.lock` | 锁定的 Python 依赖版本。 | 是 |
| `docker-compose.yml` | Docker 本地启动入口。 | 是 |
| `.gitignore` | 本地数据、缓存和 IDE 文件忽略规则。 | 是 |

## 维护边界

- 产品和接口代码优先放在 `app/`；不要把运行时状态写入源码目录。
- 新增策略行为时，同时补充 `tests/` 与相应的 `docs/adr/` 或 `CONTEXT.md`。
- `community_templates/leo/leo.yaml` 是唯一产品模板；更新规则源时必须同步审计、引用完整性和双目标编译验证。
- 真实订阅 URL、Profile token、生成配置和 SQLite 数据只能存在于本地 `data/` 或临时测试过程，不写入仓库文档和测试固件。
